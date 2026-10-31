"""The API for ReferralLoop: tracks a referral until the outcome is back and reviewed.

Stages and test scenarios follow the Healthtech Referral Management Toolkit
(CC BY 4.0, see THIRD_PARTY_NOTICES.md). This tracks and coordinates: it doesn't
decide who needs a specialist, and priority is always a person's choice, never
read from free text. A referral can't quietly disappear: it stays open until a
reviewed outcome or a recorded reason closes it.
"""
import sqlite3
from datetime import date, timedelta
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from backend.auth import require_doctor, require_user
from backend.db import get_db
from backend.records import access as gate
from backend.records import audit, care, documents
from backend.records.access import CLINICAL
from backend.records.store import insert, now_iso, one, rows, update

router = APIRouter(prefix="/api/addons/referrals")

DUPLICATE_WINDOW_DAYS = 30
MAX_CONTACT_ATTEMPTS = 3
TERMINAL = ("closed", "cancelled")

# Who may move a referral from one stage to the next.
RECEIVER_MOVES = {
    "sent": {"acknowledged", "needs_information", "declined", "redirected"},
    "acknowledged": {"accepted", "needs_information", "declined", "redirected"},
    "accepted": {"appointment_offered", "booked", "declined"},
    "appointment_offered": {"booked", "rescheduled", "declined"},
    "booked": {"rescheduled", "attended", "no_show"},
    "rescheduled": {"booked", "attended", "no_show"},
    "no_show": {"appointment_offered", "booked", "declined"},
}
REFERRER_MOVES = {"needs_information": {"sent"}}
Status = Literal[
    "acknowledged", "needs_information", "accepted", "declined", "redirected", "appointment_offered",
    "booked", "rescheduled", "attended", "no_show", "sent",
]


class ReferralRequest(BaseModel):
    patient_id: str
    encounter_id: Optional[str] = None
    receiving_organization_id: Optional[str] = None
    receiving_user_id: Optional[int] = None
    manual_destination: str = Field(default="", max_length=200)
    specialty: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)
    clinical_question: str = Field(default="", max_length=1000)
    priority: Literal["routine", "urgent"]
    respond_by: date
    document_ids: List[str] = Field(default_factory=list, max_length=20)
    confirm_duplicate_reason: str = Field(default="", max_length=300)
    replaces_referral_id: Optional[str] = None


class StatusRequest(BaseModel):
    status: Status
    note: str = Field(default="", max_length=500)


class PriorityRequest(BaseModel):
    priority: Literal["routine", "urgent"]
    reason: str = Field(min_length=1, max_length=300)


class AppointmentRequest(BaseModel):
    scheduled_at: str = Field(min_length=10, max_length=25)
    location: str = Field(default="", max_length=200)
    booked: bool = False


class ContactAttempt(BaseModel):
    note: str = Field(default="", max_length=300)


class ReviewRequest(BaseModel):
    note: str = Field(default="", max_length=500)


class CloseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class NoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=500)


def _referral(db: sqlite3.Connection, referral_id: str) -> sqlite3.Row:
    referral = db.execute("SELECT * FROM referrals WHERE id = ?", (referral_id,)).fetchone()
    if referral is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that referral.")
    return referral


def _is_receiver(db: sqlite3.Connection, user: sqlite3.Row, referral: sqlite3.Row) -> bool:
    # Sharing ends the moment the patient revokes consent, or the account loses verification.
    if user["account_status"] != "verified":
        return False
    if care.active_consent(db, referral["patient_id"], "referral_sharing") is None:
        return False
    if referral["receiving_user_id"] == user["id"]:
        return True
    if referral["receiving_organization_id"] is None:
        return False
    return (
        db.execute(
            "SELECT 1 FROM organization_members WHERE organization_id = ? AND user_id = ?",
            (referral["receiving_organization_id"], user["id"]),
        ).fetchone()
        is not None
    )


