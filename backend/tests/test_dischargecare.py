import pytest

from backend.tests.conftest import linked_patient

BASE = "/api/addons/dischargecare"


def create(doctor, patient_id, **fields):
    response = doctor.post(
        f"{BASE}/episodes",
        json={
            "patient_id": patient_id,
            "discharge_date": "2026-11-01",
            "destination": "Home with family",
            "problems": "Community-acquired pneumonia, treated",
            "emergency_contact": "Ward 4 nurses: 040-555-0100",
            **fields,
        },
    )
    assert response.status_code == 201
    return response.json()


def instruction(doctor, episode_id):
    return doctor.post(f"{BASE}/episodes/{episode_id}/instructions", json={"topic": "Medicines", "text": "Take as listed."})


def task(doctor, episode_id, **fields):
    return doctor.post(
        f"{BASE}/episodes/{episode_id}/tasks",
        json={"title": "Attend follow-up visit", "audience": "patient", **fields},
    )


def approve(doctor, episode_id):
    return doctor.post(f"{BASE}/episodes/{episode_id}/approve")


def reconciled(doctor, patient_id):
    doctor.post(f"/api/addons/medguard/patients/{patient_id}/medications", json={"written_name": "Telma"})
    result = doctor.post("/api/addons/medguard/reconcile", json={"patient_id": patient_id}).json()
    medicine = doctor.get(f"/api/patients/{patient_id}/medications").json()[0]
    doctor.post(
        f"/api/addons/medguard/reconciliations/{result['id']}/finalise",
        json={"decisions": [{"medication_id": medicine["id"], "decision": "continue"}]},
    )
    return result["id"]


@pytest.fixture
def active(doctor, person):
    account, record = linked_patient(doctor, person)
    episode = create(doctor, record["id"])
    instruction(doctor, episode["id"])
    task(doctor, episode["id"], title="Book chest X-ray")
    assert approve(doctor, episode["id"]).status_code == 200
    return account, record, episode


def test_nothing_reaches_the_patient_before_approval(doctor, person):
    account, record = linked_patient(doctor, person)
    episode = create(doctor, record["id"])
    task(doctor, episode["id"])
    assert account.get(f"{BASE}/episodes/{episode['id']}/checklist").status_code == 404
    assert account.get(f"/api/patients/{record['id']}/tasks").json() == []
    assert account.get(f"{BASE}/patients/{record['id']}/episodes").json() == []


def test_approval_needs_a_complete_summary(doctor, patient):
    episode = create(doctor, patient["id"], emergency_contact="")
    response = approve(doctor, episode["id"])
    assert response.status_code == 422
    assert "emergency contact" in response.json()["detail"]
    assert "instruction" in response.json()["detail"]


def test_only_the_responsible_clinician_approves(doctor, person, patient):
    episode = create(doctor, patient["id"])
    instruction(doctor, episode["id"])
    code = doctor.post(f"/api/patients/{patient['id']}/share-codes", json={"purpose": "clinician"}).json()["code"]
    colleague = person("doctor")
    colleague.post("/api/patients/join", json={"code": code})
    assert approve(colleague, episode["id"]).status_code == 403


def test_patients_cannot_approve(doctor, person):
    account, record = linked_patient(doctor, person)
    episode = create(doctor, record["id"])
    assert approve(account, episode["id"]).status_code == 403


def test_approval_releases_the_tasks_and_tells_the_patient(doctor, active):
    account, record, _ = active
    assert [item["title"] for item in account.get(f"/api/patients/{record['id']}/tasks").json()] == ["Book chest X-ray"]
    assert "checklist" in account.get("/api/notifications").json()[0]["title"]


def test_checklist_shows_approved_content_and_marks_pending_results(doctor, active):
    account, _, episode = active
    doctor.post(f"{BASE}/episodes/{episode['id']}/pending-results", json={"test_name": "Blood culture"})
    checklist = account.get(f"{BASE}/episodes/{episode['id']}/checklist").json()
    assert checklist["emergency_contact"].startswith("Ward 4")
    assert checklist["instructions"][0]["topic"] == "Medicines"
    assert checklist["pending_results"][0]["status"] == "pending"
    assert "problems" not in checklist


def test_a_patient_can_mark_their_task_done(active):
    account, record, _ = active
    item = account.get(f"/api/patients/{record['id']}/tasks").json()[0]
    assert account.post(f"/api/tasks/{item['id']}/status", json={"status": "done"}).status_code == 200


