"""Splits ISIC 2019 into train, validation and test sets, by lesion.

Some lesions have several photos. If one lesion appeared in both training and
test, the test score would be too high, so every photo of a lesion stays in
one split.

Run from the project folder:  python -m backend.skin.training.prepare_split
"""
import pandas as pd
from sklearn.model_selection import StratifiedGroupKFold

from backend.config import ROOT_DIR
from backend.skin.conditions import CODES

DATA_DIR = ROOT_DIR / "datasets" / "isic2019"
SPLIT_FILE = DATA_DIR / "split.csv"
SEED = 42


def build_split(truth: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
    table = truth.merge(metadata[["image", "lesion_id"]], on="image", how="left")
    table["label"] = table[CODES].to_numpy().argmax(axis=1)
    # Images with no lesion id count as their own lesion.
    table["group"] = table["lesion_id"].fillna(table["image"])

    # 1 fold in 7 for test, then 1 in 6 of the rest for validation: roughly 71/14/14.
    outer = StratifiedGroupKFold(n_splits=7, shuffle=True, random_state=SEED)
    rest_index, test_index = next(outer.split(table, table["label"], table["group"]))
    rest = table.iloc[rest_index]
    inner = StratifiedGroupKFold(n_splits=6, shuffle=True, random_state=SEED)
    train_index, val_index = next(inner.split(rest, rest["label"], rest["group"]))

    table["split"] = "test"
    table.loc[rest.index[train_index], "split"] = "train"
    table.loc[rest.index[val_index], "split"] = "val"
    return table[["image", "label", "split", "group"]]


def check_no_overlap(split: pd.DataFrame) -> None:
    per_group = split.groupby("group")["split"].nunique()
    if (per_group > 1).any():
        raise SystemExit(f"{(per_group > 1).sum()} lesions appear in more than one split")


def main() -> None:
    truth = pd.read_csv(DATA_DIR / "ISIC_2019_Training_GroundTruth.csv")
    metadata = pd.read_csv(DATA_DIR / "ISIC_2019_Training_Metadata.csv")
    split = build_split(truth, metadata)
    check_no_overlap(split)
    split.to_csv(SPLIT_FILE, index=False)
    print(split.groupby(["split", "label"]).size().unstack(fill_value=0).rename(columns=dict(enumerate(CODES))))
    print("Images:", len(split), "| Lesions:", split["group"].nunique())


if __name__ == "__main__":
    main()
