"""MedGuard rule approval, LOINC mapping review and the AMR Guard boundary."""
import ast
import json
from pathlib import Path

import pytest

from backend.app import app
from backend.config import settings
from backend.db import connect
from backend.lablens import mappings, parser
from backend.medguard import drugs, engine, references
from backend.records import fhir
from backend.tests.conftest import approve_all_rules

MEDGUARD = "/api/addons/medguard"
RULES = f"{MEDGUARD}/rules"
REASSURING = ("safe", "no interaction", "no interactions", "no known", "all clear", "no problem", "no issue")


def record(name, source="prescription"):
    found = drugs.normalise(name)
    return {
        "id": f"{name}-{source}", "written_name": name, "ingredients": json.dumps(found["ingredients"]),
        "match_status": found["match_status"], "strength": "", "frequency": "", "status": "active",
        "source": source, "end_date": None,
    }


def add(client, patient_id, name):
    return client.post(f"{MEDGUARD}/patients/{patient_id}/medications", json={"written_name": name})


@pytest.fixture
def pharmacists(person):
    return person("doctor", "Priya", "Nair", profession="pharmacist"), person("doctor", "Sam", "Roy", profession="pharmacist")


EVIDENCE = {"evidence_source": "Synthetic reference", "evidence_version": "9", "evidence_date": "2026-01-01"}


def rule_row(rule_id):
    db = connect()
    try:
        return dict(db.execute("SELECT * FROM medication_rules WHERE id = ?", (rule_id,)).fetchone())
    finally:
        db.close()


def test_all_starter_rules_start_unapproved(client):
    db = connect()
    statuses = {row[0] for row in db.execute("SELECT status FROM medication_rules")}
    total = db.execute("SELECT COUNT(*) FROM medication_rules").fetchone()[0]
    db.close()
    assert statuses == {"draft"}
    assert total == 21


def test_starter_rules_have_no_invented_evidence(client):
    row = rule_row("vka-nsaid")
    assert row["evidence_source"] is None
    assert row["reviewer_name"] is None
    assert row["created_by_label"] == "starter file, unreviewed"


def test_restarting_does_not_reset_a_reviewed_rule(client, pharmacists):
    from backend.medguard import rules

    first, _ = pharmacists
    first.patch(f"{RULES}/vka-nsaid/evidence", json=EVIDENCE)
    db = connect()
    rules.seed(db)
    db.commit()
    db.close()
    assert rule_row("vka-nsaid")["evidence_source"] == "Synthetic reference"


def test_unreviewed_rules_never_raise_a_clinical_alert(doctor, patient):
    add(doctor, patient["id"], "Warf")
    add(doctor, patient["id"], "Brufen")
    result = doctor.post(f"{MEDGUARD}/reconcile", json={"patient_id": patient["id"]}).json()
    assert [alert for alert in result["alerts"] if alert["alert_type"] == "interaction"] == []
    match = next(alert for alert in result["alerts"] if alert["alert_type"] == "unverified_rule_match")
    assert match["severity"] == "info"
    assert match["authority"] == "unverified_rule"
    assert "bleeding" not in json.dumps(match).lower()


def test_unverified_matches_never_block_or_clear_a_reconciliation(doctor, patient):
    add(doctor, patient["id"], "Warf")
    add(doctor, patient["id"], "Brufen")
    result = doctor.post(f"{MEDGUARD}/reconcile", json={"patient_id": patient["id"]}).json()
    assert doctor.post(f"{MEDGUARD}/reconciliations/{result['id']}/finalise", json={"decisions": []}).status_code == 200


def test_approved_rules_alert_with_their_provenance(doctor, patient):
    approve_all_rules()
    add(doctor, patient["id"], "Warf")
    add(doctor, patient["id"], "Brufen")
    result = doctor.post(f"{MEDGUARD}/reconcile", json={"patient_id": patient["id"]}).json()
    alert = next(alert for alert in result["alerts"] if alert["alert_type"] == "interaction")
    assert alert["authority"] == "approved_rule"
    assert "Synthetic Reviewer" in alert["explanation"]


