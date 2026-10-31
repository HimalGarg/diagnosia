"""The API for the Prescription Analyzer. Photos are never stored.

Sign-in is required because every request costs money in AI fees.
"""
import sqlite3
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from backend import llm
from backend.auth import require_user
from backend.careflow import events
from backend.db import get_db
from backend.images import MAX_IMAGE_BYTES, read_image
from backend.patient_pack import sync as pack
from backend.prescription import analyzer
from backend.prescription.analyzer import ConfirmedMedicine
from backend.records import care
from backend.records.access import WRITE, open_patient
from backend.records.store import dumps, insert, now_iso
from backend.speech import MAX_SPEECH_CHARS, Language, speak

MAX_PHOTOS = 4
MODULE = "Prescription Analyzer"

router = APIRouter(prefix="/api/prescription")


class ExplainRequest(BaseModel):
    medicines: List[ConfirmedMedicine] = Field(max_length=30)
    language: Language
    doctor_notes: str = Field(default="", max_length=2000)


class SaveRequest(BaseModel):
    medicines: List[ConfirmedMedicine] = Field(min_length=1, max_length=30)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEECH_CHARS)
    language: Language


def _workflow(user):
    return lambda db: events.for_person(db, user, MODULE)


@router.post("/read")
def read(
    images: List[UploadFile] = File(...),
    consent: bool = Form(...),
    user=Depends(require_user),
) -> dict:
    if not consent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Please agree to sending your photo to our AI services first.",
        )
    if len(images) > MAX_PHOTOS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Please upload at most {MAX_PHOTOS} photos."
        )
    photos = [read_image(image.file.read(MAX_IMAGE_BYTES + 1)) for image in images]
    locate = _workflow(user)
    with events.tracked(MODULE, "prescription.processing.started", "prescription.processing.failed", locate, user):
        reading = analyzer.read_prescription(photos)
    count = len(reading["medicines"])
    if reading["is_prescription"] and count:
        events.emit_separately(MODULE, "prescription.extraction.completed", locate, user=user, metadata={"item_count": count}, used_ai=True)
        events.emit_separately(MODULE, "prescription.confirmation_required", locate, "review_required", user, metadata={"item_count": count, "review_required": True})
    return reading


@router.post("/explain")
def explain(request: ExplainRequest, user=Depends(require_user)) -> dict:
    locate = _workflow(user)
    try:
        result = analyzer.explain_prescription(request.medicines, request.language, request.doctor_notes)
    except (llm.AIUnavailable, llm.AIDeclined) as error:
        events.emit_separately(MODULE, "prescription.processing.failed", locate, "failed", user, metadata={"error_category": events.category_of(error)})
        raise
    events.emit_separately(MODULE, "prescription.explained", locate, user=user, metadata={"language": request.language}, used_ai=bool(result["guidance"]))
    return result


@router.post("/save", status_code=status.HTTP_201_CREATED)
def save_to_record(request: SaveRequest, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """Keeps only what the patient ticked, as a patient's own entry. Nothing here is checked by a clinician."""
    record = db.execute("SELECT id FROM patients WHERE user_id = ?", (user["id"],)).fetchone()
    if record is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Link your patient record first, then you can save this to it.")
    open_patient(db, user, record["id"], "prescription.saved", WRITE)
    if any(medicine.name.legibility == "unreadable" or not medicine.name.value.strip() for medicine in request.medicines):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only medicines you can read and have confirmed can be saved.")
    saved_id = insert(
        db,
        "saved_prescriptions",
        patient_id=record["id"],
        medicines=dumps([medicine.model_dump() for medicine in request.medicines]),
        saved_by=user["id"],
        created_at=now_iso(),
    )
    pack.enqueue(db, record["id"], "prescription", saved_id)
    care.add_timeline(db, record["id"], "prescription", "Prescription saved by the patient", f"{len(request.medicines)} medicines", "saved_prescription", saved_id)
    return {"saved": True, "medicines": len(request.medicines)}


@router.post("/speak")
def read_aloud(request: SpeakRequest, _user=Depends(require_user)) -> Response:
    return Response(speak(request.text, request.language), media_type="audio/wav")
