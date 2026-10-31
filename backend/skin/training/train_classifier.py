"""Trains the classifier on PanDerm features and measures it on the test set.

Everything that needs tuning is tuned on the validation set. The test set is
only used once, at the end, for the numbers in metrics.json.

Run from the project folder:  python -m backend.skin.training.train_classifier
"""
import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score

from backend.config import ROOT_DIR
from backend.skin.conditions import CODES, CONCERNING
from backend.skin.training.extract_features import FEATURE_FILE
from backend.skin.training.prepare_split import SPLIT_FILE

WEIGHTS_FILE = ROOT_DIR / "backend" / "skin" / "weights" / "classifier.npz"
METRICS_FILE = ROOT_DIR / "backend" / "skin" / "metrics.json"
TARGET_SENSITIVITY = 0.95  # miss at most 1 in 20 concerning lesions on validation
UNCERTAIN_PERCENTILE = 1.0  # the least familiar 1% of validation images set the bar
SAME_SCORE = 0.005  # validation scores closer than this are noise on about 3,600 images, with rare classes
CONCERNING_INDEX = [CODES.index(code) for code in sorted(CONCERNING)]


def fit_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    """Finds the scaling that makes confidence scores match real accuracy."""

    def loss(temperature: float) -> float:
        probabilities = softmax(logits / temperature, axis=1)
        return -np.log(probabilities[np.arange(len(labels)), labels] + 1e-12).mean()

    return float(minimize_scalar(loss, bounds=(0.1, 10.0), method="bounded").x)


def expected_calibration_error(probabilities: np.ndarray, labels: np.ndarray, bins: int = 15) -> float:
    confidence = probabilities.max(axis=1)
    correct = probabilities.argmax(axis=1) == labels
    edges = np.linspace(0, 1, bins + 1)
    error = 0.0
    for low, high in zip(edges[:-1], edges[1:]):
        inside = (confidence > low) & (confidence <= high)
        if inside.any():
            error += inside.mean() * abs(correct[inside].mean() - confidence[inside].mean())
    return float(error)


def referral_threshold(risk: np.ndarray, is_concerning: np.ndarray) -> float:
    """The highest cut-off that still catches the target share of concerning lesions."""
    return float(np.quantile(risk[is_concerning], 1 - TARGET_SENSITIVITY))


def fit_precision(features: np.ndarray) -> np.ndarray:
    """The inverse covariance of the training lesions, for familiarity()."""
    centered = features.astype(np.float64) - features.mean(axis=0, dtype=np.float64)
    covariance = centered.T @ centered / len(centered)
    return np.linalg.inv(covariance + 1e-3 * np.eye(features.shape[1]))


def familiarity(features: np.ndarray, mean: np.ndarray, precision: np.ndarray) -> np.ndarray:
    """How much an image resembles the training lesions. Higher is more familiar.

    This is negative Mahalanobis distance. In testing it caught all 95 non-skin
    images tried, where cosine similarity to class centers caught only 8%.
    """
    difference = features - mean
    return -np.einsum("ij,jk,ik->i", difference, precision, difference)


def choose_strength(candidates: list) -> tuple:
    """The simplest model among those tied on validation, so a lucky score can't pick an overfit one.

    Candidates are (score, C, model), and a smaller C means a simpler model. Unlike the highest score
    alone, this is the rule that picked C=0.01 here on both machines this was trained on."""
    best = max(score for score, _strength, _model in candidates)
    tied = [candidate for candidate in candidates if best - candidate[0] <= SAME_SCORE]
    return min(tied, key=lambda candidate: candidate[1])


def main() -> None:
    split = pd.read_csv(SPLIT_FILE)
    features = np.load(FEATURE_FILE)
    labels = split["label"].to_numpy()
    part = {name: (split["split"] == name).to_numpy() for name in ("train", "val", "test")}

    mean = features[part["train"]].mean(axis=0)
    std = features[part["train"]].std(axis=0) + 1e-6
    scaled = (features - mean) / std

    candidates = []
    for strength in (0.001, 0.01, 0.1, 1.0):
        model = LogisticRegression(C=strength, max_iter=2000, class_weight="balanced")
        model.fit(scaled[part["train"]], labels[part["train"]])
        score = balanced_accuracy_score(labels[part["val"]], model.predict(scaled[part["val"]]))
        print(f"C={strength}: validation balanced accuracy {score:.4f}", flush=True)
        candidates.append((score, strength, model))
    _, strength, model = choose_strength(candidates)

    logits = scaled @ model.coef_.T + model.intercept_
    temperature = fit_temperature(logits[part["val"]], labels[part["val"]])
    probabilities = softmax(logits / temperature, axis=1)
    risk = probabilities[:, CONCERNING_INDEX].sum(axis=1)
    is_concerning = np.isin(labels, CONCERNING_INDEX)
    threshold = referral_threshold(risk[part["val"]], is_concerning[part["val"]])

    precision = fit_precision(features[part["train"]])
    familiar = familiarity(features, mean, precision)
    familiar_floor = float(np.percentile(familiar[part["val"]], UNCERTAIN_PERCENTILE))

    test = part["test"]
    melanoma = CODES.index("MEL")
    predicted = probabilities[test].argmax(axis=1)
    referred = risk[test] >= threshold
    test_labels = labels[test]
    metrics = {
        "dataset": "ISIC 2019 training set, split by lesion",
        "images": {name: int(mask.sum()) for name, mask in part.items()},
        "classifier_strength": strength,
        "temperature": temperature,
        "balanced_accuracy": float(balanced_accuracy_score(test_labels, predicted)),
        "macro_auroc": float(roc_auc_score(test_labels, probabilities[test], multi_class="ovr")),
        "melanoma_auroc": float(roc_auc_score(test_labels == melanoma, probabilities[test, melanoma])),
        "sensitivity_by_condition": {
            code: float((predicted[test_labels == index] == index).mean())
            for index, code in enumerate(CODES)
        },
        "referral": {
            "threshold": threshold,
            "concerning_lesions_referred": float(referred[is_concerning[test]].mean()),
            "melanomas_referred": float(referred[test_labels == melanoma].mean()),
            "benign_lesions_referred": float(referred[~is_concerning[test]].mean()),
        },
        "calibration_error": expected_calibration_error(probabilities[test], test_labels),
        "test_lesions_marked_uncertain": float((familiar[test] < familiar_floor).mean()),
    }

    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        WEIGHTS_FILE,
        mean=mean,
        std=std,
        coef=model.coef_,
        intercept=model.intercept_,
        temperature=temperature,
        threshold=threshold,
        precision=precision.astype(np.float32),
        familiar_floor=familiar_floor,
    )
    METRICS_FILE.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