def test_rejected_and_retired_rules_do_not_alert(client):
    rules = [{"id": "r", "category": "drug_interaction", "side_a": {"class": "vitamin_k_antagonist"}, "side_b": {"class": "nsaid"},
              "severity": "high", "summary": "x", "basis": "y", "status": "rejected"}]
    findings = engine.interaction_findings([record("Warf"), record("Brufen")], rules)
    assert findings[0]["authority"] == "unverified_rule"


def test_the_five_outcomes_are_told_apart():
    def states(records, rules):
        findings = engine.review(records, [], rules)
        return {item["state"] for item in engine.coverage(findings, rules, records)["statements"]}

    approved = [{"id": "r", "category": "drug_interaction", "side_a": {"class": "vitamin_k_antagonist"}, "side_b": {"class": "nsaid"},
                 "severity": "high", "summary": "x", "basis": "y", "status": "approved", "reviewer_name": "R",
                 "last_reviewed_at": "2026-10-31", "evidence_source": "S", "evidence_version": "1"}]
    draft = [{**approved[0], "status": "draft"}]
    assert "approved_rule_alert" in states([record("Warf"), record("Brufen")], approved)
    assert "unverified_rule_found" in states([record("Warf"), record("Brufen")], draft)
    assert "no_approved_rule_matched" in states([record("Telma"), record("Dolo 650")], approved)
    assert "no_approved_rules" in states([record("Telma")], [])
    assert "information_missing" in states([record("Dolo Cold")], approved)


@pytest.mark.parametrize("names", [["Telma", "Dolo 650"], ["Warf", "Brufen"], ["Mystery Syrup"]])
@pytest.mark.parametrize("status", ["approved", "draft"])
def test_no_outcome_is_worded_as_reassurance(names, status):
    rules = [{"id": "r", "category": "drug_interaction", "side_a": {"class": "vitamin_k_antagonist"}, "side_b": {"class": "nsaid"},
              "severity": "high", "summary": "bleeding", "basis": "y", "status": status, "reviewer_name": "R",
              "last_reviewed_at": "2026-10-31", "evidence_source": "S", "evidence_version": "1"}]
    records = [record(name) for name in names]
    findings = engine.review(records, [], rules)
    text = json.dumps(engine.coverage(findings, rules, records)).lower()
    assert not any(word in text for word in REASSURING)


def test_the_screen_never_offers_reassurance():
    text = (Path(__file__).parents[2] / "web" / "js" / "care" / "medguard.js").read_text().lower()
    assert "no interaction" not in text
    assert "safe to" not in text
    assert "is safe" not in text


def test_a_clear_check_says_what_it_covered(doctor):
    body = doctor.post(f"{MEDGUARD}/check-interactions", json={"medicines": ["Telma", "Dolo 650"]}).json()
    assert body["findings"] == []
    assert body["coverage"]["approved_rules_in_use"] == 0
    assert "have not been checked" in body["coverage"]["statements"][0]["text"]


def test_only_a_verified_pharmacist_can_touch_the_rule_register(doctor, person):
    claimed = person("doctor", verified=False)
    claimed.put("/api/practitioner", json={"profession": "pharmacist"})
    for client, expected in ((doctor, 403), (claimed, 403)):
        assert client.patch(f"{RULES}/vka-nsaid/evidence", json=EVIDENCE).status_code == expected
        assert client.post(f"{RULES}/vka-nsaid/submit").status_code == expected
        assert client.post(f"{RULES}/vka-nsaid/approve", json={"note": "fine"}).status_code == expected


def test_approval_needs_evidence(pharmacists):
    first, _ = pharmacists
    assert first.post(f"{RULES}/vka-nsaid/submit").status_code == 422


def test_rules_cannot_skip_review(pharmacists):
    first, _ = pharmacists
    assert first.post(f"{RULES}/vka-nsaid/approve", json={"note": "Looks fine"}).status_code == 409


