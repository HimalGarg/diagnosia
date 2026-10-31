"""Who saw or changed what. Audit rows can't be edited or deleted, by trigger."""
import logging
import sqlite3
from contextvars import ContextVar
from typing import Optional

from backend.db import connect
from backend.records.store import insert, new_id, now_iso

logger = logging.getLogger("diagnosia")
request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def record(
    db: sqlite3.Connection,
    user: Optional[sqlite3.Row],
    action: str,
    patient_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    outcome: str = "success",
    reason: str = "",
) -> None:
    insert(
        db,
        "audit_events",
        actor_user_id=user["id"] if user else None,
        action=action,
        patient_id=patient_id,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        reason=reason,
        request_id=request_id.get(),
        created_at=now_iso(),
    )


def record_denied(user: Optional[sqlite3.Row], action: str, patient_id: Optional[str], reason: str) -> None:
    """Uses its own connection, because the request's work is rolled back on a refusal."""
    connection = connect()
    try:
        record(connection, user, action, patient_id, outcome="denied", reason=reason)
        connection.commit()
    except sqlite3.Error as error:
        logger.warning("Could not record a refused request %s: %s", new_id(), error)
    finally:
        connection.close()
