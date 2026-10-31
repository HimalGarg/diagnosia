"""The doctor-only API for Skin Cancer Detection. Images are never stored."""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from backend.auth import require_doctor
from backend.images import MAX_IMAGE_BYTES, read_image
from backend.skin.classifier import SkinClassifier, get_classifier

PHONE_PHOTO_WARNING = (
    "This model was trained on dermoscopic images, so results for phone photos "
    "are less reliable."
)

router = APIRouter(prefix="/api/skin")


def classifier_dependency() -> SkinClassifier:
    try:
        return get_classifier()
    except (FileNotFoundError, OSError, ImportError):  # weights missing, or torch not installed
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The skin model isn't installed yet."
        ) from None


@router.post("/analyze")
def analyze(
    image: UploadFile = File(...),
    image_type: Literal["dermoscopic", "phone"] = Form(...),
    _doctor=Depends(require_doctor),
    classifier: SkinClassifier = Depends(classifier_dependency),
) -> dict:
    analysis = classifier.analyze(read_image(image.file.read(MAX_IMAGE_BYTES + 1)))
    result = analysis.to_dict()
    result["warning"] = PHONE_PHOTO_WARNING if image_type == "phone" else None
    return result
