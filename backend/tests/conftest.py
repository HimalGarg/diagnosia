import itertools
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend.app import app
from backend.config import settings
from backend.db import connect
from backend.prescription import analyzer
from backend.private_files import WINDOWS, make_private
from backend.records import staff, vault


@pytest.fixture(autouse=True)
def no_real_ai_keys(request, monkeypatch, tmp_path):
    """A developer's .env may hold real keys. Tests use fakes, and must never reach a real service.

    Tests marked `live` or `local_stack` are the exception, and only run on request."""
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("local_stack"):
        return
    monkeypatch.setattr(settings, "db_path", tmp_path / "isolated.db")  # no test touches the real AI run records
    for switch in ("mongodb_enabled", "yugabyte_enabled", "meko_enabled", "mongodb_change_streams"):
        monkeypatch.setattr(settings, switch, False)  # nor a service switched on in a developer's .env
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "")


@pytest.fixture(scope="session")
def key_file(tmp_path_factory):
    """A test key ring, kept in its own folder like a real one."""
    path = tmp_path_factory.mktemp("keys") / "document-keys.json"
    path.write_text(json.dumps({"active": "test1", "keys": {"test1": vault.new_key()}}))
    make_private(path)
    return path


@pytest.fixture
def expose():
    """Opens a file or folder to every user, using each system's real permissions."""

    def open_up(path):
        if not WINDOWS:
            path.chmod(0o777 if path.is_dir() else 0o644)
            return
        rights = "(OI)(CI)R" if path.is_dir() else "R"
        subprocess.run(["icacls", str(path), "/grant", f"*S-1-1-0:{rights}"], check=True, capture_output=True)

    return open_up


@pytest.fixture
def client(tmp_path, monkeypatch, key_file):
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    monkeypatch.setattr(settings, "documents_dir", tmp_path / "documents")
    monkeypatch.setattr(settings, "document_key_file", str(key_file))
    # The real medicine list is slow to load, and tests shouldn't depend on it.
    monkeypatch.setattr(app_module, "get_medicine_list", lambda: None)
    monkeypatch.setattr(analyzer, "check_name", lambda name: {"status": "unchecked"})
    with TestClient(app) as test_client:
        yield test_client


def verify_staff(user_id, profession="doctor"):
    """Stands in for a human verification, through the same code a real one uses."""
    db = connect()
    try:
        staff.record_decision(
            db, user_id, "verified", None, "test operator", evidence_type="synthetic test",
            evidence_reference="synthetic", verified_profession=profession,
        )
        db.commit()
    finally:
        db.close()


def verify_email(email, profession="doctor"):
    db = connect()
    user_id = db.execute("SELECT id FROM users WHERE email = ?", (email.lower(),)).fetchone()["id"]
    db.close()
    verify_staff(user_id, profession)


def verified_organization(admin, name="Synthetic Hospital", kind="hospital"):
    """An organization an operator has vouched for, with this person as its administrator."""
    db = connect()
    try:
        organization_id = staff.operator_verify_organization(
            db, admin.email, name, kind, "synthetic test", "synthetic", "test operator"
        )
        db.commit()
    finally:
        db.close()
    return organization_id


@pytest.fixture
def details():
    return {
        "first_name": "Asha",
        "last_name": "Rao",
        "email": "asha@example.com",
        "password": "a-long-password",
        "role": "doctor",
    }


class Person:
    """A signed-in browser, so tests can act as several people at once."""

    def __init__(self, role, first_name, last_name, email, verified=True, profession="doctor"):
        self.http = TestClient(app, raise_server_exceptions=True)
        response = self.http.post(
            "/api/auth/register",
            json={
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "password": "a-long-password",
                "role": role,
            },
        )
        assert response.status_code == 201
        self.id = response.json()["id"]
        self.email = email
        if role == "doctor" and verified:
            verify_staff(self.id, profession)

    def __getattr__(self, name):
        return getattr(self.http, name)


@pytest.fixture
def person(client):
    numbers = itertools.count(1)

    def make(role="doctor", first_name="Test", last_name="Person", **options):
        return Person(role, first_name, last_name, f"person{next(numbers)}@example.com", **options)

    return make


@pytest.fixture
def doctor(person):
    return person("doctor", "Meera", "Iyer")


@pytest.fixture
def patient(doctor):
    """A record created by the doctor, with no patient account attached."""
    response = doctor.post(
        "/api/patients",
        json={"first_name": "Ravi", "last_name": "Kumar", "date_of_birth": "1970-05-01", "sex": "male"},
    )
    assert response.status_code == 201
    return response.json()


def grant(client, patient_id, purpose):
    response = client.post(f"/api/patients/{patient_id}/consents", json={"purpose": purpose})
    assert response.status_code == 201
    return response.json()


def linked_patient(doctor, person, first_name="Ravi", last_name="Kumar"):
    """A patient record claimed by a patient account. Returns the account and the record."""
    record = doctor.post("/api/patients", json={"first_name": first_name, "last_name": last_name}).json()
    code = doctor.post(f"/api/patients/{record['id']}/share-codes", json={"purpose": "claim"}).json()["code"]
    account = person("patient", first_name, last_name)
    assert account.post("/api/patients/join", json={"code": code}).status_code == 200
    return account, record


class FakeAI:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, system, content, schema, **options):
        self.calls.append({"system": system, "content": content, "schema": schema, **options})
        return self.result