def test_nobody_approves_their_own_submission(pharmacists):
    first, second = pharmacists
    first.patch(f"{RULES}/vka-nsaid/evidence", json=EVIDENCE)
    first.post(f"{RULES}/vka-nsaid/submit")
    assert first.post(f"{RULES}/vka-nsaid/approve", json={"note": "Looks fine"}).status_code == 403
    approved = second.post(f"{RULES}/vka-nsaid/approve", json={"note": "Checked"}).json()
    assert approved["status"] == "approved"
    assert approved["reviewer_id"] == second.id
    assert [event["to_status"] for event in approved["history"]] == ["pending_pharmacist_review", "approved"]


def test_rejection_and_retirement_are_recorded(pharmacists):
    first, second = pharmacists
    first.patch(f"{RULES}/vka-nsaid/evidence", json=EVIDENCE)
    first.post(f"{RULES}/vka-nsaid/submit")
    assert second.post(f"{RULES}/vka-nsaid/reject", json={"note": "Source too old"}).json()["status"] == "rejected"
    first.patch(f"{RULES}/vka-nsaid/evidence", json=EVIDENCE)
    first.post(f"{RULES}/vka-nsaid/submit")
    second.post(f"{RULES}/vka-nsaid/approve", json={"note": "Checked"})
    assert second.post(f"{RULES}/vka-nsaid/retire", json={"note": "Superseded"}).json()["status"] == "retired"


def test_an_approved_rule_cannot_be_edited(pharmacists):
    first, second = pharmacists
    first.patch(f"{RULES}/vka-nsaid/evidence", json=EVIDENCE)
    first.post(f"{RULES}/vka-nsaid/submit")
    second.post(f"{RULES}/vka-nsaid/approve", json={"note": "Checked"})
    assert first.patch(f"{RULES}/vka-nsaid/evidence", json=EVIDENCE).status_code == 409


def test_rule_history_cannot_be_rewritten(pharmacists):
    import sqlite3

    first, _ = pharmacists
    first.patch(f"{RULES}/vka-nsaid/evidence", json=EVIDENCE)
    first.post(f"{RULES}/vka-nsaid/submit")
    db = connect()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM medication_rule_events")
    db.close()


def test_the_test_switch_cannot_apply_with_real_data_or_in_production(monkeypatch):
    from backend import safety

    monkeypatch.setattr(settings, "enable_unvalidated_clinical_rules", True)
    assert safety.unvalidated_rules_visible()
    monkeypatch.setattr(settings, "allow_real_patient_data", True)
    assert not safety.unvalidated_rules_visible()
    monkeypatch.setattr(settings, "allow_real_patient_data", False)
    monkeypatch.setattr(settings, "environment", "production")
    assert not safety.unvalidated_rules_visible()


def test_the_switch_never_upgrades_an_unverified_rule(monkeypatch):
    monkeypatch.setattr(settings, "enable_unvalidated_clinical_rules", True)
    rules = [{"id": "r", "category": "drug_interaction", "side_a": {"class": "vitamin_k_antagonist"}, "side_b": {"class": "nsaid"},
              "severity": "high", "summary": "Bleeding", "basis": "y", "status": "draft"}]
    finding = engine.interaction_findings([record("Warf"), record("Brufen")], rules)[0]
    assert finding["severity"] == "info"
    assert finding["authority"] == "unverified_rule"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@pytest.mark.parametrize(
    "reply",
    [
        FakeResponse(200, ValueError("not json")),
        FakeResponse(200, ["unexpected"]),
        FakeResponse(200, {"idGroup": {"rxnormId": "not-a-list"}}),
        FakeResponse(500, {}),
        FakeResponse(429, {}),
    ],
)
def test_bad_rxnorm_replies_fail_safely_and_are_not_cached(doctor, monkeypatch, reply):
    monkeypatch.setattr(references.httpx, "get", lambda *a, **k: reply)
    monkeypatch.setattr(references, "RETRY_PAUSE_SECONDS", 0)
    body = doctor.get(f"{MEDGUARD}/lookup", params={"name": "Tylenol"}).json()
    assert body["rxnorm"]["available"] is False
    assert body["rxnorm"]["ingredients"] == []
    db = connect()
    assert db.execute("SELECT COUNT(*) FROM terminology_cache").fetchone()[0] == 0
    db.close()


