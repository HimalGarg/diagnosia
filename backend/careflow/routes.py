"""CareFlow: a read-only activity timeline across the tools.

Who may see what is decided here, from the Diagnosia database, on every request. The event store is
only asked for references the person is already allowed to open, so a store can't widen access.
"""
import logging
import sqlite3
from typing import List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from backend.auth import require_user
from backend import settings_store
from backend.careflow import contract, delivery, events, live, stores
from backend.config import settings
from backend.db import get_db
from backend.integrations import execution, meko, yugabyte
from backend.integrations import status as integration_status
from backend.records import access as gate

logger = logging.getLogger("diagnosia.careflow")
router = APIRouter(prefix="/api/careflow")

MAX_WORKFLOWS = 500
NOT_FOUND = "We couldn't find that workflow."


def _allowed(db: sqlite3.Connection, user: sqlite3.Row) -> List[sqlite3.Row]:
    """Workflows this person can open, using the same rules as the patient list."""
    return db.execute(
        """
        SELECT flow.* FROM careflow_workflows AS flow
        WHERE flow.owner_user_id = :user
           OR flow.patient_id IN (SELECT id FROM patients WHERE user_id = :user)
           OR flow.patient_id IN (
                SELECT patient_id FROM care_team WHERE user_id = :user AND revoked_at IS NULL
                  AND (role != 'clinician' OR :status = 'verified'))
           OR (:status = 'verified' AND flow.organization_id IN (SELECT organization_id FROM organization_members WHERE user_id = :user))
        ORDER BY flow.created_at DESC LIMIT :limit
        """,
        {"user": user["id"], "status": user["account_status"], "limit": MAX_WORKFLOWS},
    ).fetchall()


def _open_workflow(db: sqlite3.Connection, user: sqlite3.Row, workflow_ref: str) -> sqlite3.Row:
    if not contract.WORKFLOW_REF.match(workflow_ref):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    workflow = next((row for row in _allowed(db, user) if row["workflow_ref"] == workflow_ref), None)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if workflow["patient_id"]:
        gate.open_patient(db, user, workflow["patient_id"], "careflow.timeline.read")
    return workflow


def _store(db: sqlite3.Connection):
    """Atlas when it's on and answering. Otherwise the local outbox, and the page says so."""
    local = stores.LocalStore(db)
    if not (settings.mongodb_enabled and settings.mongodb_uri):
        return local, False
    try:
        store = stores.mongo_store()
        store.ping()
        return store, False
    except Exception as error:  # Any Atlas failure means the same thing here: show the local copy.
        logger.warning("Atlas unavailable for reads: %s", type(error).__name__)
        return local, True


def _label(db: sqlite3.Connection, workflow: sqlite3.Row) -> str:
    """A readable name, from the Diagnosia database, only for someone already allowed to see it."""
    if workflow["patient_id"]:
        row = db.execute("SELECT first_name, last_name FROM patients WHERE id = ?", (workflow["patient_id"],)).fetchone()
        if row:
            return f"{row['first_name']} {row['last_name']}"
    if workflow["organization_id"]:
        return "Organization activity"
    return workflow["owner_module"] or "DiagnoFast interview"


def _view(events_found: Sequence[dict]) -> List[dict]:
    """Only what the timeline shows. Technical identifiers stay out of the browser."""
    return [
        {
            "event_id": event["event_id"],
            "workflow_ref": event["workflow_ref"],
            "module": event["module"],
            "event_type": event["event_type"],
            "status": event["status"],
            "occurred_at": event["occurred_at"],
            "actor_type": event["actor_type"],
            "target_module": event.get("target_module"),
            "review_required": bool(event.get("metadata", {}).get("review_required")) or event["status"] == "review_required",
        }
        for event in events_found
    ]


def _envelope(store, degraded: bool) -> dict:
    return {"source": store.name, "degraded": degraded, "synthetic_demo": settings.demo_mode}


