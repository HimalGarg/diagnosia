import ast
from datetime import date, timedelta
from pathlib import Path

import pytest

import backend.maternacare.routes as maternacare
from backend.tests.conftest import grant, linked_patient

BASE = "/api/addons/maternacare"


def start(doctor, patient_id, **fields):
    return doctor.post(
        f"{BASE}/episodes",
        json={
            "patient_id": patient_id,
            "facility": "Community Health Centre",
            "escalation_contact": "Labour room: 040-555-0100",
            "concern_response_hours": 2,
            **fields,
        },
    )


@pytest.fixture
def care(doctor, person):
    account, record = linked_patient(doctor, person, "Lakshmi", "Nair")
    grant(doctor, record["id"], "maternal_care")
    episode = start(doctor, record["id"]).json()
    return account, record, episode


def test_care_starts_only_with_consent(doctor, patient):
    response = start(doctor, patient["id"])
    assert response.status_code == 409
    assert response.json()["detail"]["purpose"] == "maternal_care"


def test_a_response_time_must_be_chosen_by_the_team(doctor, patient):
    grant(doctor, patient["id"], "maternal_care")
    response = doctor.post(f"{BASE}/episodes", json={"patient_id": patient["id"], "escalation_contact": "040-555-0100"})
    assert response.status_code == 422


def test_only_one_open_episode(doctor, care):
    _, record, _ = care
    assert start(doctor, record["id"]).status_code == 409


def test_no_visit_schedule_is_generated(care):
    _, _, episode = care
    assert episode["visits"] == []


def test_no_gestation_is_shown_without_a_confirmed_due_date(care):
    _, _, episode = care
    assert episode["gestation"] is None


def test_gestation_is_arithmetic_on_a_confirmed_date(doctor, care):
    _, record, episode = care
    due = (date.today() + timedelta(days=70)).isoformat()
    doctor.post(f"{BASE}/episodes/{episode['id']}/due-date", json={"estimated_due_date": due, "basis": "Dating scan"})
    current = doctor.get(f"{BASE}/patients/{record['id']}/episode").json()
    assert current["gestation"] == {"weeks": 30, "days": 0}
    assert current["due_date_confirmed_by"] == doctor.id


def test_impossible_due_dates_show_no_gestation():
    assert maternacare.gestation("2050-01-01", today=date(2026, 1, 1)) is None
    assert maternacare.gestation("2020-01-01", today=date(2026, 1, 1)) is None


def test_patients_cannot_set_the_due_date(care):
    account, _, episode = care
    response = account.post(f"{BASE}/episodes/{episode['id']}/due-date", json={"estimated_due_date": "2027-01-01", "basis": "Guess"})
    assert response.status_code == 403


