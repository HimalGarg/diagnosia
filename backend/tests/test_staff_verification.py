import sqlite3

import pytest

from backend.config import settings
from backend.db import connect
from backend.records import staff
from backend.tests.conftest import linked_patient, verified_organization


def status_of(user_id):
    db = connect()
    try:
        return db.execute("SELECT account_status FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    finally:
        db.close()


def audit_actions():
    db = connect()
    try:
        return [row[0] for row in db.execute("SELECT action FROM audit_events")]
    finally:
        db.close()


def invite(admin, organization_id, candidate, role="staff"):
    code = admin.post(f"/api/organizations/{organization_id}/invitations", json={"email": candidate.email, "role": role}).json()["code"]
    assert candidate.post("/api/staff/invitations/accept", json={"code": code}).status_code == 200


def verify(admin, organization_id, candidate, profession="doctor"):
    return admin.post(
        f"/api/organizations/{organization_id}/staff/{candidate.id}/verify",
        json={"verified_profession": profession, "evidence_type": "Medical council register", "evidence_reference": "SYNTHETIC-123"},
    )


@pytest.fixture
def clinic(person):
    admin = person("doctor", "Anil", "Shah")
    return admin, verified_organization(admin)


def test_public_doctor_registration_never_creates_an_active_doctor(person):
    applicant = person("doctor", verified=False)
    assert status_of(applicant.id) == "pending_verification"
    assert applicant.post("/api/patients", json={"first_name": "A", "last_name": "B"}).status_code == 403
    assert applicant.get("/api/patients").json() == []


@pytest.mark.parametrize("role", ["specialist", "admin", "pharmacist", "verified_doctor"])
def test_other_staff_roles_cannot_be_self_declared(client, details, role):
    assert client.post("/api/auth/register", json={**details, "role": role}).status_code == 422


def test_registration_cannot_set_its_own_status(client, details):
    body = {**details, "account_status": "verified", "verified_profession": "pharmacist", "is_admin": True}
    response = client.post("/api/auth/register", json=body)
    assert response.json()["account_status"] == "pending_verification"
    assert client.post("/api/patients", json={"first_name": "A", "last_name": "B"}).status_code == 403


def test_patients_get_no_staff_privileges(person):
    account = person("patient")
    assert status_of(account.id) == "active"
    assert account.post("/api/patients", json={"first_name": "A", "last_name": "B"}).status_code == 403
    assert account.post("/api/staff/invitations/accept", json={"code": "abcdefghijkl"}).status_code == 403


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/organizations", {"name": "Fake", "type": "clinic"}),
        ("/api/patients", {"first_name": "A", "last_name": "B"}),
    ],
)
def test_pending_staff_can_reach_no_staff_route(person, path, body):
    assert person("doctor", verified=False).post(path, json=body).status_code == 403


def test_a_profile_cannot_grant_a_profession(person):
    applicant = person("doctor", verified=False)
    applicant.put("/api/practitioner", json={"profession": "pharmacist", "registration_number": "X"})
    db = connect()
    row = db.execute("SELECT verified_profession FROM practitioners WHERE user_id = ?", (applicant.id,)).fetchone()
    db.close()
    assert row["verified_profession"] is None


def test_verified_staff_cannot_rewrite_their_credentials(person, clinic):
    admin, organization_id = clinic
    applicant = person("doctor", verified=False)
    applicant.put("/api/practitioner", json={"profession": "doctor", "registration_number": "A1"})
    invite(admin, organization_id, applicant)
    verify(admin, organization_id, applicant)
    response = applicant.put("/api/practitioner", json={"profession": "pharmacist", "registration_number": "A1"})
    assert response.status_code == 409


def test_full_onboarding(person, clinic):
    admin, organization_id = clinic
    applicant = person("doctor", verified=False)
    invite(admin, organization_id, applicant)
    assert status_of(applicant.id) == "pending_verification"
    listed = admin.get(f"/api/organizations/{organization_id}/staff").json()
    assert any(row["user_id"] == applicant.id and row["account_status"] == "pending_verification" for row in listed)
    assert verify(admin, organization_id, applicant).status_code == 200
    assert status_of(applicant.id) == "verified"
    assert applicant.post("/api/patients", json={"first_name": "A", "last_name": "B"}).status_code == 201
    assert "staff.verified" in audit_actions()


def test_verification_records_who_and_what_evidence(person, clinic):
    admin, organization_id = clinic
    applicant = person("doctor", verified=False)
    invite(admin, organization_id, applicant)
    verify(admin, organization_id, applicant, "pharmacist")
    db = connect()
    row = db.execute("SELECT * FROM staff_verifications WHERE user_id = ?", (applicant.id,)).fetchone()
    db.close()
    assert row["decided_by"] == admin.id
    assert row["evidence_reference"] == "SYNTHETIC-123"
    assert row["verified_profession"] == "pharmacist"


