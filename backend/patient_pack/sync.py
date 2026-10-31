"""Copies finished records into each patient's Meko datapack.

A tool queues a reference in the same transaction as its change. This worker reads the record when
it sends, so a pack can always be rebuilt from SQLite, which stays the record. Consent is checked
here as well as when reading, so a patient who revokes it stops anything still in the queue.

Meko allows 60 requests a minute, so each pass does a little and never waits on indexing.
"""
import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import httpx

from backend.careflow import events
from backend.careflow.delivery import retry_delay
from backend.config import settings
from backend.db import connect
from backend.integrations import knowledge, meko
from backend.patient_pack import render
from backend.records import care
from backend.records.store import insert, now_iso, update

logger = logging.getLogger("diagnosia.patient_pack")

STORE_PURPOSE = "patient_pack"
SHARE_PURPOSE = "pack_sharing"
SENDS_PER_PASS = 1
CHECKS_PER_PASS = 1
ERASURES_PER_PASS = 1
FINISHED_STATES = {"completed": "ready", "failed": "failed", "abandoned": "failed", "oversized": "failed"}


class PackLimitReached(Exception):
    """Another datapack would pass the limit set for this account."""


def enqueue(db: sqlite3.Connection, patient_id: str, source_type: str, source_id: str) -> None:
    """A reference only, written with the change itself. It stays here until consent allows sending."""
    timestamp = now_iso()
    db.execute(
        """
        INSERT INTO pack_items (id, patient_id, source_type, source_id, next_attempt_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_type, source_id) DO UPDATE SET
            state = 'queued', attempts = 0, last_error = NULL, next_attempt_at = excluded.next_attempt_at, updated_at = excluded.updated_at
        """,
        (str(uuid.uuid4()), patient_id, source_type, str(source_id), timestamp, timestamp, timestamp),
    )


def request_erasure(db: sqlite3.Connection, patient_id: str) -> None:
    """Consent is gone, so the worker deletes the datapack. Access has already ended by then, because
    it's checked against the consent itself."""
    timestamp = now_iso()
    db.execute("UPDATE patient_packs SET state = 'erasing', updated_at = ? WHERE patient_id = ? AND datapack_id IS NOT NULL", (timestamp, patient_id))
    db.execute(
        """
        UPDATE pack_items SET state = 'queued', content_hash = NULL, document_key = NULL, document_id = NULL, attempts = 0,
            last_error = NULL, updated_at = ? WHERE patient_id = ?
        """,
        (timestamp, patient_id),
    )


def _pack(db: sqlite3.Connection, patient_id: str) -> Optional[sqlite3.Row]:
    return db.execute("SELECT * FROM patient_packs WHERE patient_id = ?", (patient_id,)).fetchone()


def _datapack_for(db: sqlite3.Connection, client: meko.MekoClient, patient_id: str) -> str:
    pack = _pack(db, patient_id)
    if pack is not None and pack["state"] == "active":
        return pack["datapack_id"]
    used = db.execute("SELECT COALESCE(SUM(datapacks_used), 0) FROM patient_packs").fetchone()[0]
    if used >= settings.meko_pack_limit:
        raise PackLimitReached()
    # The name reveals nothing, and only this table links it to a person.
    datapack_id = client.create_datapack(f"dgn-{uuid.uuid4().hex}")
    timestamp = now_iso()
    if pack is None:
        insert(db, "patient_packs", patient_id=patient_id, datapack_id=datapack_id, state="active", datapacks_used=1, created_at=timestamp, updated_at=timestamp)
    else:
        update(db, "patient_packs", pack["id"], datapack_id=datapack_id, state="active", datapacks_used=pack["datapacks_used"] + 1, updated_at=timestamp)
    db.commit()  # the slot is spent now, whatever happens next
    client.keep_conversations_out_of_search(datapack_id)
    return datapack_id


def _retry_later(db: sqlite3.Connection, item: sqlite3.Row, error: Exception, now: datetime) -> None:
    attempts = item["attempts"] + 1
    state = "failed" if attempts >= settings.event_max_attempts else item["state"]
    update(
        db, "pack_items", item["id"], attempts=attempts, state=state, last_error=type(error).__name__[:60],
        next_attempt_at=(now + timedelta(seconds=retry_delay(attempts))).isoformat(), updated_at=now.isoformat(),
    )
    db.commit()
    logger.warning("Patient pack item not sent: %s", type(error).__name__)


def _replace_old_copy(client: meko.MekoClient, datapack_id: str, item: sqlite3.Row) -> None:
    """A corrected record must not leave its old text searchable. Meko's status reply gives no document
    id, so it's found through the item's tag."""
    for hit in client.search(datapack_id, render.tag(item["id"]), 20):
        if isinstance(hit, dict) and item["id"] in render.tags_in(str(hit.get("chunk_text", ""))) and hit.get("document_id"):
            client.delete_document(hit["document_id"], datapack_id)
            return


