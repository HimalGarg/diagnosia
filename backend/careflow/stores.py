"""Where the timeline is read from: MongoDB Atlas, or the local outbox when Atlas isn't connected.

Both give the same answers. The local store exists so the app still works without credentials, and
every response says which one it came from. Neither is a clinical record.
"""
import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from backend.careflow import contract
from backend.careflow.events import decode
from backend.config import settings

logger = logging.getLogger("diagnosia.careflow")

MAX_TIMELINE = 500
MAX_RECENT = 200
MAX_PROJECTED_EVENTS = 200
ATLAS_HOST = re.compile(r"\.mongodb(-dev|-qa|-stage)?\.net$", re.IGNORECASE)
EVENT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["schema_version", "event_type", "module", "workflow_stage", "source_operation", "workflow_ref", "tenant_ref", "occurred_at", "status", "data_classification"],
        "properties": {
            "schema_version": {"enum": list(contract.ACCEPTED_VERSIONS)},
            "module": {"enum": sorted(contract.MODULES)},
            "workflow_stage": {"enum": sorted(set(contract.WORKFLOW_STAGES.values()))},
            "source_operation": {"bsonType": "string", "maxLength": 80},
            "workflow_ref": {"bsonType": "string", "pattern": "^case_[a-z0-9_]{3,40}$"},
            "tenant_ref": {"bsonType": "string", "pattern": "^tenant_[a-z0-9_]{3,40}$"},
            "occurred_at": {"bsonType": "date"},
            "status": {"enum": ["started", "completed", "review_required", "failed"]},
            "data_classification": {"enum": ["synthetic_demo"]},
            "metadata": {"bsonType": "object"},
        },
    }
}
# What settles a request for review. A workflow's waiting count comes from these, in event order.
SETTLED_BY = {
    "note_draft.created": {"note_draft.approved", "note_draft.discarded"},
    "lab.review_required": {"lab.report_confirmed"},
    "safety_check.review_required": {"safety_check.finalised"},
    "care_plan.created": {"care_plan.approved"},
    "maternal.concern_raised": {"maternal.concern_responded"},
    "referral.outcome_returned": {"referral.reviewed"},
    "referral.needs_information": {"referral.accepted", "referral.declined", "referral.redirected"},
    "amr_analysis.review_required": {"amr_analysis.approved"},
    "prescription.confirmation_required": {"prescription.explained"},
}


class EventStore(Protocol):
    name: str

    def timeline(self, workflow_ref: str) -> List[dict]: ...

    def recent(self, workflow_refs: Sequence[str], module: Optional[str], status: Optional[str], start: Optional[str], end: Optional[str]) -> List[dict]: ...

    def summary(self, workflow_refs: Sequence[str]) -> Dict[str, int]: ...


