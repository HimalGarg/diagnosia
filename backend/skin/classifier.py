"""Analyzes a lesion image. The rest of the app only uses analyze().

To replace PanDerm before charging for this tool, provide another object with
the same analyze() method and return it from get_classifier().
"""
import base64
import io
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Callable, List, Optional, Protocol

import numpy as np
from PIL import Image

from backend.config import ROOT_DIR
from backend.skin.conditions import CONCERNING, CONDITIONS

WEIGHTS_FILE = ROOT_DIR / "backend" / "skin" / "weights" / "classifier.npz"
HEATMAP_OPACITY = 0.6
HEATMAP_SIZE = 224
GRID = 14  # the model divides each image into 14 x 14 patches of 16 pixels
PATCH = 16
HIDE_BLOCK = 2  # patches hidden at a time, along each side
GROUP_SIZE = 32  # fits the 6 GB RTX 4050 with mixed precision


@dataclass
class Condition:
    code: str
    name: str
    kind: str
    probability: float


@dataclass
class Analysis:
    conditions: List[Condition]
    concern_score: float
    priority: str  # refer, monitor or uncertain
    heatmap: Optional[str]  # a PNG data URI, or None when the image is unfamiliar

    def to_dict(self) -> dict:
        return asdict(self)


class SkinClassifier(Protocol):
    def analyze(self, image: Image.Image) -> Analysis: ...


def choose_priority(concern_score: float, threshold: float, familiar: bool) -> str:
    # An image unlike anything in training has no meaningful score, however
    # confident the numbers look.
    if not familiar:
        return "uncertain"
    return "refer" if concern_score >= threshold else "monitor"


def rank_conditions(probabilities: np.ndarray) -> List[Condition]:
    conditions = [
        Condition(code, name, kind, round(float(probability), 4))
        for (code, name, kind), probability in zip(CONDITIONS, probabilities)
    ]
    return sorted(conditions, key=lambda condition: condition.probability, reverse=True)


def occlusion_map(batch, score: Callable, stride: int = 1) -> np.ndarray:
    """Hides each part of the image in turn and measures how far the score falls.

    This shows what the model really relies on. Grad-CAM and attention maps were
    tried first, and they showed almost no agreement with this direct measure on
    30 test images, so they would have misled doctors.
    """
    positions = [
        (row, column)
        for row in range(0, GRID - HIDE_BLOCK + 1, stride)
        for column in range(0, GRID - HIDE_BLOCK + 1, stride)
    ]
    base = float(score(batch)[0])
    drops = np.zeros((GRID, GRID), dtype=np.float32)
    covered = np.zeros((GRID, GRID), dtype=np.float32)
    for start in range(0, len(positions), GROUP_SIZE):
        group = positions[start : start + GROUP_SIZE]
        hidden = batch.repeat(len(group), 1, 1, 1)
        for index, (row, column) in enumerate(group):
            hidden[
                index, :, row * PATCH : (row + HIDE_BLOCK) * PATCH, column * PATCH : (column + HIDE_BLOCK) * PATCH
            ] = 0
        for (row, column), value in zip(group, score(hidden).tolist()):
            drops[row : row + HIDE_BLOCK, column : column + HIDE_BLOCK] += base - value
            covered[row : row + HIDE_BLOCK, column : column + HIDE_BLOCK] += 1
    cam = np.maximum(drops / np.maximum(covered, 1), 0)
    return cam / max(float(cam.max()), 1e-8)


def overlay_heatmap(image: Image.Image, cam: np.ndarray) -> str:
    """Tints in red the parts of the image that raised the concern score."""
    base = np.asarray(image.convert("RGB").resize((HEATMAP_SIZE, HEATMAP_SIZE)), dtype=np.float32)
    scaled = Image.fromarray((cam * 255).astype(np.uint8)).resize((HEATMAP_SIZE, HEATMAP_SIZE), Image.BILINEAR)
    heat = np.asarray(scaled, dtype=np.float32)[..., None] / 255 * HEATMAP_OPACITY
    blended = base * (1 - heat) + np.array([255, 0, 0], dtype=np.float32) * heat
    buffer = io.BytesIO()
    Image.fromarray(blended.astype(np.uint8)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class PanDermClassifier:
    """PanDerm encoder plus the classifier from training/train_classifier.py."""

    def __init__(self, device: Optional[str] = None):
        import torch

        from backend.skin.encoder import best_device, load_encoder

        self.torch = torch
        self.device = device or best_device()
        self.encoder = load_encoder(self.device)
        saved = np.load(WEIGHTS_FILE)
        tensor = lambda name: torch.tensor(saved[name], dtype=torch.float32, device=self.device)  # noqa: E731
        self.mean, self.std = tensor("mean"), tensor("std")
        self.coef, self.intercept = tensor("coef"), tensor("intercept")
        self.precision = tensor("precision")
        self.temperature = float(saved["temperature"])
        self.threshold = float(saved["threshold"])
        self.familiar_floor = float(saved["familiar_floor"])
        self.concerning = [i for i, entry in enumerate(CONDITIONS) if entry[0] in CONCERNING]

    def _features(self, batch):
        with self.torch.autocast(self.device, enabled=self.device == "cuda"):
            return self.encoder.forward_features(batch, is_train=False).float()

    def _probabilities(self, features):
        scaled = (features - self.mean) / self.std
        return self.torch.softmax((scaled @ self.coef.T + self.intercept) / self.temperature, dim=1)

    def _concern(self, batch):
        return self._probabilities(self._features(batch))[:, self.concerning].sum(dim=1)

    def analyze(self, image: Image.Image) -> Analysis:
        from backend.skin.encoder import CROP, PREPROCESS

        photo = image.convert("RGB")
        batch = PREPROCESS(photo).unsqueeze(0).to(self.device)
        with self.torch.inference_mode():
            features = self._features(batch)
            probabilities = self._probabilities(features)[0]
            concern_score = float(probabilities[self.concerning].sum())
            difference = features[0] - self.mean
            familiar = float(-(difference @ self.precision @ difference)) >= self.familiar_floor

            heatmap = None
            if familiar:
                # Each hidden position costs one pass, so a CPU checks fewer of them.
                stride = 1 if self.device == "cuda" else HIDE_BLOCK
                cam = occlusion_map(batch, self._concern, stride)
                # The model only sees the center crop, so that's what gets tinted.
                heatmap = overlay_heatmap(CROP(photo), cam)

        return Analysis(
            # Numbers for an unfamiliar image mean nothing, so none are returned.
            conditions=rank_conditions(probabilities.cpu().numpy()) if familiar else [],
            concern_score=round(concern_score, 4) if familiar else 0.0,
            priority=choose_priority(concern_score, self.threshold, familiar),
            heatmap=heatmap,
        )


@lru_cache(maxsize=1)
def get_classifier() -> SkinClassifier:
    return PanDermClassifier()