def _send(db: sqlite3.Connection, client: meko.MekoClient, item: sqlite3.Row, post: Callable, now: datetime) -> None:
    text = render.render(db, item["id"], item["source_type"], item["source_id"])
    if text is None:  # not finished, or gone since it was queued
        update(db, "pack_items", item["id"], state="failed", last_error="record_unavailable", updated_at=now.isoformat())
        db.commit()
        return
    data = text.encode("utf-8")
    for label, pattern in knowledge.FORBIDDEN.items():
        if pattern.search(text):
            # Held for a person, not cleaned, the same as the shared knowledge files.
            update(db, "pack_items", item["id"], state="held", last_error=f"looks like a {label}"[:60], updated_at=now.isoformat())
            db.commit()
            return
    content_hash = hashlib.sha256(data).hexdigest()
    datapack_id = _datapack_for(db, client, item["patient_id"])
    if item["content_hash"] == content_hash:
        update(db, "pack_items", item["id"], state="ready", updated_at=now.isoformat())
        db.commit()
        return
    if item["content_hash"]:
        _replace_old_copy(client, datapack_id, item)
    filename = f"{item['id']}.md"
    reply = client.upload_request(filename, data, datapack_id)
    if reply.get("already_uploaded"):
        update(db, "pack_items", item["id"], state="ready", content_hash=content_hash, document_key=reply.get("key"), attempts=0, last_error=None, updated_at=now.isoformat())
        db.commit()
        return
    if not reply.get("url") or not reply.get("key"):
        raise meko.MekoUnavailable("upload reply had no URL or key")
    knowledge._send_file(reply["url"], reply, knowledge.Document(Path(filename), {}, data), post)
    update(db, "pack_items", item["id"], state="indexing", content_hash=content_hash, document_key=reply["key"], attempts=0, last_error=None, updated_at=now.isoformat())
    db.commit()


def _send_due(db: sqlite3.Connection, client: meko.MekoClient, post: Callable, now: datetime) -> int:
    due = db.execute(
        """
        SELECT pack_items.* FROM pack_items
        WHERE pack_items.state = 'queued' AND pack_items.next_attempt_at <= ?
          AND EXISTS (SELECT 1 FROM consents WHERE consents.patient_id = pack_items.patient_id
                      AND consents.purpose = ? AND consents.revoked_at IS NULL)
          AND NOT EXISTS (SELECT 1 FROM patient_packs WHERE patient_packs.patient_id = pack_items.patient_id
                          AND patient_packs.state = 'erasing')
        ORDER BY pack_items.next_attempt_at LIMIT ?
        """,
        (now.isoformat(), STORE_PURPOSE, SENDS_PER_PASS),
    ).fetchall()
    sent = 0
    for item in due:
        try:
            _send(db, client, item, post, now)
            sent += 1
        except PackLimitReached:
            update(db, "pack_items", item["id"], state="held", last_error="pack_limit_reached", updated_at=now.isoformat())
            db.commit()
        except (meko.MekoUnavailable, httpx.HTTPError) as error:
            _retry_later(db, item, error, now)
            break  # Meko is struggling, so what's behind this waits instead of failing too
    return sent


def _check_indexing(db: sqlite3.Connection, client: meko.MekoClient, now: datetime) -> None:
    waiting = db.execute(
        """
        SELECT pack_items.*, patient_packs.datapack_id FROM pack_items
        JOIN patient_packs ON patient_packs.patient_id = pack_items.patient_id
        WHERE pack_items.state = 'indexing' ORDER BY pack_items.updated_at LIMIT ?
        """,
        (CHECKS_PER_PASS,),
    ).fetchall()
    for item in waiting:
        try:
            status = client.document_status(item["document_key"], item["datapack_id"])
        except meko.MekoUnavailable as error:
            _retry_later(db, item, error, now)
            break
        state = FINISHED_STATES.get(status.get("state"))
        last_error = "indexing_failed" if state == "failed" else None
        update(db, "pack_items", item["id"], state=state or "indexing", last_error=last_error, updated_at=now.isoformat())
        db.commit()


def _erase_due(db: sqlite3.Connection, client: meko.MekoClient, now: datetime) -> None:
    due = db.execute("SELECT * FROM patient_packs WHERE state = 'erasing' LIMIT ?", (ERASURES_PER_PASS,)).fetchall()
    for pack in due:
        try:
            client.delete_datapack(pack["datapack_id"])
        except meko.MekoNotFound:
            pass  # already gone, which is what was wanted
        except meko.MekoUnavailable as error:
            logger.warning("Patient pack not erased yet: %s", type(error).__name__)  # stays erasing, and is tried again
            return
        update(db, "patient_packs", pack["id"], state="erased", datapack_id=None, updated_at=now.isoformat())
        db.commit()


def run_once(post: Callable = httpx.post, now: Optional[datetime] = None) -> Dict[str, int]:
    """One pass of the worker. Nothing is sent with real patient data, by design."""
    client = meko.pack_client()
    if client is None:
        return {}
    now = now or datetime.now(timezone.utc)
    db = connect()
    try:
        _erase_due(db, client, now)  # a patient's erasure is owed whatever else is switched off
        if not events.active():
            return {}
        _check_indexing(db, client, now)
        return {"sent": _send_due(db, client, post, now)}
    finally:
        db.close()


def rebuild(db: sqlite3.Connection, patient_id: str) -> int:
    """Queues everything again, to recover from a lost or failed pack. SQLite has it all."""
    return db.execute(
        """
        UPDATE pack_items SET state = 'queued', content_hash = NULL, attempts = 0, last_error = NULL, next_attempt_at = ?, updated_at = ?
        WHERE patient_id = ?
        """,
        (now_iso(), now_iso(), patient_id),
    ).rowcount


def has_storage_consent(db: sqlite3.Connection, patient_id: str) -> bool:
    return care.active_consent(db, patient_id, STORE_PURPOSE) is not None
