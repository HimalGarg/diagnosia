import sqlite3

import pytest

from backend.config import settings
from backend.db import connect
from backend.records import care
from backend.records.store import now_iso
from backend.tests.conftest import grant, linked_patient


def audit_actions(patient_id):
    db = connect()
    try:
        return [row["action"] for row in db.execute("SELECT action FROM audit_events WHERE patient_id = ?", (patient_id,))]
    finally:
        db.close()


def test_a_new_account_sees_no_patients(person, patient):
    stranger = person("doctor")
    assert stranger.get("/api/patients").json() == []


def test_strangers_get_a_404_and_the_attempt_is_audited(person, patient):
    stranger = person("doctor")
    response = stranger.get(f"/api/patients/{patient['id']}")
    assert response.status_code == 404
    db = connect()
    denied = db.execute("SELECT outcome FROM audit_events WHERE patient_id = ? AND outcome = 'denied'", (patient["id"],)).fetchone()
    db.close()
    assert denied is not None


@pytest.mark.parametrize("path", ["timeline", "documents", "observations", "medications", "tasks", "consents", "care-team", "fhir"])
def test_every_record_route_is_closed_to_strangers(person, patient, path):
    stranger = person("doctor")
    assert stranger.get(f"/api/patients/{patient['id']}/{path}").status_code == 404


def test_signed_out_users_get_nothing(client, patient):
    client.cookies.clear()
    assert client.get(f"/api/patients/{patient['id']}").status_code == 401


def test_reads_are_audited(doctor, patient):
    doctor.get(f"/api/patients/{patient['id']}/timeline")
    assert "timeline.read" in audit_actions(patient["id"])