def _matches(event: dict, module: Optional[str], status: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    return (
        (not module or event["module"] == module)
        and (not status or event["status"] == status)
        and (not start or event["occurred_at"] >= start)
        and (not end or event["occurred_at"] <= end)
    )


class LocalStore:
    name = "local_outbox"

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def _events(self, workflow_refs: Sequence[str], limit: int, newest_first: bool) -> List[dict]:
        rows = self.db.execute(
            """
            SELECT event FROM careflow_outbox WHERE workflow_ref IN (SELECT value FROM json_each(?))
            ORDER BY CASE WHEN ? THEN occurred_at END DESC, CASE WHEN ? THEN event_id END DESC, occurred_at, event_id LIMIT ?
            """,
            (json.dumps(list(workflow_refs)), newest_first, newest_first, limit),
        )
        return [decode(row) for row in rows]

    def timeline(self, workflow_ref: str) -> List[dict]:
        return self._events([workflow_ref], MAX_TIMELINE, False)

    def recent(self, workflow_refs, module, status, start, end) -> List[dict]:
        found = self._events(workflow_refs, MAX_TIMELINE, True)
        return [event for event in found if _matches(event, module, status, start, end)][:MAX_RECENT]

    def summary(self, workflow_refs) -> Dict[str, int]:
        return _count(self._events(workflow_refs, MAX_TIMELINE * 4, True))


def _count(events: List[dict]) -> Dict[str, int]:
    return {
        "total": len(events),
        "completed": sum(event["status"] == "completed" for event in events),
        "failed": sum(event["status"] == "failed" for event in events),
        "review_required": sum(event["status"] == "review_required" for event in events),
    }


def waiting_for_review(events: List[dict]) -> int:
    """Requests for review with nothing yet recorded that settles them. Events must be in order."""
    waiting: Dict[str, int] = {}
    for event in events:
        kind = event["event_type"]
        if kind in SETTLED_BY:
            waiting[kind] = waiting.get(kind, 0) + 1
        for asked, settled in SETTLED_BY.items():
            if kind in settled and waiting.get(asked):
                waiting[asked] -= 1
    return sum(waiting.values())


def project(workflow_ref: str, events: List[dict]) -> dict:
    """The prepared view of one workflow. Derived entirely from events, so it can always be rebuilt.

    `activity_status` describes recorded activity. It's never the official state of anything."""
    last = events[-1]
    counts = _count(events)
    pending = waiting_for_review(events)
    status = "failed" if last["status"] == "failed" else "waiting_for_review" if pending else "active"
    return {
        "_id": workflow_ref,
        "projection_note": "Activity view only. The Diagnosia database is the record.",
        "tenant_ref": last["tenant_ref"],
        "last_activity_at": _as_date(last["occurred_at"]),
        "latest_activity": last["event_type"],
        "activity_status": status,
        "event_count": counts["total"],
        "failed_count": counts["failed"],
        "review_required_count": counts["review_required"],
        "pending_review_count": pending,
        "modules": sorted({event["module"] for event in events}),
        "events": [
            {key: event.get(key) for key in ("event_id", "event_type", "module", "status", "occurred_at", "target_module")}
            for event in events[-MAX_PROJECTED_EVENTS:]
        ],
        "projected_at": datetime.now(timezone.utc),
    }


class MongoEventStore:
    """The single boundary to MongoDB. `database` can be any object with PyMongo's collection methods."""

    def __init__(self, database, name: str = "mongodb_local"):
        self.name = name
        self.events = database["care_events"]
        self.projections = database["timeline_projections"]
        self.deliveries = database["integration_delivery"]
        self.database = database

    def bootstrap(self) -> None:
        """Safe to repeat. A validator is applied to an existing collection too, so setup can't drift."""
        existing = set(self.database.list_collection_names())
        if "care_events" in existing:
            self.database.command("collMod", "care_events", validator=EVENT_VALIDATOR)
        else:
            self.database.create_collection("care_events", validator=EVENT_VALIDATOR)
        self.events.create_index([("workflow_ref", 1), ("occurred_at", 1)])
        self.events.create_index([("tenant_ref", 1), ("occurred_at", -1)])
        self.events.create_index([("module", 1), ("occurred_at", -1)])
        self.events.create_index([("status", 1), ("occurred_at", -1)])
        self.projections.create_index([("tenant_ref", 1), ("last_activity_at", -1)])
        self.deliveries.create_index([("state", 1), ("next_attempt_at", 1)])
        self.deliveries.create_index([("destination", 1), ("state", 1)])

    def ping(self) -> None:
        self.database.command("ping")

    def put(self, event: dict) -> bool:
        """Stores an event once. Returns False if it was already there, which isn't a failure."""
        document = {key: value for key, value in event.items() if key != "event_id"}
        document["_id"] = event["event_id"]
        document["occurred_at"] = _as_date(event["occurred_at"])
        document["recorded_at"] = datetime.now(timezone.utc)
        new = True
        try:
            self.events.insert_one(document)
        except Exception as error:  # Named here so the app needs no MongoDB driver unless this is on.
            if type(error).__name__ != "DuplicateKeyError":
                raise
            new = False
        # Rebuilt from the stored events rather than incremented, so a retry or an event arriving
        # out of order can't leave the projection wrong. This also repairs a crash between the two writes.
        self._rebuild(event["workflow_ref"])
        return new

    def _rebuild(self, workflow_ref: str) -> None:
        stored = list(self.events.find({"workflow_ref": workflow_ref}).sort([("occurred_at", 1), ("_id", 1)]).limit(MAX_TIMELINE))
        if stored:
            self.projections.replace_one({"_id": workflow_ref}, project(workflow_ref, [_from_document(document) for document in stored]), upsert=True)

    def record_delivery(self, delivery: dict) -> None:
        """Where each event has reached. SQLite decides what's sent next, and this is the view of it."""
        self.deliveries.replace_one({"_id": f"{delivery['event_id']}:{delivery['destination']}"}, delivery, upsert=True)

    def verify(self) -> List[Tuple[str, bool, str]]:
        """Every step of the health check, using one synthetic event that is removed afterwards."""
        checks: List[Tuple[str, bool, str]] = []
        self.database.command("ping")
        checks.append(("server responds", True, "ping answered"))
        names = set(self.database.list_collection_names())
        checks.append(("database accessible", True, f"{len(names)} collections visible"))
        self.bootstrap()
        names = set(self.database.list_collection_names())
        checks.append(("collections exist", {"care_events"} <= names, ", ".join(sorted(names))))
        indexes = {index["name"] for index in self.events.list_indexes()}
        wanted = {"workflow_ref_1_occurred_at_1", "tenant_ref_1_occurred_at_-1", "module_1_occurred_at_-1", "status_1_occurred_at_-1"}
        checks.append(("indexes exist", wanted <= indexes, f"{len(wanted & indexes)} of {len(wanted)}"))
        options = self.events.options()
        checks.append(("validator active", "validator" in options, options.get("validationAction", "error")))
        reference = f"case_health_{uuid.uuid4().hex[:16]}"
        event_id = f"evt_{uuid.uuid4().hex}"
        document = {
            "_id": event_id, "schema_version": contract.SCHEMA_VERSION, "event_type": "lab.review_required", "module": "LabLens",
            "workflow_stage": "during_visit", "source_operation": "careflow.health_check", "workflow_ref": reference, "tenant_ref": "tenant_health",
            "occurred_at": datetime.now(timezone.utc), "status": "completed", "data_classification": "synthetic_demo", "metadata": {},
        }
        self.events.insert_one(document)
        checks.append(("synthetic event written", True, "write acknowledged"))
        read = self.events.find_one({"_id": event_id})
        checks.append(("event read back", read is not None and read["workflow_ref"] == reference, "same document returned"))
        removed = self.events.delete_one({"_id": event_id}).deleted_count
        checks.append(("test event removed", removed == 1, "nothing left behind"))
        return checks

    def timeline(self, workflow_ref: str) -> List[dict]:
        found = self.events.find({"workflow_ref": workflow_ref}).sort([("occurred_at", 1), ("_id", 1)]).limit(MAX_TIMELINE)
        return [_from_document(document) for document in found]

    def recent(self, workflow_refs, module, status, start, end) -> List[dict]:
        query: dict = {"workflow_ref": {"$in": list(workflow_refs)}}
        if module:
            query["module"] = module
        if status:
            query["status"] = status
        window = {}
        if start:
            window["$gte"] = _as_date(start)
        if end:
            window["$lte"] = _as_date(end)
        if window:
            query["occurred_at"] = window
        found = self.events.find(query).sort([("occurred_at", -1), ("_id", -1)]).limit(MAX_RECENT)
        return [_from_document(document) for document in found]

    def summary(self, workflow_refs) -> Dict[str, int]:
        base = {"workflow_ref": {"$in": list(workflow_refs)}}
        return {
            "total": self.events.count_documents(base),
            **{status: self.events.count_documents({**base, "status": status}) for status in ("completed", "failed", "review_required")},
        }


def _as_date(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _from_document(document: dict) -> dict:
    event = contract.upgrade({key: value for key, value in document.items() if key not in ("_id", "recorded_at")})
    event["event_id"] = document["_id"]
    event["occurred_at"] = _as_date(document["occurred_at"]).isoformat().replace("+00:00", "Z")
    return event


def deployment_name(hosts: Sequence[str]) -> str:
    """Decided by the servers actually connected to, never by a setting's name."""
    return "mongodb_atlas" if hosts and all(ATLAS_HOST.search(host) for host in hosts) else "mongodb_local"


_mongo = None


def mongo_store() -> Optional[MongoEventStore]:
    """None unless switched on and configured. There's no default address, so a bad setting can't
    fall back to localhost. Fails only when used, never at start-up."""
    global _mongo
    if not (settings.mongodb_enabled and settings.mongodb_uri):
        return None
    if _mongo is None:
        from pymongo import MongoClient

        client = MongoClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
            connectTimeoutMS=settings.mongodb_timeout_ms,
            tz_aware=True,
            appname="diagnosia-careflow",
            w="majority",  # delivered means a majority of the cluster has the event
        )
        client.admin.command("ping")
        hosts = [host for host, _port in client.nodes]
        store = MongoEventStore(client[settings.mongodb_database], deployment_name(hosts))
        store.bootstrap()
        _mongo = store
    return _mongo


def reset_mongo() -> None:
    global _mongo
    if _mongo is not None:
        _mongo.database.client.close()
    _mongo = None
