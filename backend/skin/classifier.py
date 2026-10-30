"""Analyzes a lesion image. The rest of the app only uses analyze().

To replace PanDerm before charging for this tool, provide another object with
the same analyze() method and return it from get_classifier().
"""
import base64
import io
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import List, Optional, Protocol

import numpy as np
from PIL import Image

from backend.config import ROOT_DIR
from backend.skin.conditions import CONCERNING, CONDITIONS

WEIGHTS_FILE = ROOT_DIR / "backend" / "skin" / "weights" / "classifier.npz"
HEATMAP_OPACITY = 0.45


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


def overlay_heatmap(image: Image.Image, cam: np.ndarray) -> str:
    """Tints the image from blue (ignored) to red (drove the result)."""
    size = 224
    base = image.convert("RGB").resize((size, size))
    scaled = Image.fromarray((cam * 255).astype(np.uint8)).resize((size, size), Image.BILINEAR)
    heat = np.asarray(scaled, dtype=np.float32) / 255
    colors = np.stack([heat, np.zeros_like(heat) + heat * (1 - heat) * 2, 1 - heat], axis=-1)
    blended = (1 - HEATMAP_OPACITY) * np.asarray(base, dtype=np.float32) + HEATMAP_OPACITY * colors * 255
    buffer = io.BytesIO()
    Image.fromarray(blended.astype(np.uint8)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class PanDermClassifier:
    """PanDerm encoder plus the classifier from training/train_classifier.py."""

    def __init__(self, device: Optional[str] = None):
        import torch

        from backend.skin.encoder import load_encoder

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.encoder = load_encoder(self.device)
        saved = np.load(WEIGHTS_FILE)
        tensor = lambda name: torch.tensor(saved[name], dtype=torch.float32, device=self.device)  # noqa: E731
        self.mean, self.std = tensor("mean"), tensor("std")
        self.coef, self.intercept = tensor("coef"), tensor("intercept")
        self.centroids = tensor("centroids")
        self.temperature = float(saved["temperature"])
        self.threshold = float(saved["threshold"])
        self.familiar_floor = float(saved["familiar_floor"])

    def analyze(self, image: Image.Image) -> Analysis:
        from backend.skin.encoder import PREPROCESS

        torch = self.torch
        batch = PREPROCESS(image.convert("RGB")).unsqueeze(0).to(self.device)

        # The model's output is the class token after the last block, so patches
        # leaving that block have no effect on it. Grad-CAM must look at what
        # goes into the last block, where patches still feed the class token.
        captured = {}

        def capture(_module, inputs):
            inputs[0].retain_grad()
            captured["tokens"] = inputs[0]

        hook = self.encoder.blocks[-1].register_forward_pre_hook(capture)
        try:
            with torch.enable_grad():
                batch.requires_grad_(True)
                features = self.encoder.forward_features(batch, is_train=False)
                scaled = (features - self.mean) / self.std
                logits = scaled @ self.coef.T + self.intercept
                probabilities = torch.softmax(logits / self.temperature, dim=1)[0]
                concerning = [i for i, entry in enumerate(CONDITIONS) if entry[0] in CONCERNING]
                concern_score = probabilities[concerning].sum()
                top = int(probabilities.argmax())
                # Explain the referral score when that's what matters.
                target = concern_score if concern_score >= self.threshold else logits[0, top]
                self.encoder.zero_grad(set_to_none=True)
                target.backward()
        finally:
            hook.remove()

        unit = scaled[0] / scaled[0].norm()
        familiar = float((unit @ self.centroids.T).max()) >= self.familiar_floor
        priority = choose_priority(float(concern_score), self.threshold, familiar)

        heatmap = None
        if familiar:
            tokens = captured["tokens"]
            weights = tokens.grad[:, 1:].mean(dim=1, keepdim=True)
            side = int((tokens.shape[1] - 1) ** 0.5)
            cam = torch.relu((weights * tokens[:, 1:]).sum(dim=-1)).reshape(side, side)
            cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)
            heatmap = overlay_heatmap(image, cam.detach().float().cpu().numpy())

        return Analysis(
            conditions=rank_conditions(probabilities.detach().cpu().numpy()),
            concern_score=round(float(concern_score), 4),
            priority=priority,
            heatmap=heatmap,
        )


@lru_cache(maxsize=1)
def get_classifier() -> SkinClassifier:
    return PanDermClassifier()
