"""LOINC mapping review."""
import pytest

from backend.db import connect
from backend.lablens import mappings, parser
from backend.records import fhir


# LOINC


@pytest.fixture
def lab_reviewers(person):
    return person("doctor", "Lata", "Menon", profession="laboratory_professional"), person("doctor", "Dev", "Rao", profession="laboratory_professional")


@pytest.mark.parametrize("code, valid", [("718-7", True), ("718-8", False), ("2160-0", True), ("abc", False), ("718", False), ("7-18-7", False)])
def test_the_check_digit_rule(code, valid):
    assert mappings.check_digit_valid(code) is valid


def test_every_starter_code_passes_the_check_digit():
    failing = [analyte["loinc"] for analyte in parser.catalog()["analytes"] if not mappings.check_digit_valid(analyte["loinc"])]
    assert failing == []


def test_all_mappings_start_unreviewed(client):
    db = connect()
    assert {row[0] for row in db.execute("SELECT status FROM loinc_mappings")} == {"proposed"}
    assert db.execute("SELECT COUNT(*) FROM loinc_mappings WHERE source_version IS NOT NULL").fetchone()[0] == 0
    db.close()


def observation(code="718-7"):
    return {
        "id": "o1", "patient_id": "p1", "review_status": "accepted", "category": "laboratory", "label": "Haemoglobin",
        "code": code, "effective_at": "2026-01-01", "value_number": 13.0, "value_text": "13.0", "unit": "g/dL", "reference_text": "",
    }


def test_an_unreviewed_code_is_left_out_of_exports():
    resource = fhir.observation_resource(observation(), set())
    assert "coding" not in resource["code"]
    assert resource["code"]["text"] == "Haemoglobin"


def test_an_approved_code_is_exported():
    resource = fhir.observation_resource(observation(), {"718-7"})
    assert resource["code"]["coding"][0]["code"] == "718-7"


def test_approval_changes_the_export(lab_reviewers):
    first, _ = lab_reviewers
    response = first.post("/api/addons/lablens/mappings/hemoglobin/approve", json={"source_version": "2.80"})
    assert response.status_code == 200
    db = connect()
    assert "718-7" in fhir.approved_loinc_codes(db)
    db.close()


def test_only_a_verified_laboratory_professional_can_review_mappings(doctor, person):
    for client in (doctor, person("doctor", profession="pharmacist")):
        assert client.post("/api/addons/lablens/mappings/hemoglobin/approve", json={"source_version": "2.80"}).status_code == 403


def test_a_correction_goes_back_for_someone_else_to_approve(lab_reviewers):
    first, second = lab_reviewers
    url = "/api/addons/lablens/mappings/hemoglobin"
    assert first.post(f"{url}/correct", json={"code": "718-8", "source_version": "2.80", "note": "Typo"}).status_code == 422
    corrected = first.post(f"{url}/correct", json={"code": "2160-0", "source_version": "2.80", "note": "Synthetic correction"}).json()
    assert corrected["status"] == "proposed"
    assert first.post(f"{url}/approve", json={"source_version": "2.80"}).status_code == 403
    assert second.post(f"{url}/approve", json={"source_version": "2.80"}).json()["status"] == "approved"


def test_a_rejected_mapping_is_not_exported(lab_reviewers):
    first, second = lab_reviewers
    url = "/api/addons/lablens/mappings/hemoglobin"
    first.post(f"{url}/approve", json={"source_version": "2.80"})
    second.post(f"{url}/reject", json={"note": "Wrong property"})
    db = connect()
    assert "718-7" not in fhir.approved_loinc_codes(db)
    db.close()


def test_unknown_tests_get_no_guessed_code():
    assert parser.parse_results("Lipoprotein Little A 22 mg/dL 0 - 30")[0]["loinc_code"] is None
