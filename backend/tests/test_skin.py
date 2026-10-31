import io

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from backend.app import app
from backend.skin import routes
from backend.skin.classifier import Analysis, choose_priority, occlusion_map, rank_conditions
from backend.skin.conditions import CODES, CONCERNING
from backend.skin.training.prepare_split import build_split, check_no_overlap
from backend.skin.training.train_classifier import (
    expected_calibration_error,
    familiarity,
    fit_precision,
    fit_temperature,
    referral_threshold,
)
from backend.tests.conftest import verify_email


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
    if role == "doctor":
        verify_email(details["email"])


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


def test_occlusion_finds_the_part_of_the_image_the_score_depends_on():
    torch = pytest.importorskip("torch")
    batch = torch.ones(1, 3, 224, 224)

    def score(images):
        # Only the patch at row 3, column 9 matters.
        return images[:, :, 48:64, 144:160].mean(dim=(1, 2, 3))

    cam = occlusion_map(batch, score)
    row, column = np.unravel_index(cam.argmax(), cam.shape)
    assert (row, column) == (3, 9)
    assert cam[12, 1] == 0
    assert cam.max() == pytest.approx(1)


def test_a_coarser_occlusion_still_covers_the_whole_image():
    torch = pytest.importorskip("torch")
    seen = []

    def score(images):
        seen.append(len(images))
        return torch.zeros(len(images))

    cam = occlusion_map(torch.ones(1, 3, 224, 224), score, stride=2)
    assert sum(seen) == 1 + 7 * 7  # the original image, then seven positions along each side
    assert cam.shape == (14, 14)


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


def test_familiarity_spots_images_unlike_the_training_data():
    rng = np.random.default_rng(2)
    training = rng.normal(0, 1, size=(5000, 32))
    # Correlated features, like real ones: this point is ordinary feature by
    # feature, but its combination of values is unlike anything in training.
    training[:, 1] = training[:, 0] + rng.normal(0, 0.05, size=5000)
    mean = training.mean(axis=0)
    precision = fit_precision(training)
    scores = familiarity(training, mean, precision)
    floor = np.percentile(scores, 1)

    unlike_training = rng.normal(0, 1, size=(500, 32))  # feature 1 no longer follows feature 0
    # Some random points match the pattern by chance, so this can't reach 100%.
    assert (familiarity(unlike_training, mean, precision) < floor).mean() > 0.75
    assert (scores < floor).mean() == pytest.approx(0.01, abs=0.002)


def test_temperature_scaling_fixes_overconfidence():
    rng = np.random.default_rng(1)
    labels = rng.integers(0, 4, size=4000)
    logits = rng.normal(0, 1, size=(4000, 4))
    logits[np.arange(4000), labels] += 2.0
    overconfident = logits * 4
    temperature = fit_temperature(overconfident, labels)
    assert temperature > 1.5
    from scipy.special import softmax

    before = expected_calibration_error(softmax(overconfident, axis=1), labels)
    after = expected_calibration_error(softmax(overconfident / temperature, axis=1), labels)
    assert after < before


def test_a_resumed_download_keeps_whole_chunks_and_covers_the_rest():
    from backend.skin.training import download_isic

    chunk = download_isic.CHUNK_BYTES
    assert download_isic.chunks_for(chunk * 3, chunk + 5) == [(chunk, chunk * 2 - 1), (chunk * 2, chunk * 3 - 1)]
    assert download_isic.chunks_for(chunk * 2 - 10, 0)[-1] == (chunk, chunk * 2 - 11)


def test_the_integrity_check_matches_how_s3_builds_a_multipart_etag(tmp_path):
    import hashlib

    from backend.skin.training import download_isic

    path = tmp_path / "file.bin"
    path.write_bytes(b"a" * 10 + b"b" * 5)
    parts = hashlib.md5(b"a" * 10).digest() + hashlib.md5(b"b" * 5).digest()
    assert download_isic.etag_of(path, 10) == f"{hashlib.md5(parts).hexdigest()}-2"


def test_a_tie_on_validation_goes_to_the_simpler_model():
    from backend.skin.training.train_classifier import choose_strength

    picked = choose_strength([(0.5724, 0.001, "a"), (0.5852, 0.01, "b"), (0.5881, 0.1, "c"), (0.5649, 1.0, "d")])
    assert picked[1] == 0.01


def test_a_clear_winner_on_validation_still_wins():
    from backend.skin.training.train_classifier import choose_strength

    assert choose_strength([(0.50, 0.01, "a"), (0.60, 0.1, "b")])[1] == 0.1
