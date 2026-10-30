"""The API for the Prescription Analyzer. Photos are never stored.

Sign-in is required because every request costs money in AI fees.
"""
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from backend.auth import require_user
from backend.images import MAX_IMAGE_BYTES, read_image
from backend.prescription import analyzer
from backend.prescription.analyzer import ConfirmedMedicine
from backend.speech import MAX_SPEECH_CHARS, Language, speak

router = APIRouter(prefix="/api/prescription")


class ExplainRequest(BaseModel):
    medicines: List[ConfirmedMedicine] = Field(max_length=30)
    language: Language


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEECH_CHARS)
    language: Language


@router.post("/read")
def read(
    image: UploadFile = File(...),
    consent: bool = Form(...),
    _user=Depends(require_user),
) -> dict:
    if not consent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Please agree to sending your photo to our AI services first.",
        )
    return analyzer.read_prescription(read_image(image.file.read(MAX_IMAGE_BYTES + 1)))


@router.post("/explain")
def explain(request: ExplainRequest, _user=Depends(require_user)) -> dict:
    return analyzer.explain_prescription(request.medicines, request.language)


@router.post("/speak")
def read_aloud(request: SpeakRequest, _user=Depends(require_user)) -> Response:
    return Response(speak(request.text, request.language), media_type="audio/wav")
