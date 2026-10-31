"""The API for the Prescription Analyzer. Photos are never stored.

Sign-in is required because every request costs money in AI fees.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from backend import llm
from backend.auth import require_user
from backend.careflow import events
from backend.images import MAX_IMAGE_BYTES, read_image
from backend.prescription import analyzer
from backend.prescription.analyzer import ConfirmedMedicine
from backend.speech import MAX_SPEECH_CHARS, Language, speak

MAX_PHOTOS = 4
MODULE = "Prescription Analyzer"

router = APIRouter(prefix="/api/prescription")


class ExplainRequest(BaseModel):
    medicines: List[ConfirmedMedicine] = Field(max_length=30)
    language: Language
    doctor_notes: str = Field(default="", max_length=2000)


class CalendarRequest(BaseModel):
    medicines: List[ConfirmedMedicine] = Field(max_length=30)
    language: Language
    guidance: Optional[dict] = None
    start_date: Optional[str] = None
    times: Optional[dict] = None


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


@router.post("/calendar")
def calendar(request: CalendarRequest, _user=Depends(require_user)) -> dict:
    return analyzer.build_calendar_reminders(
        request.medicines,
        request.language,
        request.guidance,
        request.start_date,
        request.times,
    )


@router.post("/speak")
def read_aloud(request: SpeakRequest, _user=Depends(require_user)) -> Response:
    return Response(speak(request.text, request.language), media_type="audio/wav")

