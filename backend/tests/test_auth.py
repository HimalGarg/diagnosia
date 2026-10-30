import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend.auth import SESSION_COOKIE, require_doctor
from backend.config import settings


def stored_rows(table):
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        connection.close()


def test_register_signs_the_user_in(client, details):
    response = client.post("/api/auth/register", json=details)
    assert response.status_code == 201
    assert "password" not in str(response.json())
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == "asha@example.com"
    assert me.json()["role"] == "doctor"


def test_session_cookie_is_http_only(client, details):
    response = client.post("/api/auth/register", json=details)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_password_is_stored_as_argon2_hash(client, details):
    client.post("/api/auth/register", json=details)
    stored = stored_rows("users")[0]["password_hash"]
    assert stored.startswith("$argon2")
    assert details["password"] not in stored


def test_session_token_is_not_stored_in_the_database(client, details):
    client.post("/api/auth/register", json=details)
    token = client.cookies[SESSION_COOKIE]
    assert stored_rows("sessions")[0]["token_hash"] != token


def test_login_and_logout(client, details):
    client.post("/api/auth/register", json=details)
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401

    response = client.post(
        "/api/auth/login", json={"email": details["email"], "password": details["password"]}
    )
    assert response.status_code == 200
    assert client.get("/api/me").status_code == 200


def test_logout_ends_the_session_on_the_server(client, details):
    client.post("/api/auth/register", json=details)
    token = client.cookies[SESSION_COOKIE]
    client.post("/api/auth/logout")
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/me").status_code == 401


def test_wrong_password_and_unknown_email_look_the_same(client, details):
    client.post("/api/auth/register", json=details)
    client.post("/api/auth/logout")
    wrong = client.post(
        "/api/auth/login", json={"email": details["email"], "password": "not-the-password"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "not-the-password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_email_is_not_case_sensitive(client, details):
    client.post("/api/auth/register", json=details)
    duplicate = client.post("/api/auth/register", json={**details, "email": "ASHA@example.com"})
    assert duplicate.status_code == 409
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login", json={"email": "Asha@Example.com", "password": details["password"]}
    )
    assert response.status_code == 200


def test_expired_session_is_rejected(client, details):
    client.post("/api/auth/register", json=details)
    connection = sqlite3.connect(settings.db_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    connection.execute("UPDATE sessions SET expires_at = ?", (past,))
    connection.commit()
    connection.close()
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"password": "short"},
        {"email": "not-an-email"},
        {"role": "admin"},
        {"first_name": ""},
    ],
)
def test_invalid_details_are_rejected(client, details, change):
    assert client.post("/api/auth/register", json={**details, **change}).status_code == 422
    assert stored_rows("users") == []


@pytest.mark.parametrize(
    "password",
    ["' OR '1'='1", "' OR 1=1 --", "x' UNION SELECT * FROM users --", "'; DROP TABLE users; --"],
)
def test_sql_injection_in_password_does_not_log_in(client, details, password):
    client.post("/api/auth/register", json=details)
    client.post("/api/auth/logout")
    response = client.post("/api/auth/login", json={"email": details["email"], "password": password})
    assert response.status_code == 401
    assert len(stored_rows("users")) == 1


@pytest.mark.parametrize(
    "email", ["' OR 1=1 --@example.com", "asha@example.com' --", "' UNION SELECT 1 --@example.com"]
)
def test_sql_injection_in_email_does_not_log_in(client, details, email):
    client.post("/api/auth/register", json=details)
    client.post("/api/auth/logout")
    response = client.post("/api/auth/login", json={"email": email, "password": details["password"]})
    assert response.status_code in (401, 422)
    assert client.get("/api/me").status_code == 401


def test_sql_injection_in_name_is_stored_as_plain_text(client, details):
    name = "Robert'); DROP TABLE users; --"
    assert client.post("/api/auth/register", json={**details, "first_name": name}).status_code == 201
    assert stored_rows("users")[0]["first_name"] == name


def test_require_doctor_refuses_patients():
    doctor = {"role": "doctor"}
    assert require_doctor(doctor) is doctor
    with pytest.raises(HTTPException) as refused:
        require_doctor({"role": "patient"})
    assert refused.value.status_code == 403
