"""The doctor-only API for Skin Cancer Detection. Images are never stored."""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from backend.auth import require_doctor
from backend.careflow import events
from backend.images import MAX_IMAGE_BYTES, read_image
from backend.skin.classifier import SkinClassifier, get_classifier

PHONE_PHOTO_WARNING = (
    "This model was trained on dermoscopic images, so results for phone photos "
    "are less reliable."
)

router = APIRouter(prefix="/api/skin")
MODULE = "Skin Cancer Detection"


def _workflow(user):
    return lambda db: events.for_person(db, user, MODULE)


def classifier_dependency(doctor=Depends(require_doctor)) -> SkinClassifier:
    try:
        return get_classifier()
    except (FileNotFoundError, OSError, ImportError):  # weights missing, or torch not installed
        events.emit_separately(MODULE, "skin_analysis.failed", _workflow(doctor), "failed", doctor, metadata={"error_category": "model_not_installed"})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The skin model isn't installed yet."
        ) from None


@router.post("/analyze")
def analyze(
    image: UploadFile = File(...),
    image_type: Literal["dermoscopic", "phone"] = Form(...),
    doctor=Depends(require_doctor),
    classifier: SkinClassifier = Depends(classifier_dependency),
) -> dict:
    photo = read_image(image.file.read(MAX_IMAGE_BYTES + 1))
    locate = _workflow(doctor)
    with events.tracked(MODULE, "skin_analysis.started", "skin_analysis.failed", locate, doctor):
        analysis = classifier.analyze(photo)
    # The finding itself is never recorded: only that a result exists and a doctor must review it.
    events.emit_separately(MODULE, "skin_analysis.completed", locate, user=doctor, metadata={"model_provider": "local"})
    events.emit_separately(MODULE, "skin_analysis.review_required", locate, "review_required", doctor, metadata={"review_required": True})
    result = analysis.to_dict()
    result["warning"] = PHONE_PHOTO_WARNING if image_type == "phone" else None
    return result
