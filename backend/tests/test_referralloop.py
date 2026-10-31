"""Scenario names come from the Healthtech Referral Management Toolkit's test scenarios."""
from datetime import date, timedelta

import pytest

from backend.referralloop.routes import exceptions_for
from backend.tests.conftest import grant, linked_patient, verified_organization

BASE = "/api/addons/referrals"


def soon(days=10):
    return (date.today() + timedelta(days=days)).isoformat()


@pytest.fixture
def clinic(person):
    """A receiving service with one coordinator."""
    coordinator = person("doctor", "Farah", "Khan")
    coordinator.organization_id = verified_organization(coordinator, "Lakeview Cardiology", "clinic")
    return coordinator


def send(doctor, patient, clinic, **fields):
    body = {
        "patient_id": patient["id"],
        "receiving_organization_id": clinic.organization_id,
        "specialty": "Cardiology",
        "reason": "Intermittent palpitations",
        "priority": "routine",
        "respond_by": soon(),
        **fields,
    }
    return doctor.post(BASE, json=body)


@pytest.fixture
def referral(doctor, patient, clinic):
    grant(doctor, patient["id"], "referral_sharing")
    response = send(doctor, patient, clinic)
    assert response.status_code == 201
    return response.json()


def move(client, referral_id, status, note=""):
    return client.patch(f"{BASE}/{referral_id}/status", json={"status": status, "note": note})


def to_attended(clinic, referral_id):
    clinic.post(f"{BASE}/{referral_id}/acknowledge")
    move(clinic, referral_id, "accepted")
    clinic.post(f"{BASE}/{referral_id}/appointments", json={"scheduled_at": soon(5), "location": "Room 2", "booked": True})
    move(clinic, referral_id, "attended")


def return_outcome(clinic, referral_id):
    return clinic.post(f"{BASE}/{referral_id}/outcome", data={"summary": "Holter normal.", "recommendations": "Review in primary care"})


def test_ts01_happy_path_closes_the_loop(doctor, clinic, referral):
    referral_id = referral["id"]
    assert clinic.get(f"{BASE}/inbox").json()[0]["id"] == referral_id
    to_attended(clinic, referral_id)
    assert return_outcome(clinic, referral_id).status_code == 201
    state = doctor.get(f"{BASE}/{referral_id}").json()
    assert state["status"] == "outcome_returned"
    assert state["appointments"][0]["status"] == "attended"
    assert doctor.post(f"{BASE}/{referral_id}/review", json={"note": "Agreed"}).json()["status"] == "closed"
    statuses = [event["to_status"] for event in doctor.get(f"{BASE}/{referral_id}").json()["events"]]
    assert statuses == ["sent", "acknowledged", "accepted", "booked", "attended", "outcome_returned", "closed"]


def test_ts03_a_declined_referral_stays_visible(doctor, clinic, referral):
    assert move(clinic, referral["id"], "declined", "No capacity").status_code == 200
    assert "declined" in doctor.get("/api/notifications").json()[0]["title"]
    queue = doctor.get(f"{BASE}/exceptions").json()
    assert queue[0]["id"] == referral["id"]
    assert "new plan" in queue[0]["exceptions"][0]


def test_decline_needs_a_reason(clinic, referral):
    assert move(clinic, referral["id"], "declined").status_code == 422


def test_ts04_urgent_referrals_jump_the_queue(doctor, person, clinic, referral):
    second = doctor.post("/api/patients", json={"first_name": "Sita", "last_name": "Devi"}).json()
    grant(doctor, second["id"], "referral_sharing")
    urgent = send(doctor, second, clinic, priority="urgent", respond_by=soon(3)).json()
    inbox = clinic.get(f"{BASE}/inbox").json()
    assert [item["id"] for item in inbox] == [urgent["id"], referral["id"]]


def test_ts05_unreachable_patient_is_flagged_to_the_referrer(doctor, clinic, referral):
    for _ in range(3):
        clinic.post(f"{BASE}/{referral['id']}/contact-attempts", json={"note": "No answer"})
    state = doctor.get(f"{BASE}/{referral['id']}").json()
    assert state["status"] == "sent"
    assert any("unreachable" in item for item in state["exceptions"])
    assert any("unreachable" in item["title"].lower() for item in doctor.get("/api/notifications").json())
    task = doctor.get(f"/api/patients/{referral['patient_id']}/tasks").json()[0]
    assert task["owner_user_id"] == doctor.id
    assert task["priority"] == "high"


