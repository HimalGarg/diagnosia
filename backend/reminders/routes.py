"""The API for reminders, including the clinic's follow-up list."""
import sqlite3
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend import settings_store
from backend.auth import require_doctor, require_user
from backend.db import get_db
from backend.records import access as gate
from backend.records import audit, care
from backend.records.access import CLINICAL, WRITE
from backend.records.store import insert, now_iso, one, rows, update
from backend.reminders import service

router = APIRouter(prefix="/api")

NOT_ESCALATION = (
    "A reminder is not an emergency or clinical escalation. Urgent concerns must go through "
    "your care team's approved pathway."
)
# Printed lists leave the system, so they say only that the clinic wants a call.
PRINT_LABEL = "Clinic follow-up"
REACHED = ("reached", "patient_declined")


class ReminderRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    purpose: Literal["appointment", "medicine_review", "test", "follow_up", "other"] = "follow_up"
    module: Literal["general", "dischargecare", "referralloop", "lablens"] = "general"
    priority: Literal["routine", "high"] = "routine"
    scheduled_for: datetime
    respond_within_hours: Optional[int] = Field(default=None, ge=1, le=168)
    task_id: Optional[str] = None


class PreferencesRequest(BaseModel):
    in_app: bool = True
    clinic_call: bool = True
    sms: bool = False
    voice: bool = False


class OutcomeRequest(BaseModel):
    outcome: Literal["reached", "no_answer", "wrong_number", "message_left", "patient_declined", "unable_to_contact"]
    note: str = Field(default="", max_length=300)


def _reminder(db: sqlite3.Connection, reminder_id: str) -> dict:
    reminder = one(db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())
    if reminder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that reminder.")
    return reminder


def _with_history(db: sqlite3.Connection, reminder: dict) -> dict:
    return {
        **reminder,
        "notice": NOT_ESCALATION,
        "events": rows(
            db.execute(
                "SELECT from_state, to_state, channel, note, created_at FROM reminder_events WHERE reminder_id = ? ORDER BY created_at, rowid",
                (reminder["id"],),
            )
        ),
    }


@router.get("/patients/{patient_id}/communication-preferences")
def get_preferences(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    gate.open_patient(db, user, patient_id, "reminders.preferences.read")
    return service.preferences(db, patient_id)


@router.put("/patients/{patient_id}/communication-preferences")
def save_preferences(
    patient_id: str,
    request: PreferencesRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "reminders.preferences.save", WRITE)
    if (request.sms or request.voice) and care.active_consent(db, patient_id, "external_messaging") is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "consent_required",
                "purpose": "external_messaging",
                "message": f"Consent needed: {care.CONSENT_PURPOSES['external_messaging']}.",
            },
        )
    db.execute(
        """
        INSERT INTO communication_preferences (patient_id, in_app, clinic_call, sms, voice, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (patient_id) DO UPDATE SET in_app = excluded.in_app, clinic_call = excluded.clinic_call,
            sms = excluded.sms, voice = excluded.voice, updated_by = excluded.updated_by, updated_at = excluded.updated_at
        """,
        (patient_id, int(request.in_app), int(request.clinic_call), int(request.sms), int(request.voice), user["id"], now_iso()),
    )
    return service.preferences(db, patient_id)


@router.post("/patients/{patient_id}/reminders", status_code=status.HTTP_201_CREATED)
def schedule_reminder(
    patient_id: str,
    request: ReminderRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "reminder.schedule", CLINICAL)
    care.require_belongs(db, "tasks", request.task_id, patient_id)
    timestamp = now_iso()
    reminder_id = insert(
        db,
        "reminders",
        patient_id=patient_id,
        task_id=request.task_id,
        purpose=request.purpose,
        title=request.title,
        module=request.module,
        priority=request.priority,
        scheduled_for=request.scheduled_for.isoformat(timespec="seconds"),
        respond_within_hours=request.respond_within_hours or settings_store.get("reminders.respond_within_hours", db),
        created_by=user["id"],
        created_at=timestamp,
        updated_at=timestamp,
    )
    service.process_due(db)
    return _with_history(db, _reminder(db, reminder_id))


