"""The API for DischargeCare: turns a discharge plan into tracked work.

This coordinates; it doesn't decide who goes home. Patients see only what a
clinician approved, with no AI-written instructions or warning signs. The ward
barrier and flag ideas come from Microsoft's Patient Discharge Planning sample.
"""
import sqlite3
from datetime import date, datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.auth import require_doctor, require_user
from backend.db import get_db
from backend.records import access as gate
from backend.records import audit, care
from backend.records.access import CLINICAL
from backend.records.store import insert, now_iso, rows, update

router = APIRouter(prefix="/api/addons/dischargecare")


class EpisodeRequest(BaseModel):
    patient_id: str
    encounter_id: Optional[str] = None
    admitted_on: Optional[date] = None
    discharge_date: date
    destination: str = Field(min_length=1, max_length=160)
    problems: str = Field(default="", max_length=3000)
    procedures: str = Field(default="", max_length=3000)
    emergency_contact: str = Field(default="", max_length=200)
    reconciliation_id: Optional[str] = None


class EpisodeUpdate(BaseModel):
    problems: Optional[str] = Field(default=None, max_length=3000)
    procedures: Optional[str] = Field(default=None, max_length=3000)
    emergency_contact: Optional[str] = Field(default=None, max_length=200)
    reconciliation_id: Optional[str] = None
    flagged: Optional[bool] = None


class InstructionRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=2000)
    language: Literal["en-IN", "hi-IN"] = "en-IN"