def test_the_approved_summary_is_fixed(doctor, active):
    _, _, episode = active
    url = f"{BASE}/episodes/{episode['id']}"
    assert doctor.patch(url, json={"problems": "Changed"}).status_code == 409
    assert doctor.post(f"{url}/instructions", json={"topic": "x", "text": "y"}).status_code == 409


def test_closing_is_blocked_until_the_work_is_done(doctor, active):
    _, _, episode = active
    url = f"{BASE}/episodes/{episode['id']}"
    doctor.post(f"{url}/pending-results", json={"test_name": "Blood culture"})
    response = doctor.post(f"{url}/close", json={})
    assert response.status_code == 409
    blockers = response.json()["detail"]["blockers"]
    assert len(blockers) == 3
    assert doctor.get(url).json()["close_blockers"] == blockers


def test_a_recorded_exception_can_close_it(doctor, active):
    _, record, episode = active
    closed = doctor.post(f"{BASE}/episodes/{episode['id']}/close", json={"exception_reason": "Transferred to another hospital"}).json()
    assert closed["status"] == "closed"
    assert closed["close_exception"] == "Transferred to another hospital"
    assert any("closed" in event["title"] for event in doctor.get(f"/api/patients/{record['id']}/timeline").json())


def test_it_closes_cleanly_when_everything_is_done(doctor, active):
    account, record, episode = active
    url = f"{BASE}/episodes/{episode['id']}"
    item = account.get(f"/api/patients/{record['id']}/tasks").json()[0]
    account.post(f"/api/tasks/{item['id']}/status", json={"status": "done"})
    pending = doctor.post(f"{url}/pending-results", json={"test_name": "Culture"}).json()["pending_results"][0]
    doctor.post(f"{BASE}/pending-results/{pending['id']}/finalise", json={"result_note": "No growth"})
    doctor.patch(url, json={"reconciliation_id": reconciled(doctor, record["id"])})
    assert doctor.post(f"{url}/close", json={}).json()["close_exception"] == ""


def test_only_a_finalised_reconciliation_can_be_linked(doctor, patient):
    episode = create(doctor, patient["id"])
    doctor.post(f"/api/addons/medguard/patients/{patient['id']}/medications", json={"written_name": "Telma"})
    draft = doctor.post("/api/addons/medguard/reconcile", json={"patient_id": patient["id"]}).json()
    assert doctor.patch(f"{BASE}/episodes/{episode['id']}", json={"reconciliation_id": draft["id"]}).status_code == 422


def test_medicines_on_the_checklist_come_from_the_reconciled_list(doctor, active):
    account, record, episode = active
    doctor.patch(f"{BASE}/episodes/{episode['id']}", json={"reconciliation_id": reconciled(doctor, record["id"])})
    medicines = account.get(f"{BASE}/episodes/{episode['id']}/checklist").json()["medicines"]
    assert [item["written_name"] for item in medicines] == ["Telma"]


def test_overdue_high_priority_tasks_escalate_to_the_named_clinician(doctor, active):
    _, _, episode = active
    task(doctor, episode["id"], title="Wound review", audience="care_team", priority="high", due_at="2020-01-01T00:00:00Z")
    notes = doctor.get("/api/notifications").json()
    assert any("Wound review" in note["title"] for note in notes)
    assert doctor.get(f"{BASE}/board").json()[0]["tasks_escalated"] == 1


def test_board_shows_barriers_and_flags_first(doctor, patient):
    quiet = create(doctor, patient["id"], destination="Home")
    flagged = create(doctor, patient["id"], destination="Rehab")
    doctor.patch(f"{BASE}/episodes/{flagged['id']}", json={"flagged": True})
    added = doctor.post(f"{BASE}/episodes/{quiet['id']}/barriers", json={"description": "Transport not arranged", "owner": "Social worker"}).json()
    board = doctor.get(f"{BASE}/board").json()
    assert board[0]["id"] == flagged["id"]
    assert board[1]["barriers_open"] == 1
    doctor.post(f"{BASE}/barriers/{added['barriers'][0]['id']}/resolve")
    assert doctor.get(f"{BASE}/board").json()[1]["barriers_open"] == 0


def test_strangers_cannot_open_a_discharge(person, active):
    _, _, episode = active
    assert person("doctor").get(f"{BASE}/episodes/{episode['id']}").status_code == 404
    assert person("doctor").get(f"{BASE}/episodes/{episode['id']}/checklist").status_code == 404


def test_the_board_lists_only_my_patients(person, active):
    assert person("doctor").get(f"{BASE}/board").json() == []
