"""Runs every image through PanDerm once and saves the features.

Training then works on these saved features, which takes minutes rather than
hours and leaves the PanDerm weights untouched.

Run from the project folder:  python -m backend.skin.training.extract_features
"""
import time

import numpy as np
import pandas as pd
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from backend.skin.encoder import FEATURE_SIZE, PREPROCESS, best_device, load_encoder
from backend.skin.training.prepare_split import DATA_DIR, SPLIT_FILE

IMAGE_DIR = DATA_DIR / "ISIC_2019_Training_Input"
FEATURE_FILE = DATA_DIR / "features.npy"
BATCH_SIZE = 32  # fits the 6 GB RTX 4050 with mixed precision


class ImageFolderDataset(Dataset):
    def __init__(self, names):
        self.names = names

    def __len__(self):
        return len(self.names)

    def __getitem__(self, index):
        with Image.open(IMAGE_DIR / f"{self.names[index]}.jpg") as image:
            return PREPROCESS(image.convert("RGB"))


PARTIAL_FILE = DATA_DIR / "features.partial.npy"
SAVE_EVERY = 50  # batches, so an interrupted run loses minutes, not hours


def main() -> None:
    device = best_device()
    print("Device:", device, flush=True)
    encoder = load_encoder(device)
    names = pd.read_csv(SPLIT_FILE)["image"].tolist()
    features = np.zeros((len(names), FEATURE_SIZE), dtype=np.float32)
    done = 0
    marker = PARTIAL_FILE.with_suffix(".done")
    if PARTIAL_FILE.exists() and marker.exists():
        features, done = np.load(PARTIAL_FILE), int(marker.read_text())
        print(f"Resuming after {done} images", flush=True)
    loader = DataLoader(
        ImageFolderDataset(names[done:]), batch_size=BATCH_SIZE, num_workers=4, pin_memory=device == "cuda"
    )
    started = time.time()
    # Full precision off CUDA, so the features match what the app computes when it analyzes an image.
    with torch.inference_mode(), torch.autocast(device, enabled=device == "cuda"):
        for number, batch in enumerate(loader, start=1):
            output = encoder.forward_features(batch.to(device), is_train=False)
            features[done : done + len(batch)] = output.float().cpu().numpy()
            done += len(batch)
            if number % 10 == 0:
                print(f"{done}/{len(names)}  {number * BATCH_SIZE / (time.time() - started):.1f} images/s", flush=True)
            if number % SAVE_EVERY == 0:
                np.save(PARTIAL_FILE, features)
                marker.write_text(str(done))
    if not np.isfinite(features).all():
        raise SystemExit("Some features are not finite, so they can't be trusted")
    np.save(FEATURE_FILE, features)
    PARTIAL_FILE.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    print("Saved", FEATURE_FILE, features.shape)


if __name__ == "__main__":
    main()
