"""The one way any tool records a CareFlow event.

An event goes into the outbox in the same SQLite transaction as the change it describes, so it
can't claim something that was rolled back. The delivery worker sends it on later. This module
never lets its own bookkeeping break a clinical action: a refused or failed event is logged and
the tool carries on.
"""
import hashlib
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterator, Optional, Tuple

from pydantic import ValidationError

from backend.careflow import contract
from backend.careflow.contract import CareEvent, ContractError
from backend.config import settings
from backend.db import connect
from backend.integrations import execution

logger = logging.getLogger("diagnosia.careflow")

HANDOFF_CHANNELS = ("yugabyte", "meko")
ERROR_CATEGORIES = {
    "AINotConfigured": "ai_not_configured",
    "AIRateLimited": "ai_rate_limited",
    "AIUnavailable": "ai_unavailable",
    "AIDeclined": "ai_declined",
    "SpeechUnavailable": "speech_unavailable",
    "SpeechRejected": "speech_rejected",
}

# What each request was attempting, so that a failure can be reported after its transaction is gone.
_attempted: Dict[str, Tuple[contract.Mapping, str]] = {}


def active() -> bool:
    """CareFlow is built for synthetic data. With real patient data it records nothing at all."""
    return settings.demo_mode and not settings.allow_real_patient_data


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reference(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def tenant_for(db: sqlite3.Connection, user: Optional[sqlite3.Row]) -> str:
    """A one-way reference to the actor's organization, so the event store never holds its ID."""
    row = None
    if user is not None:
        row = db.execute("SELECT organization_id FROM organization_members WHERE user_id = ? ORDER BY created_at LIMIT 1", (user["id"],)).fetchone()
    if row is None:
        return "tenant_individual"
    return f"tenant_{hashlib.sha256(row['organization_id'].encode()).hexdigest()[:24]}"


def _new_workflow(db: sqlite3.Connection, user: Optional[sqlite3.Row], patient_id=None, intake_id=None, owner_user_id=None, owner_module=None) -> None:
    db.execute(
        """
        INSERT OR IGNORE INTO careflow_workflows
            (workflow_ref, correlation_id, tenant_ref, patient_id, intake_id, owner_user_id, owner_module, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (_reference("case"), _reference("corr"), tenant_for(db, user), patient_id, intake_id, owner_user_id, owner_module, _now().isoformat()),
    )


def for_patient(db: sqlite3.Connection, patient_id: str, user: Optional[sqlite3.Row] = None) -> sqlite3.Row:
    _new_workflow(db, user, patient_id=patient_id)
    return db.execute("SELECT * FROM careflow_workflows WHERE patient_id = ?", (patient_id,)).fetchone()


def for_intake(db: sqlite3.Connection, intake_id: int, owner_user_id: int) -> sqlite3.Row:
    owner = db.execute("SELECT id FROM users WHERE id = ?", (owner_user_id,)).fetchone()
    _new_workflow(db, owner, intake_id=intake_id, owner_user_id=owner_user_id)
    return db.execute("SELECT * FROM careflow_workflows WHERE intake_id = ?", (intake_id,)).fetchone()


def for_person(db: sqlite3.Connection, user: sqlite3.Row, module: str) -> sqlite3.Row:
    """Tools with no patient record, such as the Prescription Analyzer, follow the signed-in person."""
    _new_workflow(db, user, owner_user_id=user["id"], owner_module=module)
    return db.execute("SELECT * FROM careflow_workflows WHERE owner_user_id = ? AND owner_module = ?", (user["id"], module)).fetchone()


def _actor(user: Optional[sqlite3.Row]) -> str:
    if user is None:
        return "system"
    return "patient" if user["role"] == "patient" else "clinician"


def _model_metadata() -> dict:
    used = execution.last_model.get()
    return {"model_provider": used[0], "model_name": used[1]} if used else {}


def emit(
    db: sqlite3.Connection,
    module: str,
    event_type: str,
    workflow: sqlite3.Row,
    status: str = "completed",
    user: Optional[sqlite3.Row] = None,
    key: Optional[str] = None,
    target_module: Optional[str] = None,
    metadata: Optional[dict] = None,
    used_ai: bool = False,
    actor: Optional[str] = None,
    operation: Optional[str] = None,
) -> Optional[str]:
    """Adds an event to the outbox, in the caller's transaction. Returns its ID, or None if refused."""
    if not active():
        return None
    event_id = f"evt_{uuid.uuid4().hex}"
    try:
        occurred = _now()
        event = CareEvent(
            event_id=event_id,
            event_type=event_type,
            module=module,
            workflow_stage=contract.WORKFLOW_STAGES.get(module, ""),
            source_operation=operation or event_type,
            source_ref=execution.request_id.get(),
            workflow_ref=workflow["workflow_ref"],
            tenant_ref=workflow["tenant_ref"],
            correlation_id=workflow["correlation_id"],
            causation_id=_previous(db, workflow["workflow_ref"]) if target_module else None,
            occurred_at=occurred,
            status=status,
            actor_type=actor or _actor(user),
            target_module=target_module,
            metadata={**(metadata or {}), **(_model_metadata() if used_ai else {})},
        )
        inserted = db.execute(
            """
            INSERT OR IGNORE INTO careflow_outbox (event_id, idempotency_key, workflow_ref, module, event_type, status, occurred_at, event)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, f"{event_type}:{workflow['workflow_ref']}:{key or event_id}", workflow["workflow_ref"], module, event_type, status, occurred.isoformat(), event.model_dump_json()),
        ).rowcount
        if not inserted:
            return None
        for channel in ("mongodb", *(HANDOFF_CHANNELS if target_module else ())):
            db.execute(
                "INSERT INTO careflow_deliveries (event_id, channel, next_attempt_at) VALUES (?, ?, ?)",
                (event_id, channel, occurred.isoformat()),
            )
        return event_id
    except (ValidationError, ContractError, sqlite3.Error) as error:
        logger.warning("CareFlow event %s not recorded: %s", event_type, type(error).__name__)
        return None


def _previous(db: sqlite3.Connection, workflow_ref: str) -> Optional[str]:
    row = db.execute("SELECT event_id FROM careflow_outbox WHERE workflow_ref = ? ORDER BY occurred_at DESC LIMIT 1", (workflow_ref,)).fetchone()
    return row["event_id"] if row else None


def emit_separately(module: str, event_type: str, locate, status: str = "completed", user=None, **options) -> Optional[str]:
    """Commits on its own. For events with no change to share a transaction with, such as a failure."""
    if not active():
        return None
    db = connect()
    try:
        workflow = locate(db)
        bind(workflow)
        event_id = emit(db, module, event_type, workflow, status, user, **options)
        db.commit()
        return event_id
    except sqlite3.Error as error:
        logger.warning("CareFlow event %s not recorded: %s", event_type, type(error).__name__)
        return None
    finally:
        db.close()


def category_of(error: BaseException) -> str:
    return ERROR_CATEGORIES.get(type(error).__name__, "processing_error")


@contextmanager
def tracked(module: str, started: str, failed: str, locate, user=None) -> Iterator[None]:
    """For work with no database change: records its start, and its failure if there is one."""
    emit_separately(module, started, locate, "started", user)
    try:
        yield
    except Exception as error:
        emit_separately(module, failed, locate, "failed", user, metadata={"error_category": category_of(error)})
        raise


def from_audit(
    db: sqlite3.Connection,
    user: Optional[sqlite3.Row],
    action: str,
    patient_id: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    outcome: str,
    request_id: Optional[str],
) -> None:
    """Called for each audit row, in its transaction. Most audit actions aren't CareFlow steps."""
    mapping = contract.AUDIT_EVENTS.get(action)
    if mapping is None or outcome != "success" or not active():
        return
    if not patient_id:
        return
    workflow = for_patient(db, patient_id, user)
    if mapping.failed and request_id and patient_id:
        _attempted[request_id] = (mapping, patient_id)
    bind(workflow)
    if mapping.deferred:
        return
    metadata = {"review_required": True} if mapping.review_required else None
    emit(
        db, mapping.module, mapping.event_type, workflow, mapping.status, user,
        key=f"{resource_id or ''}:{request_id or uuid.uuid4().hex}", target_module=mapping.target_module, metadata=metadata,
        operation=action,
    )


def request_failed(request_id: Optional[str], error: BaseException) -> None:
    """The request rolled back, taking any success event with it. This records what really happened."""
    attempted = _attempted.pop(request_id, None) if request_id else None
    if attempted is None:
        return
    mapping, patient_id = attempted
    # The workflow may have been made in the rolled-back request, so it's looked up again here.
    emit_separately(mapping.module, mapping.failed, lambda db: for_patient(db, patient_id), "failed", metadata={"error_category": category_of(error)})


def forget(request_id: Optional[str]) -> None:
    if request_id:
        _attempted.pop(request_id, None)


def decode(row: sqlite3.Row) -> dict:
    return contract.upgrade(json.loads(row["event"]))


def bind(workflow: Optional[sqlite3.Row]) -> None:
    """Links any AI run in this request to the workflow, by its pseudonymous reference only."""
    if workflow is not None:
        execution.workflow_ref.set(workflow["workflow_ref"])