class BarrierRequest(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    owner: str = Field(default="", max_length=120)


class PendingRequest(BaseModel):
    test_name: str = Field(min_length=1, max_length=160)
    expected_by: Optional[date] = None


class FinalResult(BaseModel):
    result_note: str = Field(min_length=1, max_length=500)


class TaskPlan(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=1000)
    audience: Literal["patient", "care_team"]
    priority: Literal["routine", "high"] = "routine"
    required: bool = True
    due_at: Optional[datetime] = None
    owner_user_id: Optional[int] = None


class CloseRequest(BaseModel):
    exception_reason: str = Field(default="", max_length=500)


def _episode(db: sqlite3.Connection, episode_id: str) -> sqlite3.Row:
    episode = db.execute("SELECT * FROM discharge_episodes WHERE id = ?", (episode_id,)).fetchone()
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that discharge.")
    return episode


def _clinical(db: sqlite3.Connection, user, episode_id: str, action: str) -> sqlite3.Row:
    episode = _episode(db, episode_id)
    gate.open_patient(db, user, episode["patient_id"], action, CLINICAL)
    return episode


def _editable(db: sqlite3.Connection, user, episode_id: str, action: str) -> sqlite3.Row:
    episode = _clinical(db, user, episode_id, action)
    if episode["status"] == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This discharge is closed.")
    return episode


def _tasks(db: sqlite3.Connection, episode_id: str) -> List[dict]:
    tasks = rows(
        db.execute(
            "SELECT * FROM tasks WHERE link_type = 'discharge_episode' AND link_id = ? ORDER BY due_at IS NULL, due_at",
            (episode_id,),
        )
    )
    timestamp = now_iso()
    for task in tasks:
        task["overdue"] = bool(task["due_at"] and task["due_at"] < timestamp and task["status"] in ("open", "blocked"))
    return tasks


def _payload(db: sqlite3.Connection, episode: sqlite3.Row) -> dict:
    episode_id = episode["id"]
    return {
        **dict(episode),
        "instructions": rows(db.execute("SELECT * FROM discharge_instructions WHERE episode_id = ?", (episode_id,))),
        "barriers": rows(db.execute("SELECT * FROM discharge_barriers WHERE episode_id = ?", (episode_id,))),
        "pending_results": rows(db.execute("SELECT * FROM discharge_pending_results WHERE episode_id = ?", (episode_id,))),
        "tasks": _tasks(db, episode_id),
    }


def close_blockers(db: sqlite3.Connection, episode: sqlite3.Row) -> List[str]:
    """What must be finished, or excused on record, before a discharge can close."""
    blockers = []
    episode_id = episode["id"]
    open_required = db.execute(
        "SELECT COUNT(*) FROM tasks WHERE link_type = 'discharge_episode' AND link_id = ? AND required = 1 AND status IN ('open', 'blocked', 'missed')",
        (episode_id,),
    ).fetchone()[0]
    if open_required:
        blockers.append(f"Required tasks not done: {open_required}")
    pending = db.execute(
        "SELECT COUNT(*) FROM discharge_pending_results WHERE episode_id = ? AND status = 'pending'", (episode_id,)
    ).fetchone()[0]
    if pending:
        blockers.append(f"Test results still pending: {pending}")
    if not episode["reconciliation_id"]:
        blockers.append("No medication reconciliation is linked")
    return blockers


@router.post("/episodes", status_code=status.HTTP_201_CREATED)
def create_episode(
    request: EpisodeRequest, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    gate.open_patient(db, user, request.patient_id, "dischargecare.episode.create", CLINICAL)
    care.require_belongs(db, "encounters", request.encounter_id, request.patient_id)
    care.require_belongs(db, "medication_reconciliations", request.reconciliation_id, request.patient_id)
    episode_id = insert(
        db,
        "discharge_episodes",
        patient_id=request.patient_id,
        encounter_id=request.encounter_id,
        admitted_on=request.admitted_on.isoformat() if request.admitted_on else None,
        discharge_date=request.discharge_date.isoformat(),
        destination=request.destination,
        responsible_clinician_id=user["id"],
        problems=request.problems,
        procedures=request.procedures,
        emergency_contact=request.emergency_contact,
        reconciliation_id=request.reconciliation_id,
        created_by=user["id"],
        created_at=now_iso(),
    )
    return _payload(db, _episode(db, episode_id))


@router.get("/board")
def board(user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    """Open discharges at a glance, for the team's huddle."""
    episodes = rows(
        db.execute(
            """
            SELECT discharge_episodes.*, patients.first_name, patients.last_name
            FROM discharge_episodes JOIN patients ON patients.id = discharge_episodes.patient_id
            JOIN care_team ON care_team.patient_id = patients.id
            WHERE care_team.user_id = ? AND care_team.role = 'clinician' AND care_team.revoked_at IS NULL
              AND discharge_episodes.status != 'closed'
            ORDER BY discharge_episodes.flagged DESC, discharge_episodes.discharge_date
            """,
            (user["id"],),
        )
    )
    for episode in episodes:
        tasks = _tasks(db, episode["id"])
        episode["tasks_open"] = sum(task["status"] in ("open", "blocked", "held") for task in tasks)
        episode["tasks_overdue"] = sum(task["overdue"] for task in tasks)
        episode["tasks_blocked"] = sum(task["status"] == "blocked" for task in tasks)
        episode["tasks_escalated"] = sum(task["escalated_at"] is not None for task in tasks)
        episode["barriers_open"] = db.execute(
            "SELECT COUNT(*) FROM discharge_barriers WHERE episode_id = ? AND status = 'open'", (episode["id"],)
        ).fetchone()[0]
        episode["results_pending"] = db.execute(
            "SELECT COUNT(*) FROM discharge_pending_results WHERE episode_id = ? AND status = 'pending'", (episode["id"],)
        ).fetchone()[0]
    return episodes


@router.get("/patients/{patient_id}/episodes")
def list_episodes(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    access = gate.open_patient(db, user, patient_id, "dischargecare.episode.list")
    found = db.execute(
        "SELECT * FROM discharge_episodes WHERE patient_id = ? AND (status != 'draft' OR ? = 1) ORDER BY discharge_date DESC",
        (patient_id, int(access.is_clinician)),
    ).fetchall()
    if access.is_clinician:
        return [_payload(db, episode) for episode in found]
    return [dict(episode) for episode in found]


@router.get("/episodes/{episode_id}")
def get_episode(episode_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    episode = _clinical(db, user, episode_id, "dischargecare.episode.read")
    return {**_payload(db, episode), "close_blockers": close_blockers(db, episode)}


@router.patch("/episodes/{episode_id}")
def update_episode(
    episode_id: str,
    request: EpisodeUpdate,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _editable(db, user, episode_id, "dischargecare.episode.update")
    values = {key: value for key, value in request.model_dump().items() if value is not None}
    summary_fields = {"problems", "procedures", "emergency_contact"}
    if episode["status"] == "active" and summary_fields & set(values):
        raise HTTPException(status.HTTP_409_CONFLICT, "The approved summary can't be changed.")
    if request.reconciliation_id:
        found = db.execute(
            "SELECT status FROM medication_reconciliations WHERE id = ? AND patient_id = ?",
            (request.reconciliation_id, episode["patient_id"]),
        ).fetchone()
        if found is None or found["status"] != "finalised":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Link a finalised reconciliation for this patient.")
    if "flagged" in values:
        values["flagged"] = int(values["flagged"])
    if values:
        update(db, "discharge_episodes", episode_id, **values)
    return _payload(db, _episode(db, episode_id))


@router.post("/episodes/{episode_id}/instructions", status_code=status.HTTP_201_CREATED)
def add_instruction(
    episode_id: str,
    request: InstructionRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _editable(db, user, episode_id, "dischargecare.instruction.add")
    if episode["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Instructions are fixed once the discharge is approved.")
    insert(db, "discharge_instructions", episode_id=episode_id, **request.model_dump(), author_id=user["id"], created_at=now_iso())
    return _payload(db, episode)


@router.post("/episodes/{episode_id}/barriers", status_code=status.HTTP_201_CREATED)
def add_barrier(
    episode_id: str,
    request: BarrierRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _editable(db, user, episode_id, "dischargecare.barrier.add")
    insert(db, "discharge_barriers", episode_id=episode_id, **request.model_dump(), created_at=now_iso())
    return _payload(db, episode)


@router.post("/barriers/{barrier_id}/resolve")
def resolve_barrier(barrier_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    barrier = db.execute("SELECT * FROM discharge_barriers WHERE id = ?", (barrier_id,)).fetchone()
    if barrier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that barrier.")
    episode = _editable(db, user, barrier["episode_id"], "dischargecare.barrier.resolve")
    update(db, "discharge_barriers", barrier_id, status="resolved", resolved_by=user["id"], resolved_at=now_iso())
    return _payload(db, episode)


@router.post("/episodes/{episode_id}/pending-results", status_code=status.HTTP_201_CREATED)
def add_pending_result(
    episode_id: str,
    request: PendingRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _editable(db, user, episode_id, "dischargecare.pending.add")
    insert(
        db,
        "discharge_pending_results",
        episode_id=episode_id,
        test_name=request.test_name,
        expected_by=request.expected_by.isoformat() if request.expected_by else None,
    )
    return _payload(db, episode)


@router.post("/pending-results/{result_id}/finalise")
def finalise_result(
    result_id: str,
    request: FinalResult,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    result = db.execute("SELECT * FROM discharge_pending_results WHERE id = ?", (result_id,)).fetchone()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that result.")
    episode = _editable(db, user, result["episode_id"], "dischargecare.pending.finalise")
    update(db, "discharge_pending_results", result_id, status="final", result_note=request.result_note, finalised_by=user["id"], finalised_at=now_iso())
    return _payload(db, episode)


@router.post("/episodes/{episode_id}/tasks", status_code=status.HTTP_201_CREATED)
def add_task(
    episode_id: str,
    request: TaskPlan,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _editable(db, user, episode_id, "dischargecare.task.add")
    care.create_task(
        db,
        episode["patient_id"],
        request.title,
        user["id"],
        detail=request.detail,
        audience=request.audience,
        owner_user_id=request.owner_user_id,
        escalate_to_user_id=episode["responsible_clinician_id"],
        priority=request.priority,
        # Nothing reaches the patient until the discharge plan is approved.
        task_status="open" if episode["status"] == "active" else "held",
        required=request.required,
        due_at=request.due_at.astimezone(timezone.utc).isoformat(timespec="seconds") if request.due_at else None,
        module="dischargecare",
        link_type="discharge_episode",
        link_id=episode_id,
    )
    return _payload(db, episode)


@router.post("/episodes/{episode_id}/approve")
def approve(episode_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    episode = _clinical(db, user, episode_id, "dischargecare.episode.approve")
    if episode["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This discharge is already approved.")
    if episode["responsible_clinician_id"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the responsible clinician can approve this discharge.")
    missing = []
    if not episode["problems"].strip():
        missing.append("a problem list")
    if not episode["emergency_contact"].strip():
        missing.append("an emergency contact")
    if db.execute("SELECT 1 FROM discharge_instructions WHERE episode_id = ?", (episode_id,)).fetchone() is None:
        missing.append("at least one instruction")
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Add {', '.join(missing)} before approving.")
    timestamp = now_iso()
    update(db, "discharge_episodes", episode_id, status="active", approved_by=user["id"], approved_at=timestamp)
    db.execute(
        "UPDATE tasks SET status = 'open', updated_at = ? WHERE link_type = 'discharge_episode' AND link_id = ? AND status = 'held'",
        (timestamp, episode_id),
    )
    patient = db.execute("SELECT user_id FROM patients WHERE id = ?", (episode["patient_id"],)).fetchone()
    care.notify(db, patient["user_id"], "Your discharge checklist is ready", "", episode["patient_id"], "dischargecare")
    audit.record(db, user, "dischargecare.episode.approved", episode["patient_id"], "discharge_episode", episode_id)
    care.add_timeline(db, episode["patient_id"], "dischargecare", "Discharge plan approved", episode["destination"], "discharge_episode", episode_id)
    return _payload(db, _episode(db, episode_id))


@router.get("/episodes/{episode_id}/checklist")
def checklist(episode_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """The patient's and caregiver's view. Pending results are shown as pending."""
    episode = _episode(db, episode_id)
    gate.open_patient(db, user, episode["patient_id"], "dischargecare.checklist.read")
    if episode["status"] == "draft":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This checklist isn't ready yet.")
    tasks = [task for task in _tasks(db, episode_id) if task["audience"] == "patient"]
    medicines = rows(
        db.execute(
            "SELECT written_name, dose, frequency, status FROM medication_records WHERE source_reference = ? AND source = 'reconciled'",
            (episode["reconciliation_id"],),
        )
    )
    return {
        "discharge_date": episode["discharge_date"],
        "destination": episode["destination"],
        "emergency_contact": episode["emergency_contact"],
        "status": episode["status"],
        "instructions": rows(
            db.execute("SELECT topic, text, language FROM discharge_instructions WHERE episode_id = ?", (episode_id,))
        ),
        "medicines": medicines,
        "tasks": tasks,
        "pending_results": rows(
            db.execute(
                "SELECT test_name, expected_by, status FROM discharge_pending_results WHERE episode_id = ?", (episode_id,)
            )
        ),
    }


@router.post("/episodes/{episode_id}/close")
def close(
    episode_id: str,
    request: CloseRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _clinical(db, user, episode_id, "dischargecare.episode.close")
    if episode["status"] != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an approved, open discharge can be closed.")
    blockers = close_blockers(db, episode)
    if blockers and not request.exception_reason.strip():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "close_blocked", "blockers": blockers, "message": "; ".join(blockers)},
        )
    update(
        db,
        "discharge_episodes",
        episode_id,
        status="closed",
        closed_by=user["id"],
        closed_at=now_iso(),
        close_exception=request.exception_reason.strip(),
    )
    care.add_timeline(db, episode["patient_id"], "dischargecare", "Discharge follow-up closed", request.exception_reason.strip(), "discharge_episode", episode_id)
    return _payload(db, _episode(db, episode_id))