def test_audit_events_cannot_be_changed_or_deleted(doctor, patient):
    db = connect()
    for statement in ("UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(statement)
    db.close()


def test_patients_can_see_who_opened_their_record(doctor, person):
    account, record = linked_patient(doctor, person)
    doctor.get(f"/api/patients/{record['id']}")
    events = account.get(f"/api/patients/{record['id']}/audit-events").json()
    assert any(event["action"] == "patient.read" and event["first_name"] == "Meera" for event in events)


def test_only_a_patient_account_can_claim_a_record(doctor, person, patient):
    code = doctor.post(f"/api/patients/{patient['id']}/share-codes", json={"purpose": "claim"}).json()["code"]
    assert person("doctor").post("/api/patients/join", json={"code": code}).status_code == 403


def test_share_codes_work_once(doctor, person, patient):
    code = doctor.post(f"/api/patients/{patient['id']}/share-codes", json={"purpose": "clinician"}).json()["code"]
    assert person("doctor").post("/api/patients/join", json={"code": code}).status_code == 200
    assert person("doctor").post("/api/patients/join", json={"code": code}).status_code == 404


def test_share_codes_are_stored_only_as_hashes(doctor, patient):
    code = doctor.post(f"/api/patients/{patient['id']}/share-codes", json={"purpose": "clinician"}).json()["code"]
    assert code.encode() not in settings.db_path.read_bytes()


def test_a_patient_can_revoke_a_clinician(doctor, person):
    account, record = linked_patient(doctor, person)
    other = person("doctor")
    code = account.post(f"/api/patients/{record['id']}/share-codes", json={"purpose": "clinician"}).json()["code"]
    other.post("/api/patients/join", json={"code": code})
    assert other.get(f"/api/patients/{record['id']}").status_code == 200
    member = next(row for row in account.get(f"/api/patients/{record['id']}/care-team").json() if row["user_id"] == other.id)
    assert account.delete(f"/api/patients/{record['id']}/care-team/{member['id']}").status_code == 204
    assert other.get(f"/api/patients/{record['id']}").status_code == 404


def test_read_only_caregivers_cannot_write(doctor, person):
    account, record = linked_patient(doctor, person)
    caregiver = person("patient")
    code = account.post(f"/api/patients/{record['id']}/share-codes", json={"purpose": "caregiver", "scope": "read"}).json()["code"]
    caregiver.post("/api/patients/join", json={"code": code})
    assert caregiver.get(f"/api/patients/{record['id']}").status_code == 200
    response = caregiver.post(f"/api/patients/{record['id']}/allergies", json={"substance": "Penicillin"})
    assert response.status_code == 403


def test_caregivers_cannot_give_others_access(doctor, person):
    account, record = linked_patient(doctor, person)
    caregiver = person("patient")
    code = account.post(f"/api/patients/{record['id']}/share-codes", json={"purpose": "caregiver", "scope": "full"}).json()["code"]
    caregiver.post("/api/patients/join", json={"code": code})
    response = caregiver.post(f"/api/patients/{record['id']}/share-codes", json={"purpose": "clinician"})
    assert response.status_code == 403


def test_consent_can_be_revoked_and_stays_on_record(doctor, patient):
    consent = grant(doctor, patient["id"], "ai_processing")
    assert doctor.post(f"/api/patients/{patient['id']}/consents/{consent['id']}/revoke").status_code == 200
    db = connect()
    assert care.active_consent(db, patient["id"], "ai_processing") is None
    db.close()
    consents = doctor.get(f"/api/patients/{patient['id']}/consents").json()["consents"]
    assert consents[0]["revoked_at"] is not None
    assert consents[0]["policy_version"] == care.POLICY_VERSION


def test_consent_records_how_it_was_given(doctor, person):
    account, record = linked_patient(doctor, person)
    assert grant(doctor, record["id"], "ai_processing")["method"] == "clinician_recorded"
    assert grant(account, record["id"], "ai_scribe")["method"] == "patient_in_app"


def pdf_bytes():
    return b"%PDF-1.4 a synthetic test file"


def test_uploads_are_checked_by_their_content(doctor, patient):
    response = doctor.post(
        f"/api/patients/{patient['id']}/documents",
        files={"file": ("report.pdf", b"<script>alert(1)</script>", "application/pdf")},
    )
    assert response.status_code == 422


def test_documents_download_only_for_people_with_access(doctor, person, patient):
    uploaded = doctor.post(f"/api/patients/{patient['id']}/documents", files={"file": ("a.pdf", pdf_bytes(), "application/pdf")}).json()
    assert "stored_name" not in uploaded
    response = doctor.get(f"/api/documents/{uploaded['id']}/file")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    assert person("doctor").get(f"/api/documents/{uploaded['id']}/file").status_code == 404


def test_the_same_file_twice_is_one_document(doctor, patient):
    first = doctor.post(f"/api/patients/{patient['id']}/documents", files={"file": ("a.pdf", pdf_bytes(), "application/pdf")}).json()
    second = doctor.post(f"/api/patients/{patient['id']}/documents", files={"file": ("b.pdf", pdf_bytes(), "application/pdf")}).json()
    assert first["id"] == second["id"]


def test_filenames_cannot_escape_the_storage_folder(doctor, patient):
    uploaded = doctor.post(
        f"/api/patients/{patient['id']}/documents",
        files={"file": ("../../etc/passwd.pdf", pdf_bytes(), "application/pdf")},
    ).json()
    assert uploaded["filename"] == "passwd.pdf"


def add_task(doctor, patient, **fields):
    return doctor.post(f"/api/patients/{patient['id']}/tasks", json={"title": "Call the patient", **fields}).json()


def test_overdue_high_priority_tasks_escalate_once_to_a_named_person(doctor, patient):
    add_task(doctor, patient, priority="high", due_at="2020-01-01T00:00:00+00:00")
    assert len(doctor.get("/api/notifications").json()) == 1
    assert len(doctor.get("/api/notifications").json()) == 1


def test_routine_tasks_do_not_escalate(doctor, patient):
    add_task(doctor, patient, priority="routine", due_at="2020-01-01T00:00:00+00:00")
    assert doctor.get("/api/notifications").json() == []


def test_blocked_high_priority_task_alerts_the_escalation_owner(doctor, person):
    account, record = linked_patient(doctor, person)
    task = add_task(doctor, record, audience="patient", priority="high")
    assert account.post(f"/api/tasks/{task['id']}/status", json={"status": "blocked", "note": "No transport"}).status_code == 200
    assert "blocked" in doctor.get("/api/notifications").json()[0]["title"]


def test_patients_see_only_their_own_tasks(doctor, person):
    account, record = linked_patient(doctor, person)
    add_task(doctor, record, audience="care_team")
    add_task(doctor, record, audience="patient")
    assert len(account.get(f"/api/patients/{record['id']}/tasks").json()) == 1
    assert len(doctor.get(f"/api/patients/{record['id']}/tasks").json()) == 2


def test_patients_cannot_change_care_team_tasks(doctor, person):
    account, record = linked_patient(doctor, person)
    task = add_task(doctor, record, audience="care_team")
    assert account.post(f"/api/tasks/{task['id']}/status", json={"status": "done"}).status_code == 403


def test_notifications_are_private(doctor, person, patient):
    add_task(doctor, patient, priority="high", due_at="2020-01-01T00:00:00+00:00")
    notification = doctor.get("/api/notifications").json()[0]
    person("doctor").post(f"/api/notifications/{notification['id']}/read")
    assert doctor.get("/api/notifications").json()[0]["read_at"] is None


def test_cross_site_writes_are_refused(doctor, patient):
    response = doctor.post(
        f"/api/patients/{patient['id']}/allergies",
        json={"substance": "Penicillin"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_a_bad_foreign_key_is_a_clean_error(doctor, patient):
    response = doctor.post(f"/api/patients/{patient['id']}/tasks", json={"title": "x", "owner_user_id": 999999})
    assert response.status_code == 409


def test_fhir_bundle_leaves_out_anything_unreviewed(doctor, patient):
    db = connect()
    db.execute(
        """
        INSERT INTO observations (id, patient_id, category, label, value_number, effective_at, source_type, review_status, created_at)
        VALUES ('pending-one', ?, 'laboratory', 'Hemoglobin', 11, ?, 'ai_extracted', 'pending', ?)
        """,
        (patient["id"], now_iso(), now_iso()),
    )
    db.commit()
    db.close()
    bundle = doctor.get(f"/api/patients/{patient['id']}/fhir").json()
    types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert bundle["resourceType"] == "Bundle"
    assert "Patient" in types
    assert "Observation" not in types
