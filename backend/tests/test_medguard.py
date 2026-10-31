import json
from datetime import date

import httpx
import pytest

from backend.db import connect
from backend.medguard import drugs, engine, references
from backend.tests.conftest import approve_all_rules, linked_patient


def record(name, source="prescription", strength="", frequency="", status="active", end_date=None, record_id=None):
    found = drugs.normalise(name)
    return {
        "id": record_id or f"{name}-{source}",
        "written_name": name,
        "ingredients": json.dumps(found["ingredients"]),
        "match_status": found["match_status"],
        "strength": strength,
        "frequency": frequency,
        "status": status,
        "source": source,
        "end_date": end_date,
    }


def starter_rules(status="approved"):
    """The starter rules marked as approved, only to test what the engine does with approval."""
    data = drugs.rules()
    rules = [
        {
            "id": rule["id"], "category": "drug_interaction", "side_a": rule["a"], "side_b": rule["b"],
            "severity": rule["severity"], "summary": rule["summary"], "basis": rule["basis"], "status": status,
            "reviewer_name": "Synthetic Reviewer", "last_reviewed_at": "2026-10-31",
            "evidence_source": "Synthetic source", "evidence_version": "1",
        }
        for rule in data["rules"]
    ]
    rules += [
        {
            "id": cross["id"], "category": "allergy_cross_reaction", "side_a": {"allergy_class": cross["allergy_class"]},
            "side_b": {"drug_class": cross["drug_class"]}, "severity": "moderate", "summary": cross["summary"],
            "basis": cross["summary"], "status": status,
        }
        for cross in data["allergy_cross_reactions"]
    ]
    return rules


def allergy(substance, reaction="", severity="unknown"):
    return {"id": substance, "substance": substance, "reaction": reaction, "severity": severity, "status": "active"}


def kinds(findings):
    return [finding["alert_type"] for finding in findings]


@pytest.mark.parametrize(
    "written, ingredients",
    [
        ("Dolo 650", ["paracetamol"]),
        ("Tab. Dolo 650 mg", ["paracetamol"]),
        ("CROCIN", ["paracetamol"]),
        ("acetaminophen 500mg", ["paracetamol"]),
        ("Combiflam", ["ibuprofen", "paracetamol"]),
        ("Augmentin 625", ["amoxicillin", "clavulanic acid"]),
        ("Taxim-O 200", ["cefixime"]),
        ("Amoxicillin + Clavulanic acid", ["amoxicillin", "clavulanic acid"]),
        ("Cap. Omez 20", ["omeprazole"]),
    ],
)
def test_medicines_match_their_ingredients(written, ingredients):
    found = drugs.normalise(written)
    assert found["match_status"] == "matched"
    assert sorted(found["ingredients"]) == sorted(ingredients)


@pytest.mark.parametrize("written", ["Dolo Cold", "Augmentin Duo", "Telma AM", "Telma H", "Glycomet GP2", "Metrogyl ER"])
def test_similar_products_with_other_ingredients_are_never_auto_matched(written):
    found = drugs.normalise(written)
    assert found["match_status"] == "unmatched"
    assert found["ingredients"] == []
    assert found["suggestions"]


def test_a_misspelling_is_suggested_not_accepted():
    found = drugs.normalise("Paracetmol")
    assert found["match_status"] == "unmatched"
    assert found["suggestions"][0]["name"] == "paracetamol"


def test_nonsense_stays_unmatched():
    assert drugs.normalise("Xyzzy Forte")["suggestions"] == []


def test_every_rule_refers_to_something_in_the_catalog():
    catalog = drugs.catalog()
    classes = {name for details in catalog["ingredients"].values() for name in details["classes"]}
    for rule in drugs.rules()["rules"]:
        for side in (rule["a"], rule["b"]):
            if "class" in side:
                assert side["class"] in classes, rule["id"]
            else:
                assert side["ingredient"] in catalog["ingredients"], rule["id"]


def test_every_brand_points_to_a_known_ingredient():
    catalog = drugs.catalog()
    for brand, ingredients in catalog["brands"].items():
        assert set(ingredients) <= set(catalog["ingredients"]), brand


def test_the_knowledge_base_says_it_has_not_been_reviewed():
    assert drugs.rules()["review_status"] == "unreviewed"
    assert drugs.catalog()["review_status"] == "unreviewed"


def test_every_rule_kind_is_a_finding_with_authority():
    finding = engine.discrepancy_findings([record("Dolo 650"), record("Combiflam")])[0]
    assert finding["authority"] == "list_comparison"