def _side(db: sqlite3.Connection, user: sqlite3.Row, referral: sqlite3.Row, action: str) -> str:
    """referrer, receiver or patient. A receiving service sees this referral, not the whole record."""
    if _is_receiver(db, user, referral):
        audit.record(db, user, action, referral["patient_id"], "referral", referral["id"])
        return "receiver"
    access = gate.lookup(db, user, referral["patient_id"])
    if access is None:
        audit.record_denied(user, action, referral["patient_id"], "not part of this referral")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that referral.")
    audit.record(db, user, action, referral["patient_id"], "referral", referral["id"])
    return "referrer" if access.is_clinician and access.can_write else "patient"


def _is_manual(referral: sqlite3.Row) -> bool:
    return referral["receiving_organization_id"] is None and referral["receiving_user_id"] is None


def _log(
    db: sqlite3.Connection, referral: sqlite3.Row, new_status: str, user_id: int, side: str, note: str = "", source: str = "app"
) -> None:
    insert(
        db,
        "referral_events",
        referral_id=referral["id"],
        from_status=referral["status"],
        to_status=new_status,
        actor_user_id=user_id,
        actor_side=side,
        note=note,
        source=source,
        created_at=now_iso(),
    )


def _move(db: sqlite3.Connection, referral: sqlite3.Row, new_status: str, user_id: int, side: str, note: str = "", source: str = "app") -> None:
    _log(db, referral, new_status, user_id, side, note, source)
    update(db, "referrals", referral["id"], status=new_status, updated_at=now_iso())


def _notify_receivers(db: sqlite3.Connection, referral: sqlite3.Row, title: str, body: str = "") -> None:
    care.notify(db, referral["receiving_user_id"], title, body, None, "referralloop")
    if referral["receiving_organization_id"]:
        members = db.execute(
            "SELECT user_id FROM organization_members WHERE organization_id = ? AND role IN ('admin', 'coordinator', 'staff')",
            (referral["receiving_organization_id"],),
        )
        for member in members.fetchall():
            if member["user_id"] != referral["receiving_user_id"]:
                care.notify(db, member["user_id"], title, body, None, "referralloop")


def _payload(db: sqlite3.Connection, referral: sqlite3.Row, side: str) -> dict:
    referral_id = referral["id"]
    payload = dict(referral)
    patient = db.execute(
        "SELECT first_name, last_name, date_of_birth, sex FROM patients WHERE id = ?", (referral["patient_id"],)
    ).fetchone()
    sender = db.execute("SELECT first_name, last_name FROM users WHERE id = ?", (referral["referring_user_id"],)).fetchone()
    payload["patient"] = dict(patient)
    payload["referring_clinician"] = f"{sender['first_name']} {sender['last_name']}"
    payload["side"] = side
    payload["appointments"] = rows(
        db.execute("SELECT * FROM referral_appointments WHERE referral_id = ? ORDER BY created_at", (referral_id,))
    )
    outcome = one(db.execute("SELECT * FROM referral_outcomes WHERE referral_id = ?", (referral_id,)).fetchone())
    if side == "patient":
        for field in ("clinical_question", "duplicate_reason"):
            payload.pop(field)
        payload["outcome"] = {"returned": outcome is not None, "reviewed": bool(outcome and outcome["reviewed_at"])}
        payload["events"] = rows(
            db.execute(
                "SELECT to_status, created_at FROM referral_events WHERE referral_id = ? ORDER BY created_at, rowid",
                (referral_id,),
            )
        )
        return payload
    payload["outcome"] = outcome
    payload["events"] = rows(
        db.execute(
            """
            SELECT referral_events.*, users.first_name, users.last_name FROM referral_events
            LEFT JOIN users ON users.id = referral_events.actor_user_id
            WHERE referral_id = ? ORDER BY referral_events.created_at, referral_events.rowid
            """,
            (referral_id,),
        )
    )
    payload["documents"] = [
        documents.public(row)
        for row in rows(
            db.execute(
                """
                SELECT clinical_documents.* FROM clinical_documents
                JOIN referral_documents ON referral_documents.document_id = clinical_documents.id
                WHERE referral_documents.referral_id = ?
                """,
                (referral_id,),
            )
        )
    ]
    payload["exceptions"] = exceptions_for(payload)
    return payload