@router.get("/workflows")
def workflows(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    allowed = _allowed(db, user)
    counts = {
        row["workflow_ref"]: row["total"]
        for row in db.execute("SELECT workflow_ref, COUNT(*) AS total FROM careflow_outbox GROUP BY workflow_ref")
    }
    return {
        "workflows": [
            {"workflow_ref": row["workflow_ref"], "label": _label(db, row), "patient_id": row["patient_id"], "event_count": counts.get(row["workflow_ref"], 0)}
            for row in allowed
            if counts.get(row["workflow_ref"])
        ],
        "modules": sorted(contract.MODULES),
        "synthetic_demo": settings.demo_mode,
    }


@router.get("/workflows/{workflow_ref}/timeline")
def timeline(workflow_ref: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    workflow = _open_workflow(db, user, workflow_ref)
    store, degraded = _store(db)
    try:
        found = store.timeline(workflow_ref)
    except Exception as error:
        logger.warning("Timeline read failed: %s", type(error).__name__)
        store, degraded = stores.LocalStore(db), True
        found = store.timeline(workflow_ref)
    return {
        **_envelope(store, degraded), "workflow_ref": workflow_ref, "label": _label(db, workflow), "events": _view(found),
        "activity": {"pending_review": stores.waiting_for_review(found), "modules": sorted({event["module"] for event in found})},
    }


@router.get("/events")
def recent_events(
    module: Optional[str] = Query(default=None, max_length=40),
    status_filter: Optional[str] = Query(default=None, alias="status", max_length=20),
    start: Optional[str] = Query(default=None, max_length=32),
    end: Optional[str] = Query(default=None, max_length=32),
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    references = [row["workflow_ref"] for row in _allowed(db, user)]
    store, degraded = _store(db)
    return {**_envelope(store, degraded), "events": _view(store.recent(references, module, status_filter, start, end))}


@router.get("/summary")
def summary(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    references = [row["workflow_ref"] for row in _allowed(db, user)]
    store, degraded = _store(db)
    return {**_envelope(store, degraded), "counts": store.summary(references)}


@router.get("/stream")
def stream(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> StreamingResponse:
    """Tells a screen to reload. The list of workflows is fixed at connection, and each reload rechecks it."""
    if not settings.mongodb_change_streams:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Live updates aren't switched on.")
    allowed = {row["workflow_ref"] for row in _allowed(db, user)}
    return StreamingResponse(live.updates(allowed), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.get("/workflows/{workflow_ref}/handoffs")
def handoffs(workflow_ref: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """Which tool passed what to which, and how far each record of that handoff has got."""
    _open_workflow(db, user, workflow_ref)
    found = []
    for row in db.execute(
        "SELECT event_id, module, event_type, occurred_at, event FROM careflow_outbox WHERE workflow_ref = ? ORDER BY occurred_at",
        (workflow_ref,),
    ):
        event = events.decode(row)
        if not event.get("target_module"):
            continue
        states = {
            item["channel"]: {"state": item["status"], "context_ref": item["result_ref"]}
            for item in db.execute("SELECT channel, status, result_ref FROM careflow_deliveries WHERE event_id = ?", (row["event_id"],))
        }
        found.append({
            "source_module": event["module"], "target_module": event["target_module"], "event_type": event["event_type"],
            "occurred_at": event["occurred_at"], "human_review_required": True, "records": states,
        })
    return {"handoffs": found}


@router.get("/health")
def health(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    gate_staff(user)
    return {
        "integrations": [item.view() for item in integration_status.overview()],
        "deliveries": delivery.backlog(db),
        "agent_runs_waiting": execution.waiting(),
        "change_stream": live.status.view(),
        "recording": events.active(),
    }


@router.get("/operations")
def operations(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """Fixed operational figures for administrators. Nothing here accepts a query."""
    gate_staff(user)
    if not settings_store.is_platform_admin(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is for an administrator of a verified organization.")
    local = {**execution.summary(), "handoff_count": delivery.handoff_count(db)}
    if not (settings.yugabyte_enabled and settings.yugabyte_dsn):
        return {"source": "local_records", "figures": local}
    try:
        return {"source": "yugabytedb", "figures": yugabyte.store().operations()}
    except Exception as error:  # Any failure here means the same: show the local figures, and say so.
        logger.warning("Operational figures unavailable: %s", type(error).__name__)
        return {"source": "local_records", "degraded": True, "figures": local}


def gate_staff(user: sqlite3.Row) -> None:
    if user["role"] != "doctor" or user["account_status"] != "verified":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is for verified staff.")


@router.get("/context")
def shared_context(
    module: str = Query(max_length=40),
    topic: str = Query(min_length=2, max_length=120),
    user=Depends(require_user),
) -> dict:
    """Approved workflow guidance from Meko. Supplemental, and clearly not a clinical record."""
    gate_staff(user)
    if module not in contract.MODULES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown module.")
    client = meko.client()
    if client is None:
        return {"available": False, "items": [], "reason": "Shared context isn't connected."}
    found = client.knowledge(f"{module}: {topic}")
    return {"available": found.available, "items": found.items, "reason": found.reason}