def test_combination_products_reveal_a_duplicate_ingredient():
    findings = engine.discrepancy_findings([record("Dolo 650"), record("Combiflam")])
    assert "duplicate_ingredient" in kinds(findings)


def test_two_nsaids_are_flagged_as_the_same_type():
    findings = engine.discrepancy_findings([record("Brufen"), record("Voveran")])
    assert "same_class" in kinds(findings)


def test_the_same_medicine_in_two_lists_is_not_a_duplicate():
    findings = engine.discrepancy_findings([record("Dolo 650"), record("Crocin", source="patient_reported")])
    assert "duplicate_ingredient" not in kinds(findings)


def test_shorthand_and_words_for_the_same_schedule_agree():
    assert engine.times_a_day("1-0-1") == engine.times_a_day("BD") == 2
    assert engine.times_a_day("1-1-1") == engine.times_a_day("TDS") == 3
    assert engine.times_a_day("SOS") is None
    findings = engine.discrepancy_findings(
        [record("Metformin", frequency="1-0-1"), record("Glycomet", source="patient_reported", frequency="BD")]
    )
    assert "frequency_conflict" not in kinds(findings)


def test_a_different_schedule_is_flagged():
    findings = engine.discrepancy_findings(
        [record("Metformin", frequency="1-0-1"), record("Glycomet", source="patient_reported", frequency="OD")]
    )
    assert "frequency_conflict" in kinds(findings)


def test_a_different_strength_is_flagged():
    findings = engine.discrepancy_findings(
        [record("Metformin", strength="500 mg"), record("Glycomet", source="patient_reported", strength="1000mg")]
    )
    assert "strength_conflict" in kinds(findings)


def test_strength_written_differently_is_the_same():
    findings = engine.discrepancy_findings(
        [record("Metformin", strength="500 mg"), record("Glycomet", source="patient_reported", strength="500MG")]
    )
    assert "strength_conflict" not in kinds(findings)


def test_a_medicine_missing_from_a_source_is_flagged():
    findings = engine.omission_findings([record("Metformin"), record("Telma", source="patient_reported")])
    assert kinds(findings) == ["omission", "omission"]


def test_a_single_source_cannot_have_omissions():
    assert engine.omission_findings([record("Metformin"), record("Telma")]) == []


def test_interaction_found_through_a_brand_and_class():
    findings = engine.interaction_findings([record("Warf"), record("Combiflam")], starter_rules())
    assert findings[0]["severity"] == "high"
    assert "potential interaction" in findings[0]["explanation"].lower()
    assert "Synthetic Reviewer" in findings[0]["explanation"]
    assert findings[0]["authority"] == "approved_rule"


def test_each_alert_names_its_rule_and_evidence():
    finding = engine.interaction_findings([record("Warf"), record("Brufen")], starter_rules())[0]
    assert finding["rule_id"] == "vka-nsaid"
    assert {item["written_name"] for item in finding["evidence"]} == {"Warf", "Brufen"}


def test_order_does_not_change_the_result():
    first = engine.interaction_findings([record("Warf"), record("Brufen")], starter_rules())
    second = engine.interaction_findings([record("Brufen"), record("Warf")], starter_rules())
    assert first[0]["fingerprint"] == second[0]["fingerprint"]


def test_unmatched_medicines_are_reported_as_unchecked():
    findings = engine.review([record("Mystery Syrup")], [])
    assert kinds(findings) == ["unmatched_drug"]
    assert "No interaction" in findings[0]["explanation"]


def test_a_direct_allergy_match():
    findings = engine.allergy_findings([record("Augmentin")], [allergy("Penicillin", "Rash", "severe")])
    assert findings[0]["alert_type"] == "allergy"
    assert "Rash" in findings[0]["explanation"]


def test_a_named_drug_allergy_covers_the_brand():
    assert kinds(engine.allergy_findings([record("Brufen")], [allergy("ibuprofen")])) == ["allergy"]


def test_cross_reaction_is_marked_as_only_possible():
    findings = engine.allergy_findings([record("Taxim-O")], [allergy("Penicillin")], starter_rules())
    assert findings[0]["alert_type"] == "allergy_possible"
    assert findings[0]["severity"] == "moderate"


def test_refuted_allergies_are_ignored():
    entry = {**allergy("Penicillin"), "status": "refuted"}
    assert engine.allergy_findings([record("Augmentin")], [entry]) == []


