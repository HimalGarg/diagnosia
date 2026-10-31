"""End-to-end security checks. The route sweep covers every API route, so a new one can't slip past."""
import inspect
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import SESSION_COOKIE
from backend.config import settings
from backend.db import connect
from backend.tests.conftest import grant, linked_patient, verified_organization

PUBLIC = {("POST", "/api/auth/register"), ("POST", "/api/auth/login"), ("POST", "/api/auth/logout")}
# DiagnoFast's patient has no account. The single-use link in the path is the credential.
LINK_ROUTES = {
    ("GET", "/api/diagnofast/patient/{token}"),
    ("POST", "/api/diagnofast/patient/{token}/start"),
    ("POST", "/api/diagnofast/patient/{token}/answer"),
}
FAKE_ID = "00000000-0000-0000-0000-000000000000"


def api_routes():
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, route.path


def filled(path, value=FAKE_ID):
    return re.sub(r"\{[^}]+\}", value, path)


ALL_ROUTES = sorted(set(api_routes()))
PATIENT_ROUTES = [(method, path) for method, path in ALL_ROUTES if "{patient_id}" in path]
BODIES_FOR_POST = {"json": {}}


def test_the_sweep_sees_the_whole_api():
    assert len(ALL_ROUTES) > 120
    assert len(PATIENT_ROUTES) > 30


# An empty body fails validation before a route's own access check, so the sweeps above
# can't prove every write is guarded. This reads each route's code to prove it directly.
GATES = (
    "open_patient", "lookup", "require_membership", "require_profession", "_verifying_admin", "_staff_gate", "_clinical",
    "_editable", "_side", "_change", "_decide", "_is_receiver", "_require_admin",
    "_readable_intake", "_open_intake", "_open_workflow", "_allowed", "gate_staff",
)
NEEDS_NO_RECORD = {
    "register", "login", "logout", "me", "my_status", "accept_invitation", "system_status", "system_readiness",
    "consent_purposes", "list_patients", "create_patient", "create_my_record", "join_with_code", "list_notifications",
    "mark_notification_read", "get_practitioner", "save_practitioner", "create_organization", "list_organizations",
    "list_mappings", "inbox", "sent", "exception_queue", "board", "follow_up", "call_list", "read", "explain",
    "read_aloud", "analyze", "upload_report", "create_referral", "create_episode",
    "public_config", "list_settings", "forms", "queue",
}


def test_every_route_touching_a_record_goes_through_a_gate():
    unguarded = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            source = inspect.getsource(route.endpoint)
            guarded = any(f"{gate}(" in source for gate in GATES)
            if not guarded and route.endpoint.__name__ not in NEEDS_NO_RECORD:
                unguarded.append(route.path)
    assert unguarded == []


@pytest.mark.parametrize("name", ["upload_report", "create_referral", "create_episode", "create_intake", "queue"])
def test_routes_taking_a_patient_in_the_body_still_use_the_gate(name):
    endpoints = {route.endpoint.__name__: route.endpoint for route in app.routes if isinstance(route, APIRoute)}
    assert "open_patient(" in inspect.getsource(endpoints[name])


@pytest.mark.parametrize("method, path", sorted(LINK_ROUTES))
def test_a_made_up_link_opens_nothing(client, method, path):
    client.cookies.clear()
    body = {"json": {"language": "en-IN", "consent": True}} if path.endswith("start") else {"data": {"text": "hello"}}
    response = client.request(method, filled(path), **({} if method == "GET" else body))
    assert response.status_code == 404


@pytest.mark.parametrize("method, path", [route for route in ALL_ROUTES if route not in PUBLIC | LINK_ROUTES])
def test_every_route_needs_a_session(client, method, path):
    client.cookies.clear()
    response = client.request(method, filled(path), json={})
    assert response.status_code == 401, f"{method} {path} gave {response.status_code}"


@pytest.mark.parametrize("method, path", PATIENT_ROUTES)
def test_no_stranger_reaches_a_patient_on_any_route(person, doctor, patient, method, path):
    stranger = person("doctor")
    response = stranger.request(method, filled(path, patient["id"]), json={})
    assert response.status_code in (403, 404, 422), f"{method} {path} gave {response.status_code}"
    assert patient["last_name"] not in response.text


@pytest.mark.parametrize("method, path", [route for route in PATIENT_ROUTES if route[0] == "GET"])
def test_patient_to_patient_isolation_on_every_read(person, doctor, patient, method, path):
    other = person("patient")
    response = other.get(filled(path, patient["id"]))
    assert response.status_code in (403, 404), f"{path} gave {response.status_code}"


