"""Shared actions the add-on tools use: consent, tasks, notifications and the timeline."""
import sqlite3
from typing import List, Optional

from fastapi import HTTPException, status

from backend.config import settings
from backend.records.store import insert, now_iso, rows, update

POLICY_VERSION = "2026-10-draft"

# Consent means something only if the code that needs it checks it, so each purpose is named here.
CONSENT_PURPOSES = {
    "ai_processing": "Sending this patient's documents to our AI services to read them",
    "ai_scribe": "Recording and transcribing a consultation to draft a clinical note",
    "transcript_retention": "Keeping a consultation transcript after the note is signed",
    "referral_sharing": "Sharing referral details with the receiving specialist or service",
    "external_messaging": "Sending reminders by SMS or voice call through an outside provider",
    "patient_pack": "Keeping a searchable copy of this patient's finished records with Meko, an outside provider, and sending them to our AI services when their doctor asks for a summary",
    "pack_sharing": "Letting a doctor who accepts this patient's referral see their whole pack until the referral closes",
}


def consent_by_default() -> bool:
    """Only ever for made-up patients. With real patient data, a person has to record each agreement."""
    return settings.default_consent and settings.demo_mode and not settings.allow_real_patient_data


def grant_default_consents(db: sqlite3.Connection, patient_id: str) -> None:
    """Marked demo_default, so these can never be mistaken for a person's agreement. A purpose that
    was ever recorded is left alone, so a revocation stays revoked."""
    if not consent_by_default():
        return
    known = {row["purpose"] for row in db.execute("SELECT purpose FROM consents WHERE patient_id = ?", (patient_id,))}
    for purpose in CONSENT_PURPOSES:
        if purpose not in known:
            insert(
                db, "consents", patient_id=patient_id, purpose=purpose, scope="demo_default", method="clinician_recorded",
                policy_version=POLICY_VERSION, recorded_by=0, granted_at=now_iso(),
            )


def grant_default_consents_to_all(db: sqlite3.Connection) -> None:
    for row in db.execute("SELECT id FROM patients").fetchall():
        grant_default_consents(db, row["id"])


def active_consent(db: sqlite3.Connection, patient_id: str, purpose: str) -> Optional[sqlite3.Row]:
    return db.execute(
        """
        SELECT * FROM consents WHERE patient_id = ? AND purpose = ? AND revoked_at IS NULL
        ORDER BY granted_at DESC LIMIT 1
        """,
        (patient_id, purpose),
    ).fetchone()


def require_consent(db: sqlite3.Connection, patient_id: str, purpose: str) -> sqlite3.Row:
    consent = active_consent(db, patient_id, purpose)
    if consent is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "consent_required", "purpose": purpose, "message": f"Consent needed: {CONSENT_PURPOSES[purpose]}."},
        )
    return consent


def require_belongs(db: sqlite3.Connection, table: str, row_id: Optional[str], patient_id: str) -> None:
    """A link to another record must stay inside the same patient's record."""
    if row_id is None:
        return
    found = db.execute(f"SELECT 1 FROM {table} WHERE id = ? AND patient_id = ?", (row_id, patient_id)).fetchone()
    if found is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That link isn't part of this patient's record.")


def add_timeline(
    db: sqlite3.Connection,
    patient_id: str,
    module: str,
    title: str,
    detail: str = "",
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> None:
    insert(
        db,
        "timeline_events",
        patient_id=patient_id,
        module=module,
        title=title,
        detail=detail,
        resource_type=resource_type,
        resource_id=resource_id,
        occurred_at=occurred_at or now_iso(),
    )


def notify(
    db: sqlite3.Connection,
    recipient_user_id: Optional[int],
    title: str,
    body: str = "",
    patient_id: Optional[str] = None,
    module: str = "general",
) -> None:
    if recipient_user_id is None:
        return
    insert(
        db,
        "notifications",
        recipient_user_id=recipient_user_id,
        patient_id=patient_id,
        title=title,
        body=body,
        module=module,
        created_at=now_iso(),
    )


def care_team_clinicians(db: sqlite3.Connection, patient_id: str) -> List[int]:
    return [
        row["user_id"]
        for row in db.execute(
            """
            SELECT user_id FROM care_team
            WHERE patient_id = ? AND role = 'clinician' AND scope = 'full' AND revoked_at IS NULL
            """,
            (patient_id,),
        )
    ]


def notify_care_team(
    db: sqlite3.Connection, patient_id: str, title: str, body: str = "", module: str = "general"
) -> None:
    for user_id in care_team_clinicians(db, patient_id):
        notify(db, user_id, title, body, patient_id, module)


def create_task(
    db: sqlite3.Connection,
    patient_id: str,
    title: str,
    created_by: int,
    detail: str = "",
    audience: str = "care_team",
    owner_user_id: Optional[int] = None,
    escalate_to_user_id: Optional[int] = None,
    priority: str = "routine",
    task_status: str = "open",
    required: bool = False,
    due_at: Optional[str] = None,
    module: str = "general",
    link_type: Optional[str] = None,
    link_id: Optional[str] = None,
) -> str:
    timestamp = now_iso()
    return insert(
        db,
        "tasks",
        patient_id=patient_id,
        title=title,
        detail=detail,
        audience=audience,
        owner_user_id=owner_user_id,
        escalate_to_user_id=escalate_to_user_id,
        priority=priority,
        status=task_status,
        required=int(required),
        due_at=due_at,
        module=module,
        link_type=link_type,
        link_id=link_id,
        created_by=created_by,
        created_at=timestamp,
        updated_at=timestamp,
    )


def escalate_overdue(db: sqlite3.Connection) -> int:
    """Overdue high-priority work goes to a named person, once, rather than only reminding again."""
    overdue = rows(
        db.execute(
            """
            SELECT * FROM tasks
            WHERE priority = 'high' AND status IN ('open', 'blocked') AND due_at IS NOT NULL
              AND due_at < ? AND escalated_at IS NULL
            """,
            (now_iso(),),
        )
    )
    for task in overdue:
        update(db, "tasks", task["id"], escalated_at=now_iso())
        message = f"Overdue high-priority task: {task['title']}"
        if task["escalate_to_user_id"] is not None:
            notify(db, task["escalate_to_user_id"], message, task["detail"], task["patient_id"], task["module"])
        else:
            notify_care_team(db, task["patient_id"], message, task["detail"], task["module"])
    return len(overdue)
