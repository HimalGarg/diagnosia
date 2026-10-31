"""Accounts and sessions: replaces the old PHP login.

Passwords are stored as Argon2 hashes. Sessions are random tokens kept in an
HttpOnly cookie, and only a hash of each token is stored, so a leaked database
can't be used to sign in.
"""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field

from backend.config import settings
from backend.db import connect, get_db

SESSION_COOKIE = "diagnosia_session"
SESSION_LIFETIME = timedelta(days=14)
STAFF_SESSION_LIFETIME = timedelta(hours=12)  # staff accounts reach many patients' records
BAD_LOGIN = "Incorrect email or password."
MAX_FAILED_LOGINS = 10
FAILED_LOGIN_WINDOW = timedelta(minutes=15)

router = APIRouter(prefix="/api")
hasher = PasswordHasher()
# Checked when an email isn't found, so unknown emails take as long as wrong passwords.
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(16))


class Registration(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Literal["doctor", "patient"]


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "email": row["email"],
        "role": row["role"],
        "account_status": row["account_status"],
    }


def _start_session(db: sqlite3.Connection, response: Response, user_id: int, role: str) -> None:
    token = secrets.token_urlsafe(32)
    lifetime = STAFF_SESSION_LIFETIME if role == "doctor" else SESSION_LIFETIME
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now().isoformat(),))
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash_token(token), user_id, (_now() + lifetime).isoformat()),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(lifetime.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def current_user(
    diagnosia_session: Optional[str] = Cookie(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> Optional[sqlite3.Row]:
    if not diagnosia_session:
        return None
    return db.execute(
        """
        SELECT users.* FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token_hash = ? AND sessions.expires_at > ?
          AND users.account_status != 'suspended'
        """,
        (_hash_token(diagnosia_session), _now().isoformat()),
    ).fetchone()


def require_user(user: Optional[sqlite3.Row] = Depends(current_user)) -> sqlite3.Row:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    return user


def require_staff_account(user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    """A staff sign-in that may not be verified yet: for onboarding steps only."""
    if user["role"] != "doctor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is for staff accounts.")
    return user


def require_doctor(user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    """Verified clinical staff only. A registered account isn't enough."""
    if user["role"] != "doctor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This tool is for doctors.")
    if user["account_status"] != "verified":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "staff_not_verified", "message": "An organization administrator must verify this account first."},
        )
    return user


def _email_key(email: str) -> str:
    return hashlib.sha256(email.encode()).hexdigest()


def _too_many_failures(db: sqlite3.Connection, email: str) -> bool:
    since = (_now() - FAILED_LOGIN_WINDOW).isoformat()
    count = db.execute(
        "SELECT COUNT(*) FROM login_failures WHERE email_hash = ? AND failed_at > ?", (_email_key(email), since)
    ).fetchone()[0]
    return count >= MAX_FAILED_LOGINS


def _remember_failure(email: str) -> None:
    """Uses its own connection, because the request's work is rolled back on a refusal."""
    connection = connect()
    try:
        connection.execute("DELETE FROM login_failures WHERE failed_at <= ?", ((_now() - FAILED_LOGIN_WINDOW).isoformat(),))
        connection.execute(
            "INSERT INTO login_failures (email_hash, failed_at) VALUES (?, ?)", (_email_key(email), _now().isoformat())
        )
        connection.commit()
    finally:
        connection.close()


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(
    details: Registration, response: Response, db: sqlite3.Connection = Depends(get_db)
) -> dict:
    try:
        cursor = db.execute(
            """
            INSERT INTO users (first_name, last_name, email, password_hash, role, account_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                details.first_name.strip(),
                details.last_name.strip(),
                details.email.lower(),
                hasher.hash(details.password),
                details.role,
                # A self-declared doctor is unverified until an administrator says otherwise.
                "active" if details.role == "patient" else "pending_verification",
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists."
        ) from None
    _start_session(db, response, cursor.lastrowid, details.role)
    row = db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _public_user(row)


@router.post("/auth/login")
def login(
    credentials: Credentials, response: Response, db: sqlite3.Connection = Depends(get_db)
) -> dict:
    email = credentials.email.lower()
    if _too_many_failures(db, email):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please try again in 15 minutes.")
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    try:
        hasher.verify(row["password_hash"] if row else DUMMY_HASH, credentials.password)
    except (VerificationError, InvalidHashError):
        _remember_failure(email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN) from None
    if row is None:
        _remember_failure(email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    if row["account_status"] == "suspended":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is suspended.")
    db.execute("DELETE FROM login_failures WHERE email_hash = ?", (_email_key(email),))
    _start_session(db, response, row["id"], row["role"])
    return _public_user(row)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    diagnosia_session: Optional[str] = Cookie(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> None:
    if diagnosia_session:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(diagnosia_session),))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(user: sqlite3.Row = Depends(require_user)) -> dict:
    return _public_user(user)