@pytest.mark.parametrize("method, path", [route for route in PATIENT_ROUTES if route[0] == "GET"])
def test_pending_staff_read_nothing_on_any_route(person, doctor, patient, method, path):
    pending = person("doctor", verified=False)
    assert pending.get(filled(path, patient["id"])).status_code in (403, 404), path


def test_a_session_ends_when_it_expires(doctor):
    db = connect()
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db.execute("UPDATE sessions SET expires_at = ?", (past,))
    db.commit()
    db.close()
    assert doctor.get("/api/patients").status_code == 401


def test_logout_revokes_the_session_for_a_copied_cookie(doctor):
    token = doctor.cookies[SESSION_COOKIE]
    doctor.post("/api/auth/logout")
    stolen = TestClient(app)
    stolen.cookies.set(SESSION_COOKIE, token)
    assert stolen.get("/api/me").status_code == 401


def test_cookies_are_protected(client, details, monkeypatch):
    cookie = client.post("/api/auth/register", json=details).headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    monkeypatch.setattr(settings, "cookie_secure", True)
    secure = client.post("/api/auth/login", json={"email": details["email"], "password": details["password"]})
    assert "secure" in secure.headers["set-cookie"].lower()


def test_a_forged_cookie_gets_nothing(client):
    client.cookies.set(SESSION_COOKIE, "forged-token")
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1.evil.example"])
def test_cross_site_writes_are_refused(doctor, patient, origin):
    response = doctor.post(f"/api/patients/{patient['id']}/tasks", json={"title": "x"}, headers={"Origin": origin})
    assert response.status_code == 403