@router.get("/patients/{patient_id}/reminders")
def list_reminders(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "reminder.list")
    service.process_due(db)
    found = rows(
        db.execute("SELECT * FROM reminders WHERE patient_id = ? ORDER BY scheduled_for DESC LIMIT 100", (patient_id,))
    )
    return [_with_history(db, reminder) for reminder in found]


@router.post("/reminders/{reminder_id}/acknowledge")
def acknowledge(reminder_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """The patient or their caregiver saying they saw it."""
    reminder = _reminder(db, reminder_id)
    gate.open_patient(db, user, reminder["patient_id"], "reminder.acknowledge")
    if reminder["state"] not in ("in_app_sent", "external_delivery_pending", "delivered", "staff_followup_required"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This reminder can't be acknowledged.")
    updated = service.move(db, reminder, "acknowledged", actor_id=user["id"], note="Acknowledged")
    _close_followup(db, updated, "Patient acknowledged")
    return _with_history(db, _reminder(db, reminder_id))


def _close_followup(db: sqlite3.Connection, reminder: dict, note: str) -> None:
    if reminder.get("followup_task_id"):
        update(db, "tasks", reminder["followup_task_id"], status="done", status_note=note, updated_at=now_iso())


@router.post("/reminders/{reminder_id}/outcome")
def record_outcome(
    reminder_id: str,
    request: OutcomeRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """What happened when staff tried to reach the patient."""
    reminder = _reminder(db, reminder_id)
    gate.open_patient(db, user, reminder["patient_id"], "reminder.outcome", CLINICAL)
    if reminder["state"] != "staff_followup_required":
        raise HTTPException(status.HTTP_409_CONFLICT, "This reminder isn't waiting for follow-up.")
    attempts = reminder["contact_attempts"] + 1
    if request.outcome in REACHED:
        updated = service.move(
            db, reminder, "acknowledged", actor_id=user["id"], note=request.outcome,
            outcome=request.outcome, contact_attempts=attempts,
        )
        _close_followup(db, updated, request.outcome)
    else:
        service.log_event(db, reminder, "staff_followup_required", reminder["channel"], user["id"], request.outcome)
        update(db, "reminders", reminder_id, outcome=request.outcome, contact_attempts=attempts, updated_at=now_iso())
    audit.record(db, user, "reminder.outcome_recorded", reminder["patient_id"], "reminder", reminder_id, reason=request.outcome)
    return _with_history(db, _reminder(db, reminder_id))


@router.post("/reminders/{reminder_id}/cancel")
def cancel(reminder_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    reminder = _reminder(db, reminder_id)
    gate.open_patient(db, user, reminder["patient_id"], "reminder.cancel", CLINICAL)
    if reminder["state"] in ("acknowledged", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This reminder is finished.")
    updated = service.move(db, reminder, "cancelled", actor_id=user["id"], note="Cancelled")
    _close_followup(db, updated, "Reminder cancelled")
    return _with_history(db, _reminder(db, reminder_id))


@router.get("/reminders/follow-up")
def follow_up(
    module: Optional[str] = Query(default=None, max_length=30),
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> List[dict]:
    service.process_due(db)
    found = service.follow_up_rows(db, user["id"], module)
    for row in found:
        audit.record(db, user, "reminder.followup.read", row["patient_id"], "reminder", row["id"])
    return found


@router.get("/reminders/call-list")
def call_list(user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """For printing at clinics that work from paper. Holds no clinical detail, and every use is logged."""
    service.process_due(db)
    entries = []
    for row in service.follow_up_rows(db, user["id"]):
        audit.record(db, user, "reminder.call_list.printed", row["patient_id"], "reminder", row["id"])
        entries.append(
            {"name": f"{row['first_name']} {row['last_name'][:1]}.", "phone": row["phone"] or "", "reason": PRINT_LABEL, "attempts": row["contact_attempts"]}
        )
    return {"generated_at": now_iso(), "entries": entries}
