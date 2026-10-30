"""Runs every image through PanDerm once and saves the features.

Training then works on these saved features, which takes minutes rather than
hours and leaves the PanDerm weights untouched.

Run from the project folder:  python -m backend.skin.training.extract_features
"""
import numpy as np
import pandas as pd
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from backend.skin.encoder import FEATURE_SIZE, PREPROCESS, load_encoder
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


def main() -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    encoder = load_encoder(device)
    names = pd.read_csv(SPLIT_FILE)["image"].tolist()
    loader = DataLoader(
        ImageFolderDataset(names), batch_size=BATCH_SIZE, num_workers=4, pin_memory=True
    )
    features = np.zeros((len(names), FEATURE_SIZE), dtype=np.float32)
    done = 0
    with torch.inference_mode(), torch.autocast(device, enabled=device == "cuda"):
        for batch in loader:
            output = encoder.forward_features(batch.to(device), is_train=False)
            features[done : done + len(batch)] = output.float().cpu().numpy()
            done += len(batch)
            if done % (BATCH_SIZE * 100) == 0:
                print(f"{done}/{len(names)}", flush=True)
    if not np.isfinite(features).all():
        raise SystemExit("Some features are not finite, so they can't be trusted")
    np.save(FEATURE_FILE, features)
    print("Saved", FEATURE_FILE, features.shape)


if __name__ == "__main__":
    main()