def test_verification_needs_evidence(person, clinic):
    admin, organization_id = clinic
    applicant = person("doctor", verified=False)
    invite(admin, organization_id, applicant)
    response = admin.post(
        f"/api/organizations/{organization_id}/staff/{applicant.id}/verify",
        json={"verified_profession": "doctor", "evidence_type": "", "evidence_reference": ""},
    )
    assert response.status_code == 422


def test_nobody_can_verify_themselves(person, clinic):
    admin, organization_id = clinic
    assert verify(admin, organization_id, admin).status_code == 403


def test_pending_staff_cannot_verify_anyone(person, clinic):
    admin, organization_id = clinic
    applicant = person("doctor", verified=False)
    invite(admin, organization_id, applicant, "admin")
    other = person("doctor", verified=False)
    invite(admin, organization_id, other)
    assert verify(applicant, organization_id, other).status_code == 403
    assert verify(applicant, organization_id, applicant).status_code == 403


def test_ordinary_staff_cannot_verify_anyone(person, clinic):
    admin, organization_id = clinic
    colleague = person("doctor")
    admin.post(f"/api/organizations/{organization_id}/members", json={"email": colleague.email, "role": "staff"})
    applicant = person("doctor", verified=False)
    invite(admin, organization_id, applicant)
    assert verify(colleague, organization_id, applicant).status_code == 403


def test_a_self_made_organization_cannot_verify_anyone(person):
    founder = person("doctor")
    organization = founder.post("/api/organizations", json={"name": "Self-made", "type": "clinic"}).json()
    assert organization["verification_status"] == "unverified"
    response = founder.post(f"/api/organizations/{organization['id']}/invitations", json={"email": "a@example.com", "role": "admin"})
    assert response.status_code == 403
    applicant = person("doctor", verified=False)
    assert verify(founder, organization["id"], applicant).status_code == 403


def test_an_admin_cannot_act_on_another_organizations_staff(person, clinic):
    admin, organization_id = clinic
    other_admin = person("doctor")
    other = verified_organization(other_admin, "Other Hospital")
    applicant = person("doctor", verified=False)
    invite(admin, organization_id, applicant)
    assert verify(other_admin, other, applicant).status_code == 404
    assert verify(other_admin, organization_id, applicant).status_code == 403
    assert other_admin.get(f"/api/organizations/{organization_id}/staff").status_code == 403


def test_an_invitation_only_works_for_its_own_email(person, clinic):
    admin, organization_id = clinic
    intended, intruder = person("doctor", verified=False), person("doctor", verified=False)
    code = admin.post(f"/api/organizations/{organization_id}/invitations", json={"email": intended.email, "role": "admin"}).json()["code"]
    assert intruder.post("/api/staff/invitations/accept", json={"code": code}).status_code == 404
    assert intended.post("/api/staff/invitations/accept", json={"code": code}).status_code == 200
    assert intended.post("/api/staff/invitations/accept", json={"code": code}).status_code == 404


def test_an_invitation_gives_no_clinical_access(person, clinic):
    admin, organization_id = clinic
    applicant = person("doctor", verified=False)
    invite(admin, organization_id, applicant, "admin")
    assert applicant.post("/api/patients", json={"first_name": "A", "last_name": "B"}).status_code == 403


def test_invitation_codes_are_stored_only_as_hashes(person, clinic):
    admin, organization_id = clinic
    code = admin.post(f"/api/organizations/{organization_id}/invitations", json={"email": "x@example.com", "role": "staff"}).json()["code"]
    db = connect()
    db.execute("PRAGMA wal_checkpoint(FULL)")
    db.close()
    assert code.encode() not in settings.db_path.read_bytes()


def test_nobody_can_change_their_own_role(person, clinic):
    admin, organization_id = clinic
    response = admin.post(f"/api/organizations/{organization_id}/members", json={"email": admin.email, "role": "coordinator"})
    assert response.status_code == 403


def test_role_changes_are_audited(person, clinic):
    admin, organization_id = clinic
    colleague = person("doctor")
    admin.post(f"/api/organizations/{organization_id}/members", json={"email": colleague.email, "role": "staff"})
    assert "organization.role_changed" in audit_actions()


def test_unverified_people_cannot_be_given_roles(person, clinic):
    admin, organization_id = clinic
    applicant = person("doctor", verified=False)
    response = admin.post(f"/api/organizations/{organization_id}/members", json={"email": applicant.email, "role": "coordinator"})
    assert response.status_code == 404