def exceptions_for(referral: dict, today: Optional[date] = None) -> List[str]:
    """Why this referral needs chasing. An empty list means it's on track."""
    today_text = (today or date.today()).isoformat()
    if referral["status"] in TERMINAL:
        return []
    found = []
    state = referral["status"]
    if state == "sent" and referral["respond_by"] < today_text:
        found.append("No acknowledgement by the respond-by date")
    if state in ("acknowledged", "accepted") and referral["respond_by"] < today_text:
        found.append("No appointment by the respond-by date")
    if state == "needs_information":
        found.append("The receiving service is waiting for more information")
    if state in ("declined", "redirected"):
        found.append(f"Referral {state}: needs a new plan or a recorded closure")
    if state == "no_show":
        found.append("The patient didn't attend")
    if referral["contact_attempts"] >= MAX_CONTACT_ATTEMPTS:
        found.append(f"Patient unreachable after {referral['contact_attempts']} attempts")
    for appointment in referral.get("appointments", []):
        if state in ("booked", "rescheduled") and appointment["status"] in ("booked", "rescheduled") and appointment["scheduled_at"][:10] < today_text:
            found.append("The appointment date has passed with no attendance recorded")
    if state == "attended":
        found.append("Visit done, but the outcome hasn't come back")
    if state == "outcome_returned":
        found.append("The outcome is back, but hasn't been reviewed")
    return found


def _open_duplicate(db: sqlite3.Connection, request: ReferralRequest) -> Optional[sqlite3.Row]:
    since = (date.today() - timedelta(days=DUPLICATE_WINDOW_DAYS)).isoformat()
    return db.execute(
        """
        SELECT * FROM referrals
        WHERE patient_id = ? AND lower(specialty) = lower(?) AND status NOT IN ('closed', 'cancelled')
          AND created_at >= ? ORDER BY created_at DESC LIMIT 1
        """,
        (request.patient_id, request.specialty.strip(), since),
    ).fetchone()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_referral(
    request: ReferralRequest, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    gate.open_patient(db, user, request.patient_id, "referralloop.create", CLINICAL)
    consent = care.require_consent(db, request.patient_id, "referral_sharing")
    care.require_belongs(db, "encounters", request.encounter_id, request.patient_id)
    care.require_belongs(db, "referrals", request.replaces_referral_id, request.patient_id)
    destinations = [request.receiving_organization_id, request.receiving_user_id, request.manual_destination.strip() or None]
    if sum(value is not None for value in destinations) != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose one destination: a service, a person, or a phone or fax contact."
        )
    if request.receiving_organization_id:
        organization = db.execute(
            "SELECT verification_status FROM organizations WHERE id = ?", (request.receiving_organization_id,)
        ).fetchone()
        if organization is None or organization["verification_status"] != "verified":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That service hasn't been verified yet.")
    if request.receiving_user_id is not None:
        receiver = db.execute("SELECT account_status FROM users WHERE id = ?", (request.receiving_user_id,)).fetchone()
        if receiver is None or receiver["account_status"] != "verified":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That person isn't a verified clinician.")
    if request.respond_by < date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The respond-by date can't be in the past.")
    duplicate = _open_duplicate(db, request)
    if duplicate is not None and not request.confirm_duplicate_reason.strip():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "possible_duplicate",
                "duplicate_of": duplicate["id"],
                "message": f"There's already an open {duplicate['specialty']} referral. Give a reason to send another.",
            },
        )
    for document_id in request.document_ids:
        found = db.execute(
            "SELECT 1 FROM clinical_documents WHERE id = ? AND patient_id = ?", (document_id, request.patient_id)
        ).fetchone()
        if found is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A chosen document isn't in this patient's record.")

    timestamp = now_iso()
    referral_id = insert(
        db,
        "referrals",
        patient_id=request.patient_id,
        encounter_id=request.encounter_id,
        referring_user_id=user["id"],
        receiving_organization_id=request.receiving_organization_id,
        receiving_user_id=request.receiving_user_id,
        manual_destination=request.manual_destination.strip(),
        specialty=request.specialty.strip(),
        reason=request.reason,
        clinical_question=request.clinical_question,
        priority=request.priority,
        respond_by=request.respond_by.isoformat(),
        consent_id=consent["id"],
        replaces_referral_id=request.replaces_referral_id,
        duplicate_reason=request.confirm_duplicate_reason.strip(),
        created_at=timestamp,
        updated_at=timestamp,
    )
    for document_id in request.document_ids:
        db.execute("INSERT INTO referral_documents (referral_id, document_id) VALUES (?, ?)", (referral_id, document_id))
    referral = _referral(db, referral_id)
    insert(
        db,
        "referral_events",
        referral_id=referral_id,
        to_status="sent",
        actor_user_id=user["id"],
        actor_side="referrer",
        note=f"Priority: {request.priority}",
        created_at=timestamp,
    )
    _notify_receivers(db, referral, f"New {request.priority} {request.specialty} referral", request.reason[:200])
    care.add_timeline(db, request.patient_id, "referralloop", f"Referred to {request.specialty}", request.priority, "referral", referral_id)
    return _payload(db, referral, "referrer")


