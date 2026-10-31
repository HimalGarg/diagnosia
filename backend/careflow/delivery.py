"""Sends outbox events on to each destination.

Delivery is at least once, and every destination ignores repeats, so a retry can't create a
duplicate. A destination that is down delays its own channel only. After too many failures an event
is marked dead, stays visible, and can be replayed. Exactly-once across separate systems isn't
promised, because it isn't possible.
"""
import logging
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

from backend.careflow import stores
from backend.careflow.contract import ContractError
from backend.careflow.events import active, decode
from backend.config import settings
from backend.db import connect
from backend.integrations import execution, meko, yugabyte

logger = logging.getLogger("diagnosia.careflow")

MAX_BACKOFF_SECONDS = 3600
JITTER = 0.2  # so several failing deliveries don't all retry at the same moment
MIRROR_BATCH = 200
VALIDATION_FAILED = 121  # MongoDB's code for a document the schema refuses
# No tool hands anything to another today, so there is nothing to describe here. A tool that does
# adds the event's purpose, as fixed text.
HANDOFF_PURPOSE: dict = {}


def handoff_envelope(event: dict) -> dict:
    """Built only from allowlisted event fields and fixed text, so it can't carry clinical content."""
    return {
        "schema_version": "1.0",
        "handoff_id": f"handoff_{event['event_id'][4:]}",
        "correlation_id": event["correlation_id"],
        "source_module": event["module"],
        "target_module": event["target_module"],
        "workflow_ref": event["workflow_ref"],
        "created_at": event["occurred_at"],
        "purpose": HANDOFF_PURPOSE.get(event["event_type"], "Continue a synthetic workflow"),
        "summary": f"{event['module']} recorded {event['event_type']}. The receiving tool must re-read the source record.",
        "artifact_refs": [],
        "source_event_ids": [event["event_id"]],
        "human_review_required": True,
    }


def _context_of(db: sqlite3.Connection, event_id: str) -> Optional[str]:
    row = db.execute("SELECT result_ref FROM careflow_deliveries WHERE event_id = ? AND channel = 'meko'", (event_id,)).fetchone()
    return row["result_ref"] if row else None


def _send_to_yugabyte(db: sqlite3.Connection, event: dict) -> None:
    reference = _context_of(db, event["event_id"])
    yugabyte.store().save_handoff(handoff_envelope(event), "context_shared" if reference else "recorded", reference)


def _send_to_meko(event: dict) -> str:
    """Looks up what's relevant to the receiving tool, then notes the handoff. A failure is retried:
    it's never written down as if no context existed."""
    client = meko.client()
    handoff = handoff_envelope(event)
    found = client.knowledge(f"{handoff['source_module']} to {handoff['target_module']} handoff")
    if not found.available:
        raise meko.MekoUnavailable("knowledge search unavailable")
    return client.save_handoff(handoff, len(found.items))


def _senders(db: sqlite3.Connection) -> Dict[str, Callable[[dict], Optional[str]]]:
    """Only destinations that are switched on and configured. The rest keep their events waiting."""
    senders: Dict[str, Callable[[dict], Optional[str]]] = {}
    if settings.mongodb_enabled and settings.mongodb_uri:
        senders["mongodb"] = lambda event: stores.mongo_store().put(event) and None
    if settings.yugabyte_enabled and settings.yugabyte_dsn:
        senders["yugabyte"] = lambda event: _send_to_yugabyte(db, event)
    if meko.client() is not None:
        senders["meko"] = _send_to_meko
    return senders


def retry_delay(attempts: int) -> float:
    base = min(settings.event_backoff_seconds * 2 ** (attempts - 1), MAX_BACKOFF_SECONDS)
    return base * (1 + random.uniform(0, JITTER))


def _permanent(error: Exception) -> bool:
    """A failure that retrying can't fix. It's set aside now, so it can't hold up everything behind it."""
    return getattr(error, "code", None) == VALIDATION_FAILED or isinstance(error, (ContractError, meko.MekoUnsupported))


