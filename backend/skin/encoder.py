"""Loads the PanDerm image encoder.

PanDerm's code and weights are CC-BY-NC-ND 4.0, for non-commercial academic
research only, so neither is kept in this repository. Both live in the
git-ignored datasets/ folder.
"""
import importlib.util
from pathlib import Path

import torch
from torchvision import transforms

from backend.config import ROOT_DIR

PANDERM_DIR = ROOT_DIR / "datasets" / "PanDerm" / "classification"
ENCODER_WEIGHTS = ROOT_DIR / "backend" / "skin" / "weights" / "panderm_ll_data6_checkpoint-499.pth"
FEATURE_SIZE = 1024

# The same preprocessing PanDerm uses for evaluation (models/builder.py).
PREPROCESS = transforms.Compose(
    [
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.228, 0.224, 0.225)),
    ]
)


def _load_panderm_module():
    # Loaded by file path, because importing PanDerm's models package pulls in
    # its whole research stack (open_clip and more) that the app doesn't need.
    path = PANDERM_DIR / "models" / "modeling_finetune.py"
    spec = importlib.util.spec_from_file_location("panderm_modeling", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_encoder(device: str, weights: Path = ENCODER_WEIGHTS) -> torch.nn.Module:
    model = _load_panderm_module().panderm_large_patch16_224()
    state = torch.load(weights, map_location="cpu", weights_only=True)
    state = {key.replace("encoder.", ""): value for key, value in state.items()}
    result = model.load_state_dict(state, strict=False)
    # PanDerm loads with strict=False, which would quietly keep random weights
    # if the file didn't match. Refuse to run an untrained encoder.
    missing = [key for key in result.missing_keys if key.startswith(("blocks.", "patch_embed."))]
    if missing:
        raise RuntimeError(f"Encoder weights don't fit the model: {missing[:3]}")
    return model.eval().to(device)
