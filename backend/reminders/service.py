"""Sends reminders by whatever channel can reach the patient, and tracks what really happened.

A reminder that can't be confirmed turns into work for a person. Being created,
queued or sent is never reported as delivered, and a reminder is not a clinical
escalation: urgent concerns stay with the care team's approved pathways.
"""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from backend.records import audit, care
from backend.records.store import insert, now_iso, one, rows, update
from backend.reminders import adapters

logger = logging.getLogger("diagnosia")

EXTERNAL_CHANNELS = ("sms", "voice")


def log_event(db: sqlite3.Connection, reminder: dict, to_state: str, channel: Optional[str], actor_id: Optional[int], note: str = "") -> None:
    insert(
        db,
        "reminder_events",
        reminder_id=reminder["id"],
        from_state=reminder["state"],
        to_state=to_state,
        channel=channel,
        actor_user_id=actor_id,
        note=note,
        created_at=now_iso(),
    )
    audit.record(db, None, f"reminder.{to_state}", reminder["patient_id"], "reminder", reminder["id"], reason=note[:200])


def move(db: sqlite3.Connection, reminder: dict, to_state: str, channel: Optional[str] = None, actor_id: Optional[int] = None, note: str = "", **fields) -> dict:
    log_event(db, reminder, to_state, channel or reminder.get("channel"), actor_id, note)
    update(db, "reminders", reminder["id"], state=to_state, updated_at=now_iso(), **({"channel": channel} if channel else {}), **fields)
    return {**reminder, "state": to_state, **fields}


def preferences(db: sqlite3.Connection, patient_id: str) -> dict:
    found = one(db.execute("SELECT * FROM communication_preferences WHERE patient_id = ?", (patient_id,)).fetchone())
    return found or {"patient_id": patient_id, "in_app": 1, "clinic_call": 1, "sms": 0, "voice": 0}


def _external_allowed(db: sqlite3.Connection, reminder: dict, prefs: dict, channel: str) -> bool:
    return (
        bool(prefs[channel])
        and channel in adapters.configured_channels()
        and care.active_consent(db, reminder["patient_id"], "external_messaging") is not None
    )


def choose_channel(db: sqlite3.Connection, reminder: dict) -> Optional[str]:
    """The first channel that could reach this person, or None if only a person can."""
    prefs = preferences(db, reminder["patient_id"])
    patient = db.execute("SELECT user_id, phone FROM patients WHERE id = ?", (reminder["patient_id"],)).fetchone()
    if prefs["in_app"] and patient["user_id"] is not None:
        return "in_app"
    if patient["phone"]:
        for channel in EXTERNAL_CHANNELS:
            if _external_allowed(db, reminder, prefs, channel):
                return channel
    return None


def require_staff_contact(db: sqlite3.Connection, reminder: dict, reason: str, channel: Optional[str] = None) -> dict:
    """Puts the patient on the clinic's list, with a task someone owns."""
    task_id = care.create_task(
        db,
        reminder["patient_id"],
        "Contact patient about a reminder",
        reminder["created_by"],
        detail=reason,
        owner_user_id=reminder["created_by"],
        escalate_to_user_id=reminder["created_by"],
        priority=reminder["priority"],
        due_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
        module=reminder["module"],
        link_type="reminder",
        link_id=reminder["id"],
    )
    return move(db, reminder, "staff_followup_required", channel or "clinic_call", note=reason, failure_reason=reason, followup_task_id=task_id)


def dispatch(db: sqlite3.Connection, reminder: dict) -> dict:
    channel = choose_channel(db, reminder)
    if channel == "in_app":
        patient = db.execute("SELECT user_id FROM patients WHERE id = ?", (reminder["patient_id"],)).fetchone()
        care.notify(db, patient["user_id"], "Reminder from your care team", reminder["title"], reminder["patient_id"], reminder["module"])
        return move(db, reminder, "in_app_sent", "in_app", note="Shown in the app. Not yet seen.", sent_at=now_iso())
    if channel is None:
        return require_staff_contact(db, reminder, "No channel can reach this patient directly")
    phone = db.execute("SELECT phone FROM patients WHERE id = ?", (reminder["patient_id"],)).fetchone()["phone"]
    try:
        reference = adapters.send(channel, phone)
    except (adapters.ChannelUnavailable, adapters.DeliveryFailed) as error:
        failed = move(db, reminder, "failed", channel, note=type(error).__name__)
        return require_staff_contact(db, failed, f"{channel} delivery failed", channel)
    return move(db, reminder, "external_delivery_pending", channel, note="Handed to the provider. Not confirmed.", sent_at=now_iso(), external_id=reference)


def record_receipt(db: sqlite3.Connection, reminder_id: str, delivered: bool) -> dict:
    """For a provider's delivery report. Only this, or a person, can say delivered."""
    reminder = one(db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())
    if reminder is None or reminder["state"] != "external_delivery_pending":
        raise ValueError("No delivery is pending for that reminder")
    if delivered:
        return move(db, reminder, "delivered", note="Provider reported delivery")
    failed = move(db, reminder, "failed", note="Provider reported failure")
    return require_staff_contact(db, failed, f"{reminder['channel']} delivery failed", reminder["channel"])


def process_due(db: sqlite3.Connection) -> int:
    """Sends what is due, and moves anything unconfirmed in time to a person."""
    timestamp = now_iso()
    due = rows(db.execute("SELECT * FROM reminders WHERE state = 'pending' AND scheduled_for <= ?", (timestamp,)))
    for reminder in due:
        dispatch(db, reminder)
    waiting = rows(
        db.execute(
            "SELECT * FROM reminders WHERE state IN ('in_app_sent', 'external_delivery_pending', 'delivered') AND sent_at IS NOT NULL"
        )
    )
    moved = 0
    for reminder in waiting:
        deadline = datetime.fromisoformat(reminder["sent_at"]) + timedelta(hours=reminder["respond_within_hours"])
        if datetime.now(timezone.utc) >= deadline:
            require_staff_contact(db, reminder, "No confirmation from the patient in time", reminder["channel"])
            moved += 1
    return len(due) + moved


def follow_up_rows(db: sqlite3.Connection, user_id: int, module: Optional[str] = None) -> List[dict]:
    """Reminders needing a person, for patients on this clinician's care team only."""
    found = rows(
        db.execute(
            """
            SELECT reminders.*, patients.first_name, patients.last_name, patients.phone,
                   tasks.due_at AS followup_due_at, tasks.status AS followup_status
            FROM reminders
            JOIN patients ON patients.id = reminders.patient_id
            JOIN care_team ON care_team.patient_id = patients.id
            LEFT JOIN tasks ON tasks.id = reminders.followup_task_id
            WHERE care_team.user_id = ? AND care_team.role = 'clinician' AND care_team.revoked_at IS NULL
              AND reminders.state = 'staff_followup_required' AND (? IS NULL OR reminders.module = ?)
            ORDER BY CASE reminders.priority WHEN 'high' THEN 0 ELSE 1 END, reminders.scheduled_for
            """,
            (user_id, module, module),
        )
    )
    timestamp = now_iso()
    for row in found:
        row["overdue"] = bool(row["followup_due_at"] and row["followup_due_at"] < timestamp)
    return found
