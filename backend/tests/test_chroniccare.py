import pytest

from backend.chroniccare.routes import MEASURES
from backend.tests.conftest import linked_patient

BASE = "/api/addons/chroniccare"


def plan(doctor, patient_id, **measure):
    created = doctor.post(
        f"{BASE}/patients/{patient_id}/plans",
        json={
            "title": "Blood pressure follow-up",
            "goals": ["Measure at home each week"],
            "measures": [{"measure": "bp_systolic", "target_text": "As agreed", "cadence_days": 7, **measure}],
        },
    )
    assert created.status_code == 201
    return created.json()


def approved_plan(doctor, patient_id, **measure):
    created = plan(doctor, patient_id, **measure)
    assert doctor.post(f"{BASE}/plans/{created['id']}/approve").status_code == 200
    return created


def reading(client, patient_id, value=120, measure="bp_systolic", **fields):
    return client.post(f"{BASE}/patients/{patient_id}/readings", json={"measure": measure, "value": value, **fields})


ALERT = {"alert_high": 160, "escalation_text": "Rest and call the clinic on 040-555-0100 today."}


def test_home_readings_are_labelled_as_patient_entered(doctor, person):
    account, record = linked_patient(doctor, person)
    assert reading(account, record["id"]).json()["source_type"] == "patient_entered"
    saved = doctor.get(f"/api/patients/{record['id']}/observations", params={"category": "vital"}).json()[0]
    assert saved["source_type"] == "patient_entered"
    assert saved["review_status"] == "pending"


def test_clinician_readings_are_labelled_too(doctor, patient):
    assert reading(doctor, patient["id"]).json()["source_type"] == "clinician_entered"


@pytest.mark.parametrize("measure, value", [("oxygen_saturation", 140), ("bp_systolic", 1200), ("weight", 0)])
def test_impossible_readings_are_refused(doctor, patient, measure, value):
    assert reading(doctor, patient["id"], value, measure).status_code == 422


def test_an_alert_limit_needs_escalation_instructions(doctor, patient):
    response = doctor.post(
        f"{BASE}/patients/{patient['id']}/plans",
        json={"title": "Plan", "measures": [{"measure": "pulse", "alert_high": 120}]},
    )
    assert response.status_code == 422


def test_no_alert_comes_from_a_draft_plan(doctor, person):
    account, record = linked_patient(doctor, person)
    plan(doctor, record["id"], **ALERT)
    assert reading(account, record["id"], 180).json()["attention"] is None


def test_no_alert_without_a_plan_however_high_the_value(doctor, person):
    account, record = linked_patient(doctor, person)
    assert reading(account, record["id"], 250).json()["attention"] is None
    assert doctor.get(f"/api/patients/{record['id']}/tasks").json() == []


def test_a_crossed_limit_gives_the_clinicians_words_and_alerts_the_team(doctor, person):
    account, record = linked_patient(doctor, person)
    approved_plan(doctor, record["id"], **ALERT)
    attention = reading(account, record["id"], 180).json()["attention"]
    assert attention["message"] == ALERT["escalation_text"]
    assert "emergency" in attention["reminder"]
    tasks = doctor.get(f"/api/patients/{record['id']}/tasks").json()
    assert tasks[0]["priority"] == "high"
    assert "not clinician-verified" in tasks[0]["detail"]
    assert doctor.get("/api/notifications").json()


def test_repeat_readings_on_one_day_raise_one_task(doctor, person):
    account, record = linked_patient(doctor, person)
    approved_plan(doctor, record["id"], **ALERT)
    reading(account, record["id"], 180)
    reading(account, record["id"], 185)
    assert len(doctor.get(f"/api/patients/{record['id']}/tasks").json()) == 1


def test_only_clinicians_write_care_plans(doctor, person):
    account, record = linked_patient(doctor, person)
    assert account.post(f"{BASE}/patients/{record['id']}/plans", json={"title": "Mine"}).status_code == 403


def test_a_patient_never_sees_a_draft_plan(doctor, person):
    account, record = linked_patient(doctor, person)
    plan(doctor, record["id"])
    assert account.get(f"{BASE}/patients/{record['id']}/dashboard").json()["plans"] == []
    assert len(doctor.get(f"{BASE}/patients/{record['id']}/dashboard").json()["plans"]) == 1


def test_a_new_plan_replaces_the_old_one(doctor, patient):
    first = approved_plan(doctor, patient["id"])
    approved_plan(doctor, patient["id"])
    plans = doctor.get(f"{BASE}/patients/{patient['id']}/dashboard").json()["plans"]
    assert first["id"] not in [item["id"] for item in plans]
    assert len(plans) == 1


def test_dashboard_shows_overdue_measures(doctor, patient):
    approved_plan(doctor, patient["id"])
    measure = doctor.get(f"{BASE}/patients/{patient['id']}/dashboard").json()["measures"][0]
    assert measure["overdue"] is True
    assert measure["latest"] is None


def test_dashboard_shows_a_recent_reading_as_on_time(doctor, patient):
    approved_plan(doctor, patient["id"])
    reading(doctor, patient["id"], 130)
    measure = doctor.get(f"{BASE}/patients/{patient['id']}/dashboard").json()["measures"][0]
    assert measure["overdue"] is False
    assert measure["points"][0]["source_type"] == "clinician_entered"


def test_old_readings_count_as_overdue(doctor, patient):
    approved_plan(doctor, patient["id"])
    reading(doctor, patient["id"], 130, effective_at="2024-01-01T08:00:00+00:00")
    assert doctor.get(f"{BASE}/patients/{patient['id']}/dashboard").json()["measures"][0]["overdue"] is True


def test_adherence_comes_only_from_what_was_reported(doctor, person):
    account, record = linked_patient(doctor, person)
    doctor.post(f"/api/addons/medguard/patients/{record['id']}/medications", json={"written_name": "Telma"})
    url = f"{BASE}/patients/{record['id']}/adherence"
    assert doctor.get(f"{BASE}/patients/{record['id']}/dashboard").json()["adherence"] == []
    account.post(url, json={"medicine_name": "Telma", "event": "taken"})
    account.post(url, json={"medicine_name": "Telma", "event": "missed"})
    summary = doctor.get(f"{BASE}/patients/{record['id']}/dashboard").json()["adherence"][0]
    assert (summary["taken"], summary["missed"]) == (1, 1)


def test_symptoms_are_logged_with_severity(doctor, person):
    account, record = linked_patient(doctor, person)
    response = account.post(f"{BASE}/patients/{record['id']}/symptoms", json={"symptom": "Headache", "severity": 6, "duration": "2 hours"})
    assert response.status_code == 201
    symptom = doctor.get(f"{BASE}/patients/{record['id']}/dashboard").json()["symptoms"][0]
    assert symptom["label"] == "Headache"
    assert symptom["value_number"] == 6
    assert "2 hours" in symptom["note"]


def test_severity_stays_in_range(doctor, patient):
    response = doctor.post(f"{BASE}/patients/{patient['id']}/symptoms", json={"symptom": "Pain", "severity": 11})
    assert response.status_code == 422


def test_strangers_cannot_open_the_dashboard(person, patient):
    assert person("doctor").get(f"{BASE}/patients/{patient['id']}/dashboard").status_code == 404


def test_each_measure_has_a_code_and_unit():
    for measure in MEASURES.values():
        assert measure["loinc"] and measure["unit"]