def test_past_end_date_is_flagged():
    findings = engine.stale_findings([record("Azithral", end_date="2020-01-05")], today=date(2026, 1, 1))
    assert kinds(findings) == ["stale_entry"]


def test_engine_output_never_tells_anyone_to_change_a_medicine():
    records = [record("Warf"), record("Brufen"), record("Dolo 650"), record("Combiflam"), record("Mystery")]
    text = json.dumps(engine.review(records, [allergy("Penicillin")], starter_rules())).lower()
    for phrase in ("stop taking", "discontinue", "switch to", "should stop", "increase the dose", "reduce the dose"):
        assert phrase not in text


def add(client, patient_id, name, **fields):
    return client.post(f"/api/addons/medguard/patients/{patient_id}/medications", json={"written_name": name, **fields})


def reconcile(doctor, patient_id):
    return doctor.post("/api/addons/medguard/reconcile", json={"patient_id": patient_id})


def test_sources_stay_separate(doctor, person):
    account, record_ = linked_patient(doctor, person)
    assert add(account, record_["id"], "Dolo 650").json()["source"] == "patient_reported"
    assert add(doctor, record_["id"], "Dolo 650").json()["source"] == "clinician_entered"
    assert len(doctor.get(f"/api/patients/{record_['id']}/medications").json()) == 2


def test_patients_cannot_run_a_reconciliation(doctor, person):
    account, record_ = linked_patient(doctor, person)
    add(account, record_["id"], "Dolo 650")
    assert account.post("/api/addons/medguard/reconcile", json={"patient_id": record_["id"]}).status_code == 403


def test_prescription_import_keeps_unreadable_medicines_out(doctor, patient):
    readable = {"value": "Dolo 650", "legibility": "unsure"}
    clear = {"value": "1-0-1", "legibility": "clear"}
    empty = {"value": "", "legibility": "clear"}
    blank = {"value": "", "legibility": "unreadable"}
    response = doctor.post(
        f"/api/addons/medguard/patients/{patient['id']}/import-prescription",
        json={
            "medicines": [
                {"name": readable, "dose": empty, "frequency": clear, "duration": empty, "notes": empty},
                {"name": blank, "dose": empty, "frequency": clear, "duration": empty, "notes": empty},
            ]
        },
    ).json()
    assert len(response["imported"]) == 1
    assert response["skipped"] == ["A medicine we couldn't read"]
    saved = doctor.get(f"/api/patients/{patient['id']}/medications").json()[0]
    assert saved["source_type"] == "ai_extracted"
    assert saved["review_status"] == "pending"


def test_full_reconciliation(doctor, patient):
    approve_all_rules()
    pid = patient["id"]
    doctor.post(f"/api/patients/{pid}/allergies", json={"substance": "Penicillin"})
    warf = add(doctor, pid, "Warf", source="prescription").json()
    brufen = add(doctor, pid, "Brufen", source="patient_reported").json()
    result = reconcile(doctor, pid).json()
    assert result["status"] == "draft"
    assert result["knowledge"]["drug_list_review_status"] == "unreviewed"
    high = [alert for alert in result["alerts"] if alert["severity"] == "high"]
    assert high

    finalise = f"/api/addons/medguard/reconciliations/{result['id']}/finalise"
    decisions = {"decisions": [{"medication_id": warf["id"], "decision": "continue"}, {"medication_id": brufen["id"], "decision": "stop", "note": "Patient advised"}]}
    assert doctor.post(finalise, json=decisions).json()["detail"]["code"] == "alerts_open"

    for alert in high:
        doctor.post(f"/api/addons/medguard/alerts/{alert['id']}/review", json={"disposition": "action_planned", "reason": "Discussed"})
    done = doctor.post(finalise, json=decisions).json()
    assert done["status"] == "finalised"
    assert len(done["summary"]["confirmed"]) == 2

    medicines = doctor.get(f"/api/patients/{pid}/medications").json()
    reconciled = {row["written_name"]: row for row in medicines if row["source"] == "reconciled"}
    assert reconciled["Brufen"]["status"] == "stopped"
    # The originals are untouched: the decision is recorded next to them.
    original = next(row for row in medicines if row["id"] == brufen["id"])
    assert original["status"] == "active"
    assert original["source"] == "patient_reported"
    assert doctor.post(finalise, json=decisions).status_code == 409