def _inbox_rows(db: sqlite3.Connection, user: sqlite3.Row) -> List[sqlite3.Row]:
    # Urgent referrals come first, however old the routine ones are.
    return db.execute(
        """
        SELECT DISTINCT referrals.* FROM referrals
        LEFT JOIN organization_members AS member
          ON member.organization_id = referrals.receiving_organization_id AND member.user_id = ?
        WHERE (referrals.receiving_user_id = ? OR member.user_id IS NOT NULL)
          AND referrals.status NOT IN ('closed', 'cancelled')
          AND EXISTS (
            SELECT 1 FROM consents WHERE consents.patient_id = referrals.patient_id
              AND consents.purpose = 'referral_sharing' AND consents.revoked_at IS NULL
          )
        ORDER BY CASE referrals.priority WHEN 'urgent' THEN 0 ELSE 1 END, referrals.created_at
        """,
        (user["id"], user["id"]),
    ).fetchall()


@router.get("/inbox")
def inbox(user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    return [_payload(db, referral, "receiver") for referral in _inbox_rows(db, user)]


@router.get("/sent")
def sent(user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    found = db.execute(
        "SELECT * FROM referrals WHERE referring_user_id = ? ORDER BY updated_at DESC LIMIT 100", (user["id"],)
    ).fetchall()
    return [_payload(db, referral, "referrer") for referral in found]


@router.get("/exceptions")
def exception_queue(user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    mine = db.execute(
        "SELECT * FROM referrals WHERE referring_user_id = ? AND status NOT IN ('closed', 'cancelled')", (user["id"],)
    ).fetchall()
    payloads = [_payload(db, referral, "referrer") for referral in mine]
    payloads += [_payload(db, referral, "receiver") for referral in _inbox_rows(db, user)]
    return [referral for referral in payloads if referral["exceptions"]]


@router.get("/patients/{patient_id}")
def patient_referrals(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    access = gate.open_patient(db, user, patient_id, "referralloop.patient.list")
    found = db.execute("SELECT * FROM referrals WHERE patient_id = ? ORDER BY created_at DESC", (patient_id,)).fetchall()
    side = "referrer" if access.is_clinician and access.can_write else "patient"
    return [_payload(db, referral, side) for referral in found]


@router.get("/{referral_id}")
def get_referral(referral_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    referral = _referral(db, referral_id)
    return _payload(db, referral, _side(db, user, referral, "referralloop.read"))


def _change(db: sqlite3.Connection, user, referral: sqlite3.Row, new_status: str, note: str) -> dict:
    side = _side(db, user, referral, f"referralloop.status.{new_status}")
    current = referral["status"]
    manual = _is_manual(referral) and side == "referrer"
    allowed = (
        RECEIVER_MOVES.get(current, set()) if side == "receiver" or manual else set()
    ) | (REFERRER_MOVES.get(current, set()) if side == "referrer" else set())
    if new_status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A referral can't go from {current} to {new_status} here.")
    if new_status in ("declined", "redirected", "needs_information") and not note.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Please give a reason, so the referrer knows what to do next.")
    _move(db, referral, new_status, user["id"], "referrer" if manual else side, note, "manual" if manual else "app")
    if side == "receiver":
        care.notify(db, referral["referring_user_id"], f"Referral {new_status.replace('_', ' ')}: {referral['specialty']}", note, referral["patient_id"], "referralloop")
    else:
        _notify_receivers(db, referral, f"Referral updated: {referral['specialty']}", note)
    fresh = _referral(db, referral["id"])
    return _payload(db, fresh, side)


@router.post("/{referral_id}/acknowledge")
def acknowledge(referral_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    return _change(db, user, _referral(db, referral_id), "acknowledged", "")


@router.patch("/{referral_id}/status")
def change_status(
    referral_id: str,
    request: StatusRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    referral = _referral(db, referral_id)
    fresh = _change(db, user, referral, request.status, request.note)
    if request.status in ("attended", "no_show", "rescheduled"):
        db.execute(
            """
            UPDATE referral_appointments SET status = ? WHERE referral_id = ?
              AND status IN ('offered', 'booked', 'rescheduled')
            """,
            (request.status, referral_id),
        )
    return {**fresh, "appointments": rows(db.execute("SELECT * FROM referral_appointments WHERE referral_id = ?", (referral_id,)))}


@router.post("/{referral_id}/priority")
def change_priority(
    referral_id: str,
    request: PriorityRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    referral = _referral(db, referral_id)
    side = _side(db, user, referral, "referralloop.priority")
    if side == "patient":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the referring or receiving clinician can change priority.")
    _log(db, referral, referral["status"], user["id"], side, f"Priority {referral['priority']} to {request.priority}: {request.reason}")
    update(db, "referrals", referral_id, priority=request.priority, updated_at=now_iso())
    return _payload(db, _referral(db, referral_id), side)


@router.post("/{referral_id}/appointments", status_code=status.HTTP_201_CREATED)
def add_appointment(
    referral_id: str,
    request: AppointmentRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    referral = _referral(db, referral_id)
    new_status = "booked" if request.booked else "appointment_offered"
    fresh = _change(db, user, referral, new_status, request.location)
    insert(
        db,
        "referral_appointments",
        referral_id=referral_id,
        scheduled_at=request.scheduled_at,
        location=request.location,
        status="booked" if request.booked else "offered",
        created_at=now_iso(),
    )
    patient = db.execute("SELECT user_id FROM patients WHERE id = ?", (referral["patient_id"],)).fetchone()
    care.notify(
        db,
        patient["user_id"],
        f"Appointment for your {referral['specialty']} referral",
        f"{request.scheduled_at} {request.location}".strip(),
        referral["patient_id"],
        "referralloop",
    )
    return {**fresh, "appointments": rows(db.execute("SELECT * FROM referral_appointments WHERE referral_id = ?", (referral_id,)))}


@router.post("/{referral_id}/contact-attempts")
def record_contact_attempt(
    referral_id: str,
    request: ContactAttempt,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """A patient who can't be reached is flagged to the referrer, not dropped."""
    referral = _referral(db, referral_id)
    side = _side(db, user, referral, "referralloop.contact_attempt")
    attempts = referral["contact_attempts"] + 1
    _log(db, referral, referral["status"], user["id"], side, f"Contact attempt {attempts}: {request.note}")
    update(db, "referrals", referral_id, contact_attempts=attempts, updated_at=now_iso())
    if attempts == MAX_CONTACT_ATTEMPTS:
        care.create_task(
            db,
            referral["patient_id"],
            f"Patient unreachable for {referral['specialty']} referral",
            user["id"],
            detail=f"{attempts} attempts recorded. Please follow up.",
            owner_user_id=referral["referring_user_id"],
            escalate_to_user_id=referral["referring_user_id"],
            priority="high",
            module="referralloop",
            link_type="referral",
            link_id=referral_id,
        )
        care.notify(db, referral["referring_user_id"], "Patient unreachable", referral["specialty"], referral["patient_id"], "referralloop")
    return {"contact_attempts": attempts}


@router.post("/{referral_id}/outcome", status_code=status.HTTP_201_CREATED)
def record_outcome(
    referral_id: str,
    summary: str = Form(min_length=1, max_length=4000),
    recommendations: str = Form("", max_length=4000),
    follow_up_owner: str = Form("", max_length=200),
    return_to_referrer: bool = Form(True),
    file: Optional[UploadFile] = File(None),
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    referral = _referral(db, referral_id)
    side = _side(db, user, referral, "referralloop.outcome")
    if side != "receiver" and not (side == "referrer" and _is_manual(referral)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The receiving service records the outcome.")
    if referral["status"] != "attended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Record the visit as attended first.")
    document_id = None
    if file is not None and file.filename:
        document, _ = documents.store(
            db,
            referral["patient_id"],
            file.file.read(documents.MAX_DOCUMENT_BYTES + 1),
            file.filename,
            "consult_note",
            "clinician_entered",
            user["id"],
        )
        document_id = document["id"]
        db.execute(
            "INSERT OR IGNORE INTO referral_documents (referral_id, document_id) VALUES (?, ?)", (referral_id, document_id)
        )
    insert(
        db,
        "referral_outcomes",
        referral_id=referral_id,
        summary=summary,
        recommendations=recommendations,
        follow_up_owner=follow_up_owner,
        return_to_referrer=int(return_to_referrer),
        document_id=document_id,
        recorded_by=user["id"],
        created_at=now_iso(),
    )
    _move(db, referral, "outcome_returned", user["id"], side)
    # A note that only lands in the chart gets missed, so review is a task for a named person.
    care.create_task(
        db,
        referral["patient_id"],
        f"Review the {referral['specialty']} outcome",
        user["id"],
        owner_user_id=referral["referring_user_id"],
        escalate_to_user_id=referral["referring_user_id"],
        module="referralloop",
        link_type="referral",
        link_id=referral_id,
    )
    care.notify(db, referral["referring_user_id"], f"{referral['specialty']} outcome returned", summary[:200], referral["patient_id"], "referralloop")
    care.add_timeline(db, referral["patient_id"], "referralloop", f"{referral['specialty']} outcome returned", summary[:200], "referral", referral_id)
    return _payload(db, _referral(db, referral_id), side)


@router.post("/{referral_id}/review")
def review_outcome(
    referral_id: str,
    request: ReviewRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """The referrer's review closes the loop."""
    referral = _referral(db, referral_id)
    side = _side(db, user, referral, "referralloop.review")
    if side != "referrer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The referring clinician reviews the outcome.")
    if referral["status"] != "outcome_returned":
        raise HTTPException(status.HTTP_409_CONFLICT, "There's no outcome to review yet.")
    timestamp = now_iso()
    db.execute(
        "UPDATE referral_outcomes SET reviewed_by = ?, reviewed_at = ? WHERE referral_id = ?",
        (user["id"], timestamp, referral_id),
    )
    db.execute(
        "UPDATE tasks SET status = 'done', updated_at = ? WHERE link_type = 'referral' AND link_id = ? AND status = 'open'",
        (timestamp, referral_id),
    )
    _move(db, referral, "closed", user["id"], side, request.note or "Outcome reviewed")
    update(db, "referrals", referral_id, closed_reason="Outcome reviewed")
    care.add_timeline(db, referral["patient_id"], "referralloop", f"{referral['specialty']} referral closed", "Outcome reviewed", "referral", referral_id)
    return _payload(db, _referral(db, referral_id), side)


@router.post("/{referral_id}/close")
def close_referral(
    referral_id: str,
    request: CloseRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Closing without an outcome always needs a reason on record."""
    referral = _referral(db, referral_id)
    side = _side(db, user, referral, "referralloop.close")
    if side != "referrer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The referring clinician closes a referral.")
    if referral["status"] in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "This referral is already closed.")
    db.execute(
        "UPDATE tasks SET status = 'cancelled', updated_at = ? WHERE link_type = 'referral' AND link_id = ? AND status = 'open'",
        (now_iso(), referral_id),
    )
    _move(db, referral, "cancelled", user["id"], side, request.reason)
    update(db, "referrals", referral_id, closed_reason=request.reason)
    _notify_receivers(db, referral, f"Referral closed: {referral['specialty']}", request.reason)
    care.add_timeline(db, referral["patient_id"], "referralloop", f"{referral['specialty']} referral closed", request.reason, "referral", referral_id)
    return _payload(db, _referral(db, referral_id), side)
