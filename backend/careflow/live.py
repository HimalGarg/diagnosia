"""Live CareFlow updates from MongoDB Change Streams, when a deployment supports them.

A background thread watches for new events and tells open CareFlow screens to reload. The message
says only that something changed. The screen then asks again through the normal route, which checks
access, so this can't reveal anything. Every screen also refreshes on a timer, so losing this
changes how quickly an update appears, not whether it does. That timer is a fallback. It is not a
change stream, and the status reported here says which one is working.

The resume token is saved after each change, so a restart carries on from the same place.
"""
import asyncio
import json
import logging
import random
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Deque, Dict, Optional, Set, Tuple

from backend.careflow import stores
from backend.config import settings
from backend.db import connect

logger = logging.getLogger("diagnosia.careflow")

KEEP_RECENT = 500
WAIT_MS = 1000
FIRST_WAIT_SECONDS = 1
LONGEST_WAIT_SECONDS = 60
CHECK_SECONDS = 1
HEARTBEAT_CHECKS = 20
STREAM_NAME = "care_events"
STOP_TIMEOUT_SECONDS = 5
# MongoDB's codes for a position that can no longer be resumed from.
STALE_TOKEN_CODES = {260, 280, 286}


class Feed:
    """The newest changes, numbered, so each open screen can catch up from where it was. An event
    seen twice, as can happen after resuming, is only announced once."""

    def __init__(self) -> None:
        self.version = 0
        self.recent: Deque[Tuple[int, str]] = deque(maxlen=KEEP_RECENT)
        self.announced: Deque[str] = deque(maxlen=KEEP_RECENT)
        self.lock = threading.Lock()

    def publish(self, workflow_ref: str, event_id: Optional[str] = None) -> bool:
        with self.lock:
            if event_id is not None and event_id in self.announced:
                return False
            if event_id is not None:
                self.announced.append(event_id)
            self.version += 1
            self.recent.append((self.version, workflow_ref))
            return True

    def since(self, seen: int) -> Tuple[int, Set[str]]:
        with self.lock:
            return self.version, {reference for number, reference in self.recent if number > seen}


feed = Feed()


@dataclass
class StreamStatus:
    running: bool = False
    connected: bool = False
    resumed: bool = False
    changes_seen: int = 0
    reconnects: int = 0
    last_change_at: Optional[str] = None
    last_error: Optional[str] = None
    collection: str = field(default_factory=lambda: settings.mongodb_change_stream_collection)

    def view(self) -> Dict[str, object]:
        return dict(self.__dict__)


status = StreamStatus()


def load_token() -> Optional[dict]:
    db = connect()
    try:
        row = db.execute("SELECT resume_token FROM careflow_stream_state WHERE name = ?", (STREAM_NAME,)).fetchone()
        return json.loads(row["resume_token"]) if row else None
    except (sqlite3.Error, ValueError):
        return None
    finally:
        db.close()


def save_token(token: Optional[dict]) -> None:
    """A lost token costs a screen one timer refresh, so a failure to save is only logged."""
    db = connect()
    try:
        if token is None:
            db.execute("DELETE FROM careflow_stream_state WHERE name = ?", (STREAM_NAME,))
        else:
            db.execute(
                """
                INSERT INTO careflow_stream_state (name, resume_token, updated_at) VALUES (?, ?, ?)
                ON CONFLICT (name) DO UPDATE SET resume_token = excluded.resume_token, updated_at = excluded.updated_at
                """,
                (STREAM_NAME, json.dumps(dict(token)), datetime.now(timezone.utc).isoformat()),
            )
        db.commit()
    except sqlite3.Error as error:
        logger.warning("Change stream position not saved: %s", type(error).__name__)
    finally:
        db.close()


def _collection(store):
    return store.database[settings.mongodb_change_stream_collection]


def watch(stop: threading.Event, store=None, on_error=None) -> None:
    """Runs until stopped. A lost connection resumes from the last token, or starts fresh if it's stale."""
    token = load_token()
    status.running = True
    wait = FIRST_WAIT_SECONDS
    while not stop.is_set():
        opened = False
        try:
            target = store or stores.mongo_store()
            with _collection(target).watch([{"$match": {"operationType": "insert"}}], resume_after=token, max_await_time_ms=WAIT_MS) as stream:
                opened = True
                status.connected = True
                status.resumed = token is not None
                status.last_error = None
                wait = FIRST_WAIT_SECONDS
                while stream.alive and not stop.is_set():
                    change = stream.try_next()
                    if change is None:
                        continue
                    token = stream.resume_token
                    save_token(token)
                    document = change["fullDocument"]
                    status.changes_seen += 1
                    status.last_change_at = datetime.now(timezone.utc).isoformat()
                    feed.publish(document["workflow_ref"], str(document["_id"]))
        except Exception as error:  # Any failure means the same: wait, then reconnect. Timers still refresh screens.
            status.connected = False
            status.reconnects += 1
            status.last_error = type(error).__name__[:60]
            # A server that refuses to open from this position will refuse it every time, whatever
            # the reason it gives. A network error has no code, and keeps the position.
            refused_to_open = not opened and token is not None and getattr(error, "code", None) is not None
            if refused_to_open or getattr(error, "code", None) in STALE_TOKEN_CODES:
                token = None
                save_token(None)
            logger.warning("Change stream interrupted: %s", type(error).__name__)
            if on_error:
                on_error(error)
            stop.wait(wait * (1 + random.uniform(0, 0.2)))
            wait = min(wait * 2, LONGEST_WAIT_SECONDS)
    status.connected = False
    status.running = False


class Watcher:
    def __init__(self, thread: threading.Thread, stop: threading.Event):
        self.thread = thread
        self.stop_event = stop

    def stop(self) -> None:
        """Closes the stream, and doesn't leave the thread running past shutdown."""
        self.stop_event.set()
        self.thread.join(STOP_TIMEOUT_SECONDS)


def start() -> Optional[Watcher]:
    if not (settings.mongodb_change_streams and settings.mongodb_enabled and settings.mongodb_uri):
        return None
    stop = threading.Event()
    thread = threading.Thread(target=watch, args=(stop,), name="careflow-change-stream", daemon=True)
    thread.start()
    return Watcher(thread, stop)


async def updates(allowed: Set[str], source: Feed = feed, checks: Optional[int] = None) -> AsyncIterator[str]:
    """Server-sent events for one screen. `checks` limits the loop, for tests."""
    seen = source.version
    idle = 0
    done = 0
    while checks is None or done < checks:
        done += 1
        await asyncio.sleep(CHECK_SECONDS if checks is None else 0)
        seen, changed = source.since(seen)
        if changed & allowed:
            idle = 0
            yield f"data: {json.dumps({'changed': True})}\n\n"
            continue
        idle += 1
        if idle >= HEARTBEAT_CHECKS:
            idle = 0
            yield ": still here\n\n"
