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
from backend.db import get_db

SESSION_COOKIE = "diagnosia_session"
SESSION_LIFETIME = timedelta(days=14)
BAD_LOGIN = "Incorrect email or password."

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
    }


def _start_session(db: sqlite3.Connection, response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now().isoformat(),))
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash_token(token), user_id, (_now() + SESSION_LIFETIME).isoformat()),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
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
        """,
        (_hash_token(diagnosia_session), _now().isoformat()),
    ).fetchone()


def require_user(user: Optional[sqlite3.Row] = Depends(current_user)) -> sqlite3.Row:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    return user


def require_doctor(user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    if user["role"] != "doctor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This tool is for doctors.")
    return user


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(
    details: Registration, response: Response, db: sqlite3.Connection = Depends(get_db)
) -> dict:
    try:
        cursor = db.execute(
            """
            INSERT INTO users (first_name, last_name, email, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                details.first_name.strip(),
                details.last_name.strip(),
                details.email.lower(),
                hasher.hash(details.password),
                details.role,
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists."
        ) from None
    _start_session(db, response, cursor.lastrowid)
    row = db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _public_user(row)


@router.post("/auth/login")
def login(
    credentials: Credentials, response: Response, db: sqlite3.Connection = Depends(get_db)
) -> dict:
    row = db.execute(
        "SELECT * FROM users WHERE email = ?", (credentials.email.lower(),)
    ).fetchone()
    try:
        hasher.verify(row["password_hash"] if row else DUMMY_HASH, credentials.password)
    except (VerificationError, InvalidHashError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN) from None
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    _start_session(db, response, row["id"])
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
