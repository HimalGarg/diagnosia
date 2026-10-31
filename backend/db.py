"""SQLite storage. Every query uses parameters, never string building."""
import sqlite3
from pathlib import Path
from typing import Iterator

from backend import migrations
from backend.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('doctor', 'patient')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intakes (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    doctor_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    form_id TEXT NOT NULL,
    patient_name TEXT NOT NULL,
    patient_age INTEGER,
    patient_sex TEXT,
    abha_id TEXT,
    language TEXT,
    status TEXT NOT NULL DEFAULT 'waiting',
    answers TEXT NOT NULL DEFAULT '{}',
    transcript TEXT NOT NULL DEFAULT '[]',
    urgent TEXT NOT NULL DEFAULT '[]',
    records TEXT,
    report TEXT,
    current_question TEXT,
    questions_asked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI may run a dependency and its route on different worker threads.
    connection = sqlite3.connect(settings.db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    connection = connect()
    try:
        connection.execute("PRAGMA journal_mode = WAL")  # readers don't wait for a writer
        connection.executescript(SCHEMA)
        # Each tool keeps its own tables next to its code.
        for schema in sorted(Path(__file__).parent.glob("*/schema.sql")):
            connection.executescript(schema.read_text(encoding="utf-8"))
        migrations.run(connection, settings.db_path)
    finally:
        connection.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, committed on success."""
    connection = connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()
