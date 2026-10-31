"""LabLens against synthetic Indian report layouts. See docs/EXTERNAL_INTEGRATION_VALIDATION.md."""
import pytest

from backend.lablens import extract, parser
from backend.tests.lab_validation import score
from backend.tests.lab_validation.formats import cases
from backend.tests.test_lablens import extraction


@pytest.fixture(scope="module")
def measured():
    return score.run()


def test_no_value_is_ever_read_wrongly(measured):
    assert measured["wrong_values"] == []


def test_nothing_is_lost_without_a_flag(measured):
    assert measured["silent_misses"] == []


@pytest.mark.parametrize("field, minimum", [("found", 1.0), ("value", 1.0), ("unit", 0.9), ("low", 0.9), ("high", 0.9)])
def test_field_accuracy_stays_up(measured, field, minimum):
    item = measured["fields"][field]
    assert item["hits"] / item["total"] >= minimum, item


@pytest.mark.parametrize("field", ["patient", "collected"])
def test_header_accuracy_stays_up(measured, field):
    item = measured["header"][field]
    assert item["hits"] / item["total"] >= 0.8, item


def test_a_line_with_two_tests_goes_to_a_person():
    results = parser.parse_results("Hemoglobin 13.2 g/dL 13.0 - 17.0        Creatinine 1.1 mg/dL 0.7 - 1.3")
    assert [result["standard_name"] for result in results] == ["Hemoglobin", "Creatinine"]
    assert all(result["confidence"] == "review" for result in results)


def test_a_name_inside_another_name_does_not_split_a_line():
    assert len(parser.parse_results("Total Cholesterol 180 mg/dL < 200")) == 1
    assert len(parser.parse_results("HDL Cholesterol 41 mg/dL > 40")) == 1


@pytest.mark.parametrize("name", ["scanned_clean", "scanned_poor"])
def test_scans_are_not_read_locally(name):
    case = next(item for item in cases() if item.name == name)
    assert len(extract.native_text(case.pdf).strip()) < extract.MIN_TEXT_CHARS


@pytest.mark.parametrize("case", [item for item in cases() if item.readable_locally], ids=lambda item: item.name)
def test_extracted_values_never_become_verified_on_their_own(doctor, patient, case):
    report = doctor.post(
        "/api/addons/lablens/reports",
        data={"patient_id": patient["id"]},
        files={"file": ("r.pdf", case.pdf, "application/pdf")},
    ).json()
    result = extraction(doctor, report["id"])
    assert result["status"] == "needs_review"
    assert doctor.get(f"/api/patients/{patient['id']}/observations").json() == []
