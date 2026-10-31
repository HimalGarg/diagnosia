"""Technical records of each AI run: which provider and model answered, and how long it took.

A run is written when it begins and updated when it ends, so a crash leaves a visible unfinished
run rather than nothing. These records live in their own small SQLite file, for two reasons. A
request holds the main database's write lock while the AI works, so a separate write to that
database would wait on its own request. And nothing here is a clinical record. The delivery worker
copies new and changed runs to YugabyteDB when that's switched on. Otherwise they stay here.

Nothing here holds a prompt, an answer, reasoning, or any patient detail.
"""
import logging
import sqlite3
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.config import settings

logger = logging.getLogger("diagnosia.integrations")

BOOT_ID = uuid.uuid4().hex
BUSY_TIMEOUT_SECONDS = 2
TOOL_MODULES = {
    "clinscribe_note": "ClinScribe",
    "prescription_read": "Prescription Analyzer",
    "prescription_explain": "Prescription Analyzer",
    "prescription_plan": "Prescription Analyzer",
    "lablens_read": "LabLens",
    "diagnofast_turn": "DiagnoFast",
    "diagnofast_report": "DiagnoFast",
    "speech_to_text": "Speech",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    workflow_ref TEXT,
    module TEXT NOT NULL,
    tool TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
    provider TEXT,
    model TEXT,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    fallback_provider TEXT,
    fallback_model TEXT,
    error_category TEXT,
    trace_ref TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    boot_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    synced_at TEXT
);
CREATE INDEX IF NOT EXISTS agent_runs_unsynced ON agent_runs (synced_at, updated_at);
CREATE TABLE IF NOT EXISTS agent_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs (run_id),
    attempt_number INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    error_category TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    UNIQUE (run_id, attempt_number)
);
"""

last_model: ContextVar[Optional[Tuple[str, str]]] = ContextVar("last_model", default=None)
workflow_ref: ContextVar[Optional[str]] = ContextVar("workflow_ref", default=None)
request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def path() -> Path:
    return settings.db_path.with_name(f"{settings.db_path.stem}-execution.db")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(path(), timeout=BUSY_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(SCHEMA)
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Write:
    """A record that can't be written must never stop the AI call it describes."""

    def __enter__(self) -> Optional[sqlite3.Connection]:
        try:
            self.connection = connect()
        except sqlite3.Error as error:
            logger.warning("AI run record not written: %s", type(error).__name__)
            self.connection = None
        return self.connection

    def __exit__(self, kind, error, _trace) -> bool:
        if self.connection is not None:
            if kind is None:
                self.connection.commit()
            self.connection.close()
        if isinstance(error, sqlite3.Error):
            logger.warning("AI run record not written: %s", type(error).__name__)
            return True
        return False


class AgentRun:
    def __init__(self, tool: str):
        self.tool = tool
        self.run_id = f"run_{uuid.uuid4().hex}"
        self.started_at = _now()
        self.started = time.monotonic()
        self.clock = self.started
        self.attempts = 0
        self.first: Optional[Tuple[str, str]] = None
        self.last: Optional[Tuple[str, str]] = None
        self.error_category: Optional[str] = None
        with _Write() as db:
            if db is not None:
                db.execute(
                    """
                    INSERT INTO agent_runs (run_id, workflow_ref, module, tool, status, trace_ref, started_at, boot_id, updated_at)
                    VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)
                    """,
                    (self.run_id, workflow_ref.get(), TOOL_MODULES.get(tool, tool), tool, request_id.get(), self.started_at, BOOT_ID, self.started_at),
                )

    def tried(self, provider: str, model: str, outcome: str) -> None:
        finished = _now()
        duration = int((time.monotonic() - self.clock) * 1000)
        self.attempts += 1
        self.clock = time.monotonic()
        self.first = self.first or (provider, model)
        self.last = (provider, model)
        succeeded = outcome == "succeeded"
        self.error_category = None if succeeded else outcome[:60]
        if succeeded:
            last_model.set((provider, model))
        with _Write() as db:
            if db is not None:
                db.execute(
                    """
                    INSERT OR IGNORE INTO agent_attempts
                        (attempt_id, run_id, attempt_number, provider, model, status, error_category, started_at, finished_at, duration_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (f"{self.run_id}:{self.attempts}", self.run_id, self.attempts, provider, model, "succeeded" if succeeded else "failed", self.error_category, self.started_at, finished, duration),
                )

    def finish(self, status: str) -> None:
        finished = _now()
        fell_back = self.first is not None and self.last != self.first
        with _Write() as db:
            if db is not None:
                db.execute(
                    """
                    UPDATE agent_runs SET status = ?, provider = ?, model = ?, fallback_used = ?, fallback_provider = ?, fallback_model = ?,
                        error_category = ?, attempt_count = ?, finished_at = ?, duration_ms = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        status, self.first[0] if self.first else None, self.first[1] if self.first else None, int(fell_back),
                        self.last[0] if fell_back else None, self.last[1] if fell_back else None, self.error_category, self.attempts,
                        finished, int((time.monotonic() - self.started) * 1000), finished, self.run_id,
                    ),
                )


def recover_interrupted() -> int:
    """Runs left unfinished by an earlier process can't finish now, so they're marked, not lost."""
    timestamp = _now()
    with _Write() as db:
        if db is None:
            return 0
        return db.execute(
            """
            UPDATE agent_runs SET status = 'interrupted', error_category = 'interrupted', finished_at = ?, updated_at = ?
            WHERE status = 'running' AND boot_id != ?
            """,
            (timestamp, timestamp, BOOT_ID),
        ).rowcount
    return 0


def pending_sync(limit: int) -> List[Dict]:
    """Runs that are new or have changed since they were last copied, each with its attempts."""
    with _Write() as db:
        if db is None:
            return []
        rows = db.execute(
            "SELECT * FROM agent_runs WHERE synced_at IS NULL OR synced_at < updated_at ORDER BY updated_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {**dict(row), "attempts": [dict(item) for item in db.execute("SELECT * FROM agent_attempts WHERE run_id = ? ORDER BY attempt_number", (row["run_id"],))]}
            for row in rows
        ]
    return []


def mark_synced(runs: List[Dict]) -> None:
    """Only as far as what was sent, so a change made during the send is copied next time."""
    with _Write() as db:
        if db is not None:
            db.executemany("UPDATE agent_runs SET synced_at = ? WHERE run_id = ?", [(run["updated_at"], run["run_id"]) for run in runs])


def waiting() -> int:
    with _Write() as db:
        if db is None:
            return 0
        return db.execute("SELECT COUNT(*) FROM agent_runs WHERE synced_at IS NULL OR synced_at < updated_at").fetchone()[0]
    return 0


def summary() -> Dict[str, object]:
    """The local answer to the operational questions, when YugabyteDB isn't connected."""
    with _Write() as db:
        if db is None:
            return {}
        row = db.execute(
            """
            SELECT COUNT(*) AS total_runs,
                   COALESCE(SUM(status = 'succeeded'), 0) AS successful_runs,
                   COALESCE(SUM(status = 'failed'), 0) AS failed_runs,
                   COALESCE(SUM(status = 'interrupted'), 0) AS interrupted_runs,
                   COALESCE(SUM(status = 'running'), 0) AS running_runs,
                   COALESCE(SUM(fallback_used), 0) AS fallback_runs,
                   AVG(duration_ms) AS average_duration_ms,
                   COALESCE(SUM(MAX(attempt_count - 1, 0)), 0) AS retry_count
            FROM agent_runs
            """
        ).fetchone()
        return dict(row)
    return {}