def test_the_module_uses_no_ai():
    tree = ast.parse(Path(maternacare.__file__).read_text())
    imported = [node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert "llm" not in imported
    assert "anthropic" not in imported


def test_a_clinician_plans_visits_and_a_missed_one_becomes_follow_up(doctor, care):
    _, record, episode = care
    visit = doctor.post(f"{BASE}/episodes/{episode['id']}/visits", json={"planned_date": "2026-01-10", "purpose": "Antenatal check"}).json()
    assert doctor.get(f"{BASE}/patients/{record['id']}/episode").json()["visits"][0]["overdue"] is True
    doctor.patch(f"{BASE}/visits/{visit['id']}", json={"status": "missed"})
    task = doctor.get(f"/api/patients/{record['id']}/tasks").json()[0]
    assert "missed visit" in task["title"]
    assert "community health worker" in task["detail"]


def test_patient_readings_are_kept_apart_from_clinician_ones(doctor, care):
    account, _, episode = care
    mine = account.post(f"{BASE}/episodes/{episode['id']}/observations", json={"label": "Weight", "value_number": 62, "unit": "kg"}).json()
    theirs = doctor.post(f"{BASE}/episodes/{episode['id']}/observations", json={"label": "Hb", "value_number": 11, "unit": "g/dL"}).json()
    assert (mine["source_type"], mine["review_status"]) == ("patient_entered", "pending")
    assert (theirs["source_type"], theirs["review_status"]) == ("clinician_entered", "accepted")


def test_only_clinicians_add_to_the_risk_register(doctor, care):
    account, record, episode = care
    url = f"{BASE}/episodes/{episode['id']}/risks"
    assert account.post(url, json={"description": "Worried"}).status_code == 403
    risk = doctor.post(url, json={"description": "Previous caesarean section"}).json()
    assert risk["confirmed_by"] == doctor.id


def test_education_carries_its_publisher_and_version(doctor, care):
    account, record, episode = care
    doctor.post(
        f"{BASE}/episodes/{episode['id']}/education",
        json={"title": "Nutrition", "body": "Reviewed text", "publisher": "State health department", "content_version": "2026-1"},
    )
    item = account.get(f"{BASE}/patients/{record['id']}/episode").json()["education"][0]
    assert item["publisher"] == "State health department"
    assert item["approved_by"] == doctor.id


def test_patients_cannot_publish_education(care):
    account, _, episode = care
    response = account.post(
        f"{BASE}/episodes/{episode['id']}/education",
        json={"title": "x", "body": "y", "publisher": "z", "content_version": "1"},
    )
    assert response.status_code == 403


def test_a_patient_can_keep_their_birth_plan(care):
    account, record, episode = care
    response = account.put(f"{BASE}/episodes/{episode['id']}/birth-plan", json={"facility": "District hospital", "transport": "108 ambulance"})
    assert response.status_code == 200
    assert account.get(f"{BASE}/patients/{record['id']}/episode").json()["birth_plan"]["transport"] == "108 ambulance"


def test_a_concern_reaches_a_named_person_with_a_deadline(doctor, care):
    account, record, episode = care
    response = account.post(f"{BASE}/episodes/{episode['id']}/concerns", json={"description": "Less movement today"}).json()
    assert "112" in response["emergency_help"]
    assert response["contact"].startswith("Labour room")
    task = doctor.get(f"/api/patients/{record['id']}/tasks").json()[0]
    assert task["priority"] == "high"
    assert task["owner_user_id"] == doctor.id
    assert task["due_at"] == response["respond_by"]
    assert "Less movement" in doctor.get("/api/notifications").json()[0]["body"]


def test_a_concern_is_not_classified(care):
    account, _, episode = care
    response = account.post(f"{BASE}/episodes/{episode['id']}/concerns", json={"description": "Headache"}).json()
    assert not {"severity", "risk", "urgency", "triage"} & set(response)


def test_read_only_caregivers_can_still_raise_a_concern(doctor, person, care):
    account, record, episode = care
    code = account.post(f"/api/patients/{record['id']}/share-codes", json={"purpose": "caregiver", "scope": "read"}).json()["code"]
    caregiver = person("patient")
    caregiver.post("/api/patients/join", json={"code": code})
    assert caregiver.post(f"{BASE}/episodes/{episode['id']}/concerns", json={"description": "She feels faint"}).status_code == 201


def test_an_unanswered_concern_escalates_to_the_care_team(doctor, care):
    account, record, episode = care
    account.post(f"{BASE}/episodes/{episode['id']}/concerns", json={"description": "Pain"})
    from backend.db import connect

    db = connect()
    db.execute("UPDATE tasks SET due_at = '2020-01-01T00:00:00+00:00'")
    db.commit()
    db.close()
    assert any("Overdue" in note["title"] for note in doctor.get("/api/notifications").json())


def test_responding_clears_the_task_and_allows_closing(doctor, care):
    account, record, episode = care
    concern = account.post(f"{BASE}/episodes/{episode['id']}/concerns", json={"description": "Pain"}).json()
    assert doctor.post(f"{BASE}/episodes/{episode['id']}/close").status_code == 409
    doctor.post(f"{BASE}/concerns/{concern['id']}/respond", json={"outcome": "Seen in clinic"})
    assert doctor.get(f"/api/patients/{record['id']}/tasks").json()[0]["status"] == "done"
    assert doctor.post(f"{BASE}/episodes/{episode['id']}/close").status_code == 200


def test_delivery_moves_to_postnatal_and_links_a_newborn(doctor, care):
    account, record, episode = care
    response = doctor.post(
        f"{BASE}/episodes/{episode['id']}/delivery",
        json={"delivered_at": "2026-10-01T04:30:00+05:30", "facility": "District hospital", "outcome": "Live birth", "create_newborn_record": True},
    ).json()
    assert response["status"] == "postnatal"
    newborn = doctor.get(f"/api/patients/{response['newborn_patient_id']}").json()
    assert newborn["last_name"] == "Nair"
    assert newborn["date_of_birth"] == "2026-10-01"
    assert account.get(f"/api/patients/{response['newborn_patient_id']}").json()["relationship"] == "caregiver"
    current = doctor.get(f"{BASE}/patients/{record['id']}/episode").json()
    assert current["gestation"] is None
    assert doctor.post(f"{BASE}/episodes/{episode['id']}/delivery", json={"delivered_at": "2026-10-01T04:30:00", "facility": "x", "outcome": "y"}).status_code == 409


def test_strangers_see_nothing(person, care):
    _, record, episode = care
    assert person("doctor").get(f"{BASE}/patients/{record['id']}/episode").status_code == 404
    assert person("patient").post(f"{BASE}/episodes/{episode['id']}/concerns", json={"description": "x"}).status_code == 404