def test_rejection_is_audited_and_gives_no_access(person, clinic):
    admin, organization_id = clinic
    applicant = person("doctor", verified=False)
    invite(admin, organization_id, applicant)
    url = f"/api/organizations/{organization_id}/staff/{applicant.id}/reject"
    assert admin.post(url, json={"reason": "Registration not found"}).status_code == 200
    assert status_of(applicant.id) == "rejected"
    assert applicant.post("/api/patients", json={"first_name": "A", "last_name": "B"}).status_code == 403
    assert verify(admin, organization_id, applicant).status_code == 409
    assert "staff.rejected" in audit_actions()


def test_suspension_ends_sessions_and_access_at_once(person, clinic, doctor, patient):
    admin, organization_id = clinic
    admin.post(f"/api/organizations/{organization_id}/members", json={"email": doctor.email, "role": "staff"})
    assert doctor.get(f"/api/patients/{patient['id']}").status_code == 200
    assert admin.post(f"/api/organizations/{organization_id}/staff/{doctor.id}/suspend", json={"reason": "Under review"}).status_code == 200
    assert doctor.get(f"/api/patients/{patient['id']}").status_code == 401
    assert doctor.get("/api/me").status_code == 401
    login = doctor.post("/api/auth/login", json={"email": doctor.email, "password": "a-long-password"})
    assert login.status_code == 403
    assert "staff.suspended" in audit_actions()


def test_a_suspended_admin_can_do_nothing(person, clinic):
    admin, organization_id = clinic
    second = person("doctor")
    admin.post(f"/api/organizations/{organization_id}/members", json={"email": second.email, "role": "admin"})
    second.post(f"/api/organizations/{organization_id}/staff/{admin.id}/suspend", json={"reason": "Under review"})
    assert admin.get(f"/api/organizations/{organization_id}/staff").status_code == 401


def test_reinstating_restores_access_with_a_new_sign_in(person, clinic, doctor, patient):
    admin, organization_id = clinic
    admin.post(f"/api/organizations/{organization_id}/members", json={"email": doctor.email, "role": "staff"})
    base = f"/api/organizations/{organization_id}/staff/{doctor.id}"
    admin.post(f"{base}/suspend", json={"reason": "Under review"})
    assert admin.post(f"{base}/reinstate", json={"reason": "Review finished"}).status_code == 200
    assert doctor.get("/api/me").status_code == 401
    doctor.post("/api/auth/login", json={"email": doctor.email, "password": "a-long-password"})
    assert doctor.get(f"/api/patients/{patient['id']}").status_code == 200


def test_care_team_place_doesnt_survive_losing_verification(person, clinic, doctor, patient):
    db = connect()
    db.execute("UPDATE users SET account_status = 'rejected' WHERE id = ?", (doctor.id,))
    db.commit()
    db.close()
    assert doctor.get(f"/api/patients/{patient['id']}").status_code == 404
    assert doctor.get("/api/patients").json() == []


def test_doctors_cannot_reach_another_organizations_patients(person, doctor, patient):
    other_admin = person("doctor")
    verified_organization(other_admin, "Elsewhere Hospital")
    assert other_admin.get(f"/api/patients/{patient['id']}").status_code == 404


def test_clinician_share_codes_need_a_verified_account(doctor, person, patient):
    code = doctor.post(f"/api/patients/{patient['id']}/share-codes", json={"purpose": "clinician"}).json()["code"]
    assert person("doctor", verified=False).post("/api/patients/join", json={"code": code}).status_code == 403


def test_verification_history_cannot_be_rewritten(person, clinic):
    db = connect()
    for statement in ("UPDATE staff_verifications SET decision = 'verified'", "DELETE FROM staff_verifications"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(statement)
    db.close()


def test_there_is_no_web_route_for_bootstrapping_an_organization():
    from backend.app import app

    paths = [route.path for route in app.routes]
    assert not any("bootstrap" in path for path in paths)
    assert not any(path.endswith("/verify-organization") for path in paths)


def test_operator_bootstrap_needs_evidence(person):
    account = person("doctor", verified=False)
    db = connect()
    with pytest.raises(ValueError):
        staff.operator_verify_organization(db, account.email, "Hospital", "hospital", "", "", "operator")
    db.close()


def test_login_is_throttled_after_repeated_failures(client, details):
    client.post("/api/auth/register", json=details)
    client.post("/api/auth/logout")
    for _ in range(10):
        client.post("/api/auth/login", json={"email": details["email"], "password": "wrong-password"})
    blocked = client.post("/api/auth/login", json={"email": details["email"], "password": details["password"]})
    assert blocked.status_code == 429


def test_staff_sessions_are_shorter_than_patient_sessions(client, details):
    cookie = client.post("/api/auth/register", json=details).headers["set-cookie"]
    assert "Max-Age=43200" in cookie


def test_patients_keep_their_records_with_linked_accounts(doctor, person):
    account, record = linked_patient(doctor, person)
    assert account.get(f"/api/patients/{record['id']}").status_code == 200