def test_undecided_medicines_are_listed_as_unresolved(doctor, patient):
    add(doctor, patient["id"], "Telma")
    result = reconcile(doctor, patient["id"]).json()
    done = doctor.post(f"/api/addons/medguard/reconciliations/{result['id']}/finalise", json={"decisions": []}).json()
    assert done["summary"]["unresolved"][0]["name"] == "Telma"


def test_rerunning_keeps_reviews_already_recorded(doctor, patient):
    approve_all_rules()
    add(doctor, patient["id"], "Warf")
    add(doctor, patient["id"], "Brufen")
    first = reconcile(doctor, patient["id"]).json()
    alert = next(item for item in first["alerts"] if item["alert_type"] == "interaction")
    doctor.post(f"/api/addons/medguard/alerts/{alert['id']}/review", json={"disposition": "acknowledged", "reason": "Known"})
    second = reconcile(doctor, patient["id"]).json()
    assert second["id"] == first["id"]
    kept = [item for item in second["alerts"] if item["fingerprint"] == alert["fingerprint"]]
    assert len(kept) == 1
    assert kept[0]["disposition"] == "acknowledged"


def test_identifying_a_medicine_keeps_the_original_and_enables_checks(doctor, patient):
    medicine = add(doctor, patient["id"], "Mystery Syrup").json()
    assert medicine["match_status"] == "unmatched"
    fixed = doctor.patch(f"/api/addons/medguard/medications/{medicine['id']}", json={"ingredients": ["ibuprofen"], "reason": "Checked the strip"}).json()
    assert fixed["match_status"] == "matched"
    assert fixed["review_status"] == "corrected"
    assert "Checked the strip" in fixed["original_value"]


def test_quick_check_flags_what_it_cannot_identify(doctor):
    approve_all_rules()
    response = doctor.post("/api/addons/medguard/check-interactions", json={"medicines": ["Warf", "Brufen", "Dolo Cold"]}).json()
    assert {finding["alert_type"] for finding in response["findings"]} == {"interaction", "unmatched_drug"}


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body


def test_rxnorm_outage_does_not_break_the_tool(doctor, monkeypatch):
    def down(*args, **kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(references.httpx, "get", down)
    body = doctor.get("/api/addons/medguard/lookup", params={"name": "Dolo 650"}).json()
    assert body["rxnorm"]["available"] is False
    assert body["match_status"] == "matched"


def test_rxnorm_lookups_send_only_the_drug_name_and_are_cached(doctor, monkeypatch):
    calls = []

    def fake(url, params, timeout):
        calls.append(params)
        if url.endswith("rxcui.json"):
            return FakeResponse(200, {"idGroup": {"rxnormId": ["161"]}})
        return FakeResponse(200, {"relatedGroup": {"conceptGroup": [{"conceptProperties": [{"name": "Acetaminophen"}]}]}})

    monkeypatch.setattr(references.httpx, "get", fake)
    first = doctor.get("/api/addons/medguard/lookup", params={"name": "Tylenol"}).json()
    count = len(calls)
    doctor.get("/api/addons/medguard/lookup", params={"name": "Tylenol"})
    assert first["rxnorm"]["ingredients"] == ["Acetaminophen"]
    assert len(calls) == count
    assert calls[0]["name"] == "tylenol"


def test_label_silence_is_not_presented_as_safety(doctor, monkeypatch):
    monkeypatch.setattr(references.httpx, "get", lambda *a, **k: FakeResponse(404, {}))
    body = doctor.get("/api/addons/medguard/label-evidence", params={"drug": "warfarin", "other": "ibuprofen"}).json()
    assert body["mentioned"] is False
    assert "safe" not in json.dumps(body).lower()


def test_reconciliation_is_on_the_timeline_only_when_final(doctor, patient):
    add(doctor, patient["id"], "Telma")
    result = reconcile(doctor, patient["id"]).json()
    assert doctor.get(f"/api/patients/{patient['id']}/timeline").json() == []
    doctor.post(f"/api/addons/medguard/reconciliations/{result['id']}/finalise", json={"decisions": []})
    assert doctor.get(f"/api/patients/{patient['id']}/timeline").json()[0]["module"] == "medguard"


def test_only_one_open_reconciliation_per_patient(doctor, patient):
    add(doctor, patient["id"], "Telma")
    assert reconcile(doctor, patient["id"]).json()["id"] == reconcile(doctor, patient["id"]).json()["id"]
    db = connect()
    assert db.execute("SELECT COUNT(*) FROM medication_reconciliations").fetchone()[0] == 1
    db.close()
