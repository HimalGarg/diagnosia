"""Checks uploaded images before any tool uses them."""
import io

from fastapi import HTTPException, status
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MIN_IMAGE_SIDE = 64


def read_image(data: bytes) -> Image.Image:
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Images must be under 10 MB.")
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in ("JPEG", "PNG"):
            raise UnidentifiedImageError
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Please upload a JPG or PNG image."
        ) from None
    if min(image.size) < MIN_IMAGE_SIDE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This image is too small to analyze.")
    return image