def deliver_channel(db: sqlite3.Connection, channel: str, send: Callable[[dict], Optional[str]], now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    due = db.execute(
        """
        SELECT deliveries.event_id, deliveries.attempts, outbox.event FROM careflow_deliveries AS deliveries
        JOIN careflow_outbox AS outbox ON outbox.event_id = deliveries.event_id
        WHERE deliveries.channel = ? AND deliveries.status = 'pending' AND deliveries.next_attempt_at <= ?
        ORDER BY outbox.occurred_at, deliveries.event_id LIMIT ?
        """,
        (channel, now.isoformat(), settings.event_batch_size),
    ).fetchall()
    outcome = {"delivered": 0, "retrying": 0, "dead": 0}
    for row in due:
        attempts = row["attempts"] + 1
        try:
            result = send(decode(row))
        except Exception as error:  # Any destination failure is the same to this loop. The category is what's kept.
            category = type(error).__name__[:60]
            permanent = _permanent(error)
            dead = permanent or attempts >= settings.event_max_attempts
            db.execute(
                """
                UPDATE careflow_deliveries SET attempts = ?, status = ?, next_attempt_at = ?, last_attempt_at = ?, last_error = ?, mirrored = 0
                WHERE event_id = ? AND channel = ?
                """,
                (attempts, "dead" if dead else "pending", (now + timedelta(seconds=retry_delay(attempts))).isoformat(), now.isoformat(), category, row["event_id"], channel),
            )
            db.commit()
            outcome["dead" if dead else "retrying"] += 1
            logger.warning("CareFlow delivery to %s failed for %s: %s", channel, row["event_id"], category)
            if permanent:
                continue
            break  # the destination is struggling, so later events wait instead of failing too
        db.execute(
            """
            UPDATE careflow_deliveries SET status = 'delivered', attempts = ?, delivered_at = ?, last_attempt_at = ?, last_error = NULL,
                result_ref = ?, mirrored = 0
            WHERE event_id = ? AND channel = ?
            """,
            (attempts, now.isoformat(), now.isoformat(), result if isinstance(result, str) else None, row["event_id"], channel),
        )
        if channel == "meko" and isinstance(result, str):
            # YugabyteDB's row for this handoff now has a context reference to record.
            db.execute(
                "UPDATE careflow_deliveries SET status = 'pending', next_attempt_at = ? WHERE event_id = ? AND channel = 'yugabyte' AND status = 'delivered'",
                (now.isoformat(), row["event_id"]),
            )
        db.commit()
        outcome["delivered"] += 1
    return outcome


def _send_runs() -> None:
    """Copies AI run records that are new or changed. They stay in the local file either way."""
    store = yugabyte.store()
    if store is None:
        return
    runs = execution.pending_sync(settings.event_batch_size)
    if not runs:
        return
    store.save_runs(runs)
    execution.mark_synced(runs)


def _mirror_states(db: sqlite3.Connection) -> None:
    """Copies delivery state to MongoDB's integration_delivery, for operators looking from that side."""
    store = stores.mongo_store()
    rows = db.execute(
        "SELECT * FROM careflow_deliveries WHERE mirrored = 0 AND attempts > 0 ORDER BY last_attempt_at LIMIT ?",
        (MIRROR_BATCH,),
    ).fetchall()
    for row in rows:
        store.record_delivery({
            "event_id": row["event_id"], "destination": row["channel"], "state": row["status"], "attempts": row["attempts"],
            "last_attempt_at": row["last_attempt_at"], "last_error": row["last_error"], "next_attempt_at": row["next_attempt_at"],
            "delivered_at": row["delivered_at"],
        })
    if rows:
        db.executemany("UPDATE careflow_deliveries SET mirrored = 1 WHERE event_id = ? AND channel = ?", [(row["event_id"], row["channel"]) for row in rows])
        db.commit()


def deliver_once() -> Dict[str, Dict[str, int]]:
    """One pass of the worker. Does nothing with real patient data, by design."""
    if not active():
        return {}
    results = {}
    db = connect()
    try:
        for channel, send in _senders(db).items():
            results[channel] = deliver_channel(db, channel, send)
        if results.get("mongodb", {}).get("retrying", 0) == 0 and "mongodb" in results:
            try:
                _mirror_states(db)
            except Exception as error:  # A view of the state, so losing it costs nothing that matters.
                logger.warning("Delivery state not copied to MongoDB: %s", type(error).__name__)
    finally:
        db.close()
    try:
        _send_runs()
    except Exception as error:  # They wait in the local file for the next pass.
        logger.warning("AI run records not sent to YugabyteDB: %s", type(error).__name__)
    return results


def replay_dead(db: sqlite3.Connection, channel: Optional[str] = None) -> int:
    """Puts failed deliveries back in the queue. Destinations ignore repeats, so this is always safe."""
    return db.execute(
        """
        UPDATE careflow_deliveries SET status = 'pending', attempts = 0, next_attempt_at = ?, mirrored = 0
        WHERE status = 'dead' AND (? IS NULL OR channel = ?)
        """,
        (datetime.now(timezone.utc).isoformat(), channel, channel),
    ).rowcount


def handoff_count(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(DISTINCT event_id) FROM careflow_deliveries WHERE channel = 'yugabyte'").fetchone()[0]


def backlog(db: sqlite3.Connection) -> Dict[str, Dict[str, int]]:
    found: Dict[str, Dict[str, int]] = {channel: {"pending": 0, "delivered": 0, "dead": 0} for channel in ("mongodb", "yugabyte", "meko")}
    for row in db.execute("SELECT channel, status, COUNT(*) AS total FROM careflow_deliveries GROUP BY channel, status"):
        found[row["channel"]][row["status"]] = row["total"]
    return found