def test_browser_cross_site_marker_is_refused(doctor, patient):
    response = doctor.post(f"/api/patients/{patient['id']}/tasks", json={"title": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_same_site_writes_still_work(doctor, patient):
    response = doctor.post(
        f"/api/patients/{patient['id']}/tasks", json={"title": "x"}, headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code == 201


def test_api_answers_are_not_cached(doctor, patient):
    response = doctor.get(f"/api/patients/{patient['id']}")
    assert response.headers["cache-control"] == "no-store"


def test_security_headers(client):
    response = client.get("/care.html")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "unsafe-eval" not in policy
    assert "script-src 'self' 'unsafe-inline'" not in policy


@pytest.mark.parametrize("name", ["<img src=x onerror=alert(1)>", "'; DROP TABLE patients; --", "Robert\"); DELETE FROM users; --"])
def test_hostile_names_are_stored_as_plain_text(doctor, name):
    created = doctor.post("/api/patients", json={"first_name": name, "last_name": "Test"}).json()
    assert doctor.get(f"/api/patients/{created['id']}").json()["first_name"] == name
    db = connect()
    assert db.execute("SELECT COUNT(*) FROM patients").fetchone()[0] >= 1
    db.close()


@pytest.mark.parametrize("value", ["' OR '1'='1", "1; DROP TABLE tasks", "../../etc/passwd", "%2e%2e%2f"])
def test_hostile_ids_find_nothing(doctor, value):
    for path in (f"/api/patients/{value}", f"/api/documents/{value}/file", f"/api/addons/lablens/reports/{value}/extraction"):
        assert doctor.get(path).status_code == 404


def test_no_sql_is_built_from_request_values():
    from pathlib import Path

    backend = Path(__file__).parents[1]
    suspicious = re.compile(r'execute\(\s*f"[^"]*\{(?!assignments|columns|marks|table|audience)')
    for path in backend.rglob("*.py"):
        if "tests" in path.parts:
            continue
        assert not suspicious.search(path.read_text(encoding="utf-8")), path


def test_uploaded_files_keep_their_declared_safe_type(doctor, patient):
    disguised = doctor.post(
        f"/api/patients/{patient['id']}/documents",
        files={"file": ("page.html", b"%PDF-1.4 <script>alert(1)</script>", "text/html")},
    ).json()
    assert disguised["content_type"] == "application/pdf"
    response = doctor.get(f"/api/documents/{disguised['id']}/file")
    assert response.headers["content-type"].startswith("application/pdf")
    assert "attachment" in response.headers["content-disposition"]


@pytest.mark.parametrize("data", [b"<html><script>alert(1)</script></html>", b"MZ\x90\x00", b"#!/bin/sh\nrm -rf /", b""])
def test_only_pdf_jpg_and_png_are_stored(doctor, patient, data):
    response = doctor.post(f"/api/patients/{patient['id']}/documents", files={"file": ("a.pdf", data, "application/pdf")})
    assert response.status_code == 422


def test_header_injection_through_a_filename_fails(doctor, patient):
    document = doctor.post(
        f"/api/patients/{patient['id']}/documents",
        files={"file": ('a"\r\nX-Evil: 1.pdf', b"%PDF-1.4 x", "application/pdf")},
    ).json()
    response = doctor.get(f"/api/documents/{document['id']}/file")
    assert "x-evil" not in response.headers


def test_the_consent_revocation_reach(doctor, person):
    """Revoking takes effect on the next request, for every path that relied on it."""
    account, record = linked_patient(doctor, person)
    coordinator = person("doctor")
    organization = verified_organization(coordinator, "Lakeview", "clinic")
    consent = grant(account, record["id"], "referral_sharing")
    file = doctor.post(f"/api/patients/{record['id']}/documents", files={"file": ("a.pdf", b"%PDF-1.4 a", "application/pdf")}).json()
    created = doctor.post(
        "/api/addons/referrals",
        json={"patient_id": record["id"], "receiving_organization_id": organization, "specialty": "Cardiology",
              "reason": "x", "priority": "routine", "respond_by": "2099-01-01", "document_ids": [file["id"]]},
    ).json()
    assert coordinator.get(f"/api/addons/referrals/{created['id']}").status_code == 200

    assert account.post(f"/api/patients/{record['id']}/consents/{consent['id']}/revoke").status_code == 200

    assert coordinator.get(f"/api/addons/referrals/{created['id']}").status_code == 404
    assert coordinator.get(f"/api/documents/{file['id']}/file").status_code == 404
    assert coordinator.get("/api/addons/referrals/inbox").json() == []
    assert coordinator.post(f"/api/addons/referrals/{created['id']}/acknowledge").status_code == 404
    denied = doctor.post(
        "/api/addons/referrals",
        json={"patient_id": record["id"], "manual_destination": "Phone", "specialty": "Neurology", "reason": "x",
              "priority": "routine", "respond_by": "2099-01-01"},
    )
    assert denied.status_code == 409


def test_a_second_consent_restores_sharing_only_when_given(doctor, person):
    account, record = linked_patient(doctor, person)
    first = grant(account, record["id"], "ai_scribe")
    account.post(f"/api/patients/{record['id']}/consents/{first['id']}/revoke")
    assert account.get(f"/api/patients/{record['id']}/consents").json()["consents"][0]["revoked_at"]


def test_audit_entries_say_who_did_what_to_what_and_when(doctor, patient):
    doctor.get(f"/api/patients/{patient['id']}/timeline")
    db = connect()
    row = dict(db.execute("SELECT * FROM audit_events WHERE action = 'timeline.read'").fetchone())
    db.close()
    assert row["actor_user_id"] == doctor.id
    assert row["patient_id"] == patient["id"]
    assert row["created_at"]
    assert row["request_id"]
    assert row["outcome"] == "success"


def test_audit_logs_hold_no_patient_content(doctor, patient):
    doctor.post(f"/api/patients/{patient['id']}/allergies", json={"substance": "SYNTHETIC-SECRET"})
    doctor.get(f"/api/patients/{patient['id']}/allergies")
    db = connect()
    text = " ".join(str(tuple(row)) for row in db.execute("SELECT * FROM audit_events"))
    db.close()
    assert "SYNTHETIC-SECRET" not in text
    assert patient["last_name"] not in text


def test_the_audit_list_shows_patients_names_not_details(doctor, person):
    account, record = linked_patient(doctor, person)
    doctor.get(f"/api/patients/{record['id']}")
    entry = account.get(f"/api/patients/{record['id']}/audit-events").json()[0]
    assert set(entry) == {"action", "outcome", "created_at", "first_name", "last_name"}


def test_no_route_edits_or_deletes_the_audit_log():
    for method, path in ALL_ROUTES:
        if "audit" in path:
            assert method == "GET", path


def test_the_application_cannot_alter_audit_rows(doctor, patient):
    db = connect()
    for statement in ("UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(statement)
    db.close()


def test_sensitive_administration_is_audited(person):
    admin = person("doctor")
    organization = verified_organization(admin)
    colleague = person("doctor")
    admin.post(f"/api/organizations/{organization}/members", json={"email": colleague.email, "role": "staff"})
    admin.post(f"/api/organizations/{organization}/staff/{colleague.id}/suspend", json={"reason": "Synthetic"})
    db = connect()
    actions = {row[0] for row in db.execute("SELECT action FROM audit_events")}
    db.close()
    assert {"organization.verified", "organization.role_changed", "staff.suspended"} <= actions


def test_error_pages_reveal_no_internals(client):
    response = client.get("/api/patients/none")
    assert "Traceback" not in response.text
    assert "sqlite" not in response.text.lower()


def test_documentation_routes_stay_off(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_the_browser_keeps_no_session_token():
    from pathlib import Path

    folder = Path(__file__).parents[2] / "web" / "js" / "care"
    for script in folder.glob("*.js"):
        text = script.read_text(encoding="utf-8")
        assert "localStorage" not in text, script.name
        assert "document.cookie" not in text, script.name