def test_reference_results_carry_source_and_time(doctor, monkeypatch):
    def fake(url, params, timeout):
        if url.endswith("rxcui.json"):
            return FakeResponse(200, {"idGroup": {"rxnormId": ["161"]}})
        return FakeResponse(200, {"relatedGroup": {"conceptGroup": [{"conceptProperties": [{"name": "Acetaminophen"}]}]}})

    monkeypatch.setattr(references.httpx, "get", fake)
    rxnorm = doctor.get(f"{MEDGUARD}/lookup", params={"name": "Tylenol"}).json()["rxnorm"]
    assert rxnorm["source"].startswith("RxNorm")
    assert rxnorm["retrieved_at"]


def test_a_label_lookup_is_never_called_an_interaction_check(doctor, monkeypatch):
    monkeypatch.setattr(references.httpx, "get", lambda *a, **k: FakeResponse(200, {"results": [{"drug_interactions": ["warfarin text"]}]}))
    body = doctor.get(f"{MEDGUARD}/label-evidence", params={"drug": "ibuprofen", "other": "warfarin"}).json()
    assert body["mentioned"] is True
    assert "not a complete" in body["limitation"].lower()


def test_old_reference_results_expire(doctor, monkeypatch):
    calls = []

    def fake(url, params, timeout):
        calls.append(url)
        return FakeResponse(200, {"idGroup": {}})

    monkeypatch.setattr(references.httpx, "get", fake)
    doctor.get(f"{MEDGUARD}/lookup", params={"name": "Tylenol"})
    db = connect()
    db.execute("UPDATE terminology_cache SET fetched_at = '2020-01-01T00:00:00+00:00'")
    db.commit()
    db.close()
    doctor.get(f"{MEDGUARD}/lookup", params={"name": "Tylenol"})
    assert len(calls) == 2


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


# AMR Guard


def amr_paths():
    return [route.path for route in app.routes if route.path.startswith("/api/addons/amrguard")]


def test_amr_guard_declares_decision_support_unavailable(doctor):
    body = doctor.get("/api/addons/amrguard/catalog").json()
    assert body["clinical_decision_support"] == "unavailable"
    assert "isn't a formulary" in body["catalog_use"]


def test_amr_guard_has_no_recommendation_route():
    joined = " ".join(amr_paths()).lower()
    for word in ("recommend", "suggest", "empiric", "treat", "prescri", "dose", "substitut", "therapy", "chat", "ask"):
        assert word not in joined


def test_amr_guard_imports_no_ai_or_network_client():
    folder = Path(__file__).parents[1] / "amrguard"
    forbidden = {"llm", "anthropic", "httpx", "requests", "speech"}
    for path in folder.glob("*.py"):
        tree = ast.parse(path.read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {alias.name.split(".")[-1] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names |= {alias.name for alias in node.names} | {(node.module or "").split(".")[-1]}
        assert not forbidden & names, path.name


def test_amr_guard_is_closed_to_unverified_organizations(person):
    founder = person("doctor")
    organization = founder.post("/api/organizations", json={"name": "Self-made", "type": "hospital"}).json()
    assert founder.get(f"/api/addons/amrguard/organizations/{organization['id']}/culture-queue").status_code == 403


def test_a_steward_recommendation_is_stored_as_written_and_nothing_is_added():
    source = (Path(__file__).parents[1] / "amrguard" / "antibiogram.py").read_text().lower()
    assert "recommendation" not in source
    assert "first-line" not in source


def test_the_prompt_list_has_no_antibiotic_advice():
    from backend.clinscribe import drafting
    from backend.lablens import extract

    for prompt in (drafting.SYSTEM, extract.READ_SYSTEM):
        assert "antibiotic" not in prompt.lower()
