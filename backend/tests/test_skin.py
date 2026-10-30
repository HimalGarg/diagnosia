import io

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from backend.app import app
from backend.skin import routes
from backend.skin.classifier import Analysis, choose_priority, rank_conditions
from backend.skin.conditions import CODES, CONCERNING
from backend.skin.training.prepare_split import build_split, check_no_overlap
from backend.skin.training.train_classifier import (
    expected_calibration_error,
    fit_temperature,
    referral_threshold,
)


def image_bytes(size=(128, 128), image_format="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, (150, 90, 80)).save(buffer, format=image_format)
    return buffer.getvalue()


class FakeClassifier:
    def __init__(self):
        self.calls = 0

    def analyze(self, image):
        self.calls += 1
        return Analysis(
            conditions=rank_conditions(np.eye(len(CODES))[0]),
            concern_score=0.9,
            priority="refer",
            heatmap="data:image/png;base64,AA==",
        )


@pytest.fixture
def fake_classifier():
    fake = FakeClassifier()
    app.dependency_overrides[routes.classifier_dependency] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def sign_up(client, details, role):
    client.post("/api/auth/register", json={**details, "role": role})


def upload(client, data=None, image_type="dermoscopic"):
    return client.post(
        "/api/skin/analyze",
        files={"image": ("lesion.png", data if data is not None else image_bytes(), "image/png")},
        data={"image_type": image_type},
    )


def test_signed_out_visitors_are_refused(client, fake_classifier):
    assert upload(client).status_code == 401
    assert fake_classifier.calls == 0


def test_patients_are_refused(client, details, fake_classifier):
    sign_up(client, details, "patient")
    assert upload(client).status_code == 403
    assert fake_classifier.calls == 0


def test_doctors_get_ranked_results(client, details, fake_classifier):
    sign_up(client, details, "doctor")
    response = upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["priority"] == "refer"
    assert body["conditions"][0]["code"] == "MEL"
    assert body["warning"] is None


def test_phone_photos_get_a_warning(client, details, fake_classifier):
    sign_up(client, details, "doctor")
    assert "dermoscopic" in upload(client, image_type="phone").json()["warning"]


@pytest.mark.parametrize(
    "data",
    [b"not an image", image_bytes(size=(20, 20)), image_bytes(image_format="GIF")],
)
def test_bad_uploads_are_refused(client, details, fake_classifier, data):
    sign_up(client, details, "doctor")
    assert upload(client, data=data).status_code == 422
    assert fake_classifier.calls == 0


def test_oversized_uploads_are_refused(client, details, fake_classifier):
    sign_up(client, details, "doctor")
    assert upload(client, data=b"0" * (routes.MAX_IMAGE_BYTES + 1)).status_code == 413


def test_missing_model_gives_a_clear_message(client, details, monkeypatch):
    def missing():
        raise FileNotFoundError

    monkeypatch.setattr(routes, "get_classifier", missing)
    sign_up(client, details, "doctor")
    response = upload(client)
    assert response.status_code == 503
    assert "isn't installed" in response.json()["detail"]


def test_conditions_are_sorted_and_all_present():
    probabilities = np.linspace(0.01, 0.2, len(CODES))
    ranked = rank_conditions(probabilities)
    assert [c.probability for c in ranked] == sorted((c.probability for c in ranked), reverse=True)
    assert {c.code for c in ranked} == set(CODES)


def test_unfamiliar_images_are_always_uncertain():
    assert choose_priority(0.99, 0.2, familiar=False) == "uncertain"
    assert choose_priority(0.99, 0.2, familiar=True) == "refer"
    assert choose_priority(0.10, 0.2, familiar=True) == "monitor"


def test_concerning_conditions_include_every_cancer():
    assert CONCERNING == {"MEL", "BCC", "SCC", "AK"}


def test_no_lesion_appears_in_two_splits():
    rng = np.random.default_rng(0)
    images = [f"ISIC_{i}" for i in range(3000)]
    labels = rng.integers(0, len(CODES), size=len(images))
    truth = pd.DataFrame(np.eye(len(CODES))[labels], columns=CODES)
    truth.insert(0, "image", images)
    # About a third of images share a lesion, and some have no lesion id.
    lesions = [f"lesion_{i // 3}" if i % 7 else None for i in range(len(images))]
    metadata = pd.DataFrame({"image": images, "lesion_id": lesions})

    split = build_split(truth, metadata)
    check_no_overlap(split)
    shares = split["split"].value_counts(normalize=True)
    assert 0.65 < shares["train"] < 0.78
    assert 0.10 < shares["val"] < 0.20
    assert 0.10 < shares["test"] < 0.20


def test_check_no_overlap_catches_leaks():
    leaked = pd.DataFrame({"group": ["a", "a"], "split": ["train", "test"]})
    with pytest.raises(SystemExit):
        check_no_overlap(leaked)


def test_referral_threshold_hits_the_sensitivity_target():
    risk = np.linspace(0, 1, 1000)
    concerning = np.ones_like(risk, dtype=bool)
    threshold = referral_threshold(risk, concerning)
    assert (risk[concerning] >= threshold).mean() == pytest.approx(0.95, abs=0.01)


def test_temperature_scaling_fixes_overconfidence():
    rng = np.random.default_rng(1)
    labels = rng.integers(0, 4, size=4000)
    logits = rng.normal(0, 1, size=(4000, 4))
    logits[np.arange(4000), labels] += 2.0
    overconfident = logits * 4
    temperature = fit_temperature(overconfident, labels)
    assert temperature > 2
    from scipy.special import softmax

    before = expected_calibration_error(softmax(overconfident, axis=1), labels)
    after = expected_calibration_error(softmax(overconfident / temperature, axis=1), labels)
    assert after < before