def test_ts06_returned_outcome_is_a_task_for_the_referrer(doctor, clinic, referral):
    to_attended(clinic, referral["id"])
    return_outcome(clinic, referral["id"])
    task = doctor.get(f"/api/patients/{referral['patient_id']}/tasks").json()[0]
    assert "outcome" in task["title"]
    assert task["owner_user_id"] == doctor.id
    assert any("outcome" in event["title"] for event in doctor.get(f"/api/patients/{referral['patient_id']}/timeline").json())
    doctor.post(f"{BASE}/{referral['id']}/review", json={})
    assert doctor.get(f"/api/patients/{referral['patient_id']}/tasks").json()[0]["status"] == "done"


def test_ts07_duplicates_need_a_reason(doctor, patient, clinic, referral):
    response = send(doctor, patient, clinic)
    assert response.status_code == 409
    assert response.json()["detail"]["duplicate_of"] == referral["id"]
    confirmed = send(doctor, patient, clinic, confirm_duplicate_reason="Second opinion")
    assert confirmed.status_code == 201


def test_ts08_follow_up_links_to_the_earlier_referral(doctor, patient, clinic, referral):
    to_attended(clinic, referral["id"])
    return_outcome(clinic, referral["id"])
    doctor.post(f"{BASE}/{referral['id']}/review", json={})
    follow_up = send(doctor, patient, clinic, replaces_referral_id=referral["id"])
    assert follow_up.status_code == 201
    assert follow_up.json()["replaces_referral_id"] == referral["id"]


def test_sending_needs_consent(doctor, patient, clinic):
    response = send(doctor, patient, clinic)
    assert response.status_code == 409
    assert response.json()["detail"]["purpose"] == "referral_sharing"


def test_one_destination_is_required(doctor, patient, clinic):
    grant(doctor, patient["id"], "referral_sharing")
    assert send(doctor, patient, clinic, manual_destination="Phone: 040 555 0100").status_code == 422
    assert send(doctor, patient, clinic, receiving_organization_id=None).status_code == 422


def test_a_receiver_sees_the_referral_not_the_whole_record(doctor, clinic, referral):
    assert clinic.get(f"{BASE}/{referral['id']}").status_code == 200
    assert clinic.get(f"/api/patients/{referral['patient_id']}").status_code == 404
    assert clinic.get(f"/api/patients/{referral['patient_id']}/medications").status_code == 404


def test_outsiders_see_nothing(person, referral):
    outsider = person("doctor")
    assert outsider.get(f"{BASE}/{referral['id']}").status_code == 404
    assert outsider.post(f"{BASE}/{referral['id']}/acknowledge").status_code == 404


def test_the_package_shares_only_chosen_files(doctor, patient, clinic):
    grant(doctor, patient["id"], "referral_sharing")
    upload = lambda name: doctor.post(  # noqa: E731
        f"/api/patients/{patient['id']}/documents",
        files={"file": (name, b"%PDF-1.4 " + name.encode(), "application/pdf")},
    ).json()
    shared, private = upload("shared.pdf"), upload("private.pdf")
    created = send(doctor, patient, clinic, document_ids=[shared["id"]]).json()
    assert [item["id"] for item in created["documents"]] == [shared["id"]]
    assert clinic.get(f"/api/documents/{shared['id']}/file").status_code == 200
    assert clinic.get(f"/api/documents/{private['id']}/file").status_code == 404


def test_revoking_consent_cuts_off_the_receiving_service(doctor, patient, clinic):
    consent = grant(doctor, patient["id"], "referral_sharing")
    file = doctor.post(f"/api/patients/{patient['id']}/documents", files={"file": ("a.pdf", b"%PDF-1.4 a", "application/pdf")}).json()
    created = send(doctor, patient, clinic, document_ids=[file["id"]]).json()
    assert clinic.get(f"{BASE}/{created['id']}").status_code == 200
    doctor.post(f"/api/patients/{patient['id']}/consents/{consent['id']}/revoke")
    assert clinic.get(f"{BASE}/{created['id']}").status_code == 404
    assert clinic.get(f"/api/documents/{file['id']}/file").status_code == 404
    assert clinic.get(f"{BASE}/inbox").json() == []


def test_documents_of_another_patient_cannot_be_attached(doctor, patient, clinic):
    grant(doctor, patient["id"], "referral_sharing")
    other = doctor.post("/api/patients", json={"first_name": "A", "last_name": "B"}).json()
    file = doctor.post(f"/api/patients/{other['id']}/documents", files={"file": ("x.pdf", b"%PDF-1.4 x", "application/pdf")}).json()
    assert send(doctor, patient, clinic, document_ids=[file["id"]]).status_code == 422


@pytest.mark.parametrize("status", ["attended", "booked", "accepted"])
def test_steps_cannot_be_skipped(clinic, referral, status):
    assert move(clinic, referral["id"], status).status_code == 409


def test_the_referrer_cannot_act_for_a_receiving_service(doctor, referral):
    assert doctor.post(f"{BASE}/{referral['id']}/acknowledge").status_code == 409


def test_a_needs_information_request_goes_back_to_the_referrer(doctor, clinic, referral):
    move(clinic, referral["id"], "needs_information", "Please send the ECG")
    assert "waiting for more information" in doctor.get(f"{BASE}/{referral['id']}").json()["exceptions"][0]
    assert move(doctor, referral["id"], "sent", "ECG attached").status_code == 200


def test_a_priority_change_records_who_and_why(doctor, clinic, referral):
    response = clinic.post(f"{BASE}/{referral['id']}/priority", json={"priority": "urgent", "reason": "ECG reviewed"})
    assert response.json()["priority"] == "urgent"
    assert "ECG reviewed" in response.json()["events"][-1]["note"]
    assert response.json()["events"][-1]["first_name"] == "Farah"


def test_closing_without_an_outcome_needs_a_reason(doctor, referral):
    assert doctor.post(f"{BASE}/{referral['id']}/close", json={"reason": ""}).status_code == 422
    closed = doctor.post(f"{BASE}/{referral['id']}/close", json={"reason": "Patient moved away"}).json()
    assert closed["status"] == "cancelled"
    assert closed["closed_reason"] == "Patient moved away"
    assert doctor.get(f"{BASE}/{referral['id']}").json()["exceptions"] == []


def test_a_receiver_cannot_close_a_referral(clinic, referral):
    assert clinic.post(f"{BASE}/{referral['id']}/close", json={"reason": "Done"}).status_code == 403


def test_an_outcome_needs_a_visit(clinic, referral):
    assert return_outcome(clinic, referral["id"]).status_code == 409


def test_a_phone_referral_can_be_tracked_by_the_referrer(doctor, patient):
    grant(doctor, patient["id"], "referral_sharing")
    created = doctor.post(
        BASE,
        json={
            "patient_id": patient["id"],
            "manual_destination": "Phone: Dr Rao, 040 555 0100",
            "specialty": "Neurology",
            "reason": "Headaches",
            "priority": "routine",
            "respond_by": soon(),
        },
    ).json()
    assert doctor.post(f"{BASE}/{created['id']}/acknowledge").status_code == 200
    move(doctor, created["id"], "accepted")
    doctor.post(f"{BASE}/{created['id']}/appointments", json={"scheduled_at": soon(2), "booked": True})
    move(doctor, created["id"], "attended", "Reported by phone")
    assert doctor.post(f"{BASE}/{created['id']}/outcome", data={"summary": "Migraine confirmed"}).status_code == 201
    events = doctor.get(f"{BASE}/{created['id']}").json()["events"]
    assert any(event["source"] == "manual" for event in events)


def test_a_patient_sees_progress_but_not_clinical_detail(doctor, person, clinic):
    account, record = linked_patient(doctor, person)
    grant(doctor, record["id"], "referral_sharing")
    created = send(doctor, record, clinic, clinical_question="SENSITIVE-QUESTION").json()
    clinic.post(f"{BASE}/{created['id']}/acknowledge")
    move(clinic, created["id"], "accepted")
    clinic.post(f"{BASE}/{created['id']}/appointments", json={"scheduled_at": soon(5), "location": "Room 2", "booked": True})
    assert any("Appointment" in item["title"] for item in account.get("/api/notifications").json())
    view = account.get(f"{BASE}/patients/{record['id']}").json()[0]
    assert view["status"] == "booked"
    assert "SENSITIVE-QUESTION" not in str(view)
    assert view["side"] == "patient"


def test_a_past_respond_by_date_is_refused(doctor, patient, clinic):
    grant(doctor, patient["id"], "referral_sharing")
    assert send(doctor, patient, clinic, respond_by="2020-01-01").status_code == 422


def base_referral(**fields):
    return {"status": "sent", "respond_by": "2026-01-10", "contact_attempts": 0, "appointments": [], **fields}


def test_silent_referrals_are_exceptions():
    assert exceptions_for(base_referral(), today=date(2026, 1, 20))
    assert exceptions_for(base_referral(), today=date(2026, 1, 5)) == []


def test_a_missed_appointment_date_is_an_exception():
    referral = base_referral(status="booked", appointments=[{"status": "booked", "scheduled_at": "2026-01-02T10:00"}])
    assert "appointment date has passed" in exceptions_for(referral, today=date(2026, 1, 20))[0]


def test_an_unreviewed_outcome_is_an_exception():
    assert "hasn't been reviewed" in exceptions_for(base_referral(status="outcome_returned"), today=date(2026, 1, 5))[0]
