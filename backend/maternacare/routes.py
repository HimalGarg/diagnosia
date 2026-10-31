"""The API for MaternaCare: coordinates care through pregnancy and after birth.

It keeps continuity and makes sure a worry reaches a named person. It has no
built-in visit schedule, danger-sign list or risk score, and no AI: those belong
to the clinical protocol a local obstetric team approves, entered here by a
clinician. Gestation shown is plain arithmetic on a clinician-confirmed due date.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend import settings_store
from backend.auth import require_doctor, require_user
from backend.db import get_db
from backend.records import access as gate
from backend.records import audit, care
from backend.records.access import CLINICAL, WRITE
from backend.records.store import insert, now_iso, one, rows, update

router = APIRouter(prefix="/api/addons/maternacare")

FULL_TERM_DAYS = 280


class EpisodeRequest(BaseModel):
    patient_id: str
    facility: str = Field(default="", max_length=160)
    gravida: Optional[int] = Field(default=None, ge=1, le=20)
    para: Optional[int] = Field(default=None, ge=0, le=20)
    escalation_contact: str = Field(min_length=5, max_length=200)
    concern_response_hours: int = Field(ge=1, le=72)


class DueDateRequest(BaseModel):
    estimated_due_date: date
    basis: str = Field(min_length=1, max_length=120)


class VisitRequest(BaseModel):
    stage: Literal["antenatal", "postnatal"] = "antenatal"
    subject: Literal["mother", "newborn"] = "mother"
    planned_date: date
    purpose: str = Field(min_length=1, max_length=200)


class VisitUpdate(BaseModel):
    status: Literal["attended", "missed", "cancelled"]
    notes: str = Field(default="", max_length=500)


class ObservationRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    value_number: Optional[float] = None
    value_text: Optional[str] = Field(default=None, max_length=120)
    unit: str = Field(default="", max_length=30)
    effective_at: Optional[datetime] = None


class RiskRequest(BaseModel):
    description: str = Field(min_length=1, max_length=300)


class EducationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4000)
    language: Literal["en-IN", "hi-IN"] = "en-IN"
    publisher: str = Field(min_length=1, max_length=160)
    content_version: str = Field(min_length=1, max_length=40)


class BirthPlanRequest(BaseModel):
    facility: str = Field(default="", max_length=160)
    transport: str = Field(default="", max_length=300)
    contacts: str = Field(default="", max_length=300)
    referral_arrangements: str = Field(default="", max_length=500)


class DeliveryRequest(BaseModel):
    delivered_at: datetime
    facility: str = Field(min_length=1, max_length=160)
    outcome: str = Field(min_length=1, max_length=300)
    create_newborn_record: bool = False
    newborn_first_name: str = Field(default="Baby", max_length=80)


class ConcernRequest(BaseModel):
    description: str = Field(min_length=1, max_length=1000)


class ConcernResponse(BaseModel):
    outcome: str = Field(min_length=1, max_length=500)


def gestation(estimated_due_date: Optional[str], today: Optional[date] = None) -> Optional[dict]:
    if not estimated_due_date:
        return None
    today = today or date.today()
    elapsed = FULL_TERM_DAYS - (date.fromisoformat(estimated_due_date) - today).days
    if not 0 <= elapsed <= FULL_TERM_DAYS + 42:
        return None
    return {"weeks": elapsed // 7, "days": elapsed % 7}


def _episode(db: sqlite3.Connection, episode_id: str) -> sqlite3.Row:
    episode = db.execute("SELECT * FROM maternal_episodes WHERE id = ?", (episode_id,)).fetchone()
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that care record.")
    return episode


def _clinical(db: sqlite3.Connection, user, episode_id: str, action: str) -> sqlite3.Row:
    episode = _episode(db, episode_id)
    gate.open_patient(db, user, episode["patient_id"], action, CLINICAL)
    care.require_consent(db, episode["patient_id"], "maternal_care")
    return episode


def _payload(db: sqlite3.Connection, episode: sqlite3.Row, access: gate.Access) -> dict:
    episode_id = episode["id"]
    visits = rows(db.execute("SELECT * FROM maternal_visits WHERE episode_id = ? ORDER BY planned_date", (episode_id,)))
    today = date.today().isoformat()
    for visit in visits:
        visit["overdue"] = visit["status"] == "scheduled" and visit["planned_date"] < today
    payload = {
        **dict(episode),
        "gestation": gestation(episode["estimated_due_date"]) if episode["status"] == "antenatal" else None,
        "visits": visits,
        "education": rows(db.execute("SELECT * FROM maternal_education WHERE episode_id = ?", (episode_id,))),
        "birth_plan": one(db.execute("SELECT * FROM birth_plans WHERE episode_id = ?", (episode_id,)).fetchone()),
        "delivery": one(db.execute("SELECT * FROM delivery_records WHERE episode_id = ?", (episode_id,)).fetchone()),
        "observations": rows(
            db.execute(
                "SELECT * FROM observations WHERE patient_id = ? AND category = 'maternal' ORDER BY effective_at DESC LIMIT 50",
                (episode["patient_id"],),
            )
        ),
        "risks": rows(db.execute("SELECT * FROM maternal_risks WHERE episode_id = ?", (episode_id,))),
        "concerns": rows(
            db.execute("SELECT * FROM maternal_concerns WHERE episode_id = ? ORDER BY reported_at DESC", (episode_id,))
        ),
        "emergency_help": settings_store.get("contact.emergency_message", db),
    }
    if not access.is_clinician:
        payload["risks"] = [risk for risk in payload["risks"] if risk["status"] == "active"]
    return payload


@router.post("/episodes", status_code=status.HTTP_201_CREATED)
def start_episode(
    request: EpisodeRequest, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    access = gate.open_patient(db, user, request.patient_id, "maternacare.episode.start", CLINICAL)
    consent = care.require_consent(db, request.patient_id, "maternal_care")
    try:
        episode_id = insert(
            db,
            "maternal_episodes",
            patient_id=request.patient_id,
            facility=request.facility,
            gravida=request.gravida,
            para=request.para,
            care_provider_id=user["id"],
            escalation_contact=request.escalation_contact,
            concern_response_hours=request.concern_response_hours,
            consent_id=consent["id"],
            created_at=now_iso(),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "This patient already has open maternal care.") from None
    care.add_timeline(db, request.patient_id, "maternacare", "Maternal care started", request.facility, "maternal_episode", episode_id)
    return _payload(db, _episode(db, episode_id), access)


@router.get("/patients/{patient_id}/episode")
def get_episode(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> Optional[dict]:
    access = gate.open_patient(db, user, patient_id, "maternacare.episode.read")
    episode = db.execute(
        "SELECT * FROM maternal_episodes WHERE patient_id = ? ORDER BY created_at DESC LIMIT 1", (patient_id,)
    ).fetchone()
    return _payload(db, episode, access) if episode else None


@router.post("/episodes/{episode_id}/due-date")
def confirm_due_date(
    episode_id: str,
    request: DueDateRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _clinical(db, user, episode_id, "maternacare.due_date.confirm")
    update(
        db,
        "maternal_episodes",
        episode_id,
        estimated_due_date=request.estimated_due_date.isoformat(),
        due_date_confirmed_by=user["id"],
        due_date_basis=request.basis,
    )
    return {"estimated_due_date": request.estimated_due_date.isoformat(), "gestation": gestation(request.estimated_due_date.isoformat()), "episode_id": episode["id"]}


@router.post("/episodes/{episode_id}/visits", status_code=status.HTTP_201_CREATED)
def add_visit(
    episode_id: str,
    request: VisitRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _clinical(db, user, episode_id, "maternacare.visit.add")
    visit_id = insert(
        db,
        "maternal_visits",
        episode_id=episode_id,
        stage=request.stage,
        subject=request.subject,
        planned_date=request.planned_date.isoformat(),
        purpose=request.purpose,
        created_by=user["id"],
        updated_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM maternal_visits WHERE id = ?", (visit_id,)).fetchone())


@router.patch("/visits/{visit_id}")
def update_visit(
    visit_id: str,
    request: VisitUpdate,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    visit = db.execute("SELECT * FROM maternal_visits WHERE id = ?", (visit_id,)).fetchone()
    if visit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that visit.")
    episode = _clinical(db, user, visit["episode_id"], "maternacare.visit.update")
    update(db, "maternal_visits", visit_id, status=request.status, notes=request.notes, updated_at=now_iso())
    if request.status == "missed":
        care.create_task(
            db,
            episode["patient_id"],
            f"Follow up missed visit: {visit['purpose']}",
            user["id"],
            detail="Contact the patient by phone or through the community health worker.",
            owner_user_id=episode["care_provider_id"],
            escalate_to_user_id=episode["care_provider_id"],
            priority="high",
            due_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
            module="maternacare",
            link_type="maternal_visit",
            link_id=visit_id,
        )
    return one(db.execute("SELECT * FROM maternal_visits WHERE id = ?", (visit_id,)).fetchone())


@router.post("/episodes/{episode_id}/observations", status_code=status.HTTP_201_CREATED)
def add_observation(
    episode_id: str,
    request: ObservationRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _episode(db, episode_id)
    access = gate.open_patient(db, user, episode["patient_id"], "maternacare.observation.add", WRITE)
    if request.value_number is None and not request.value_text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a value.")
    observation_id = insert(
        db,
        "observations",
        patient_id=episode["patient_id"],
        category="maternal",
        label=request.label,
        value_number=request.value_number,
        value_text=request.value_text,
        unit=request.unit,
        effective_at=request.effective_at.isoformat(timespec="seconds") if request.effective_at else now_iso(),
        source_type="clinician_entered" if access.is_clinician else "patient_entered",
        review_status="accepted" if access.is_clinician else "pending",
        reviewed_by=user["id"] if access.is_clinician else None,
        reviewed_at=now_iso() if access.is_clinician else None,
        created_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM observations WHERE id = ?", (observation_id,)).fetchone())


@router.post("/episodes/{episode_id}/risks", status_code=status.HTTP_201_CREATED)
def add_risk(
    episode_id: str,
    request: RiskRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Only what a clinician has confirmed goes on this list."""
    _clinical(db, user, episode_id, "maternacare.risk.add")
    risk_id = insert(
        db,
        "maternal_risks",
        episode_id=episode_id,
        description=request.description,
        confirmed_by=user["id"],
        confirmed_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM maternal_risks WHERE id = ?", (risk_id,)).fetchone())


@router.post("/episodes/{episode_id}/education", status_code=status.HTTP_201_CREATED)
def add_education(
    episode_id: str,
    request: EducationRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _clinical(db, user, episode_id, "maternacare.education.add")
    item_id = insert(
        db,
        "maternal_education",
        episode_id=episode_id,
        **request.model_dump(),
        approved_by=user["id"],
        approved_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM maternal_education WHERE id = ?", (item_id,)).fetchone())


@router.put("/episodes/{episode_id}/birth-plan")
def save_birth_plan(
    episode_id: str,
    request: BirthPlanRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _episode(db, episode_id)
    gate.open_patient(db, user, episode["patient_id"], "maternacare.birth_plan.save", WRITE)
    db.execute(
        """
        INSERT INTO birth_plans (episode_id, facility, transport, contacts, referral_arrangements, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (episode_id) DO UPDATE SET facility = excluded.facility, transport = excluded.transport,
            contacts = excluded.contacts, referral_arrangements = excluded.referral_arrangements,
            updated_by = excluded.updated_by, updated_at = excluded.updated_at
        """,
        (episode_id, request.facility, request.transport, request.contacts, request.referral_arrangements, user["id"], now_iso()),
    )
    return one(db.execute("SELECT * FROM birth_plans WHERE episode_id = ?", (episode_id,)).fetchone())


@router.post("/episodes/{episode_id}/delivery", status_code=status.HTTP_201_CREATED)
def record_delivery(
    episode_id: str,
    request: DeliveryRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    episode = _clinical(db, user, episode_id, "maternacare.delivery.record")
    if episode["status"] != "antenatal":
        raise HTTPException(status.HTTP_409_CONFLICT, "A delivery is already recorded.")
    mother = db.execute("SELECT * FROM patients WHERE id = ?", (episode["patient_id"],)).fetchone()
    timestamp = now_iso()
    newborn_id = None
    if request.create_newborn_record:
        newborn_id = insert(
            db,
            "patients",
            first_name=request.newborn_first_name,
            last_name=mother["last_name"],
            date_of_birth=request.delivered_at.date().isoformat(),
            sex="unknown",
            preferred_language=mother["preferred_language"],
            created_by=user["id"],
            created_at=timestamp,
        )
        insert(db, "care_team", patient_id=newborn_id, user_id=user["id"], role="clinician", scope="full", added_by=user["id"], granted_at=timestamp)
        if mother["user_id"] is not None:
            insert(db, "care_team", patient_id=newborn_id, user_id=mother["user_id"], role="caregiver", scope="full", added_by=user["id"], granted_at=timestamp)
    insert(
        db,
        "delivery_records",
        episode_id=episode_id,
        delivered_at=request.delivered_at.isoformat(timespec="seconds"),
        facility=request.facility,
        outcome=request.outcome,
        newborn_patient_id=newborn_id,
        recorded_by=user["id"],
        created_at=timestamp,
    )
    update(db, "maternal_episodes", episode_id, status="postnatal")
    care.add_timeline(db, episode["patient_id"], "maternacare", "Delivery recorded", request.facility, "maternal_episode", episode_id, request.delivered_at.isoformat(timespec="seconds"))
    return {"episode_id": episode_id, "status": "postnatal", "newborn_patient_id": newborn_id}


@router.post("/episodes/{episode_id}/concerns", status_code=status.HTTP_201_CREATED)
def raise_concern(
    episode_id: str,
    request: ConcernRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Every concern reaches a named person with a deadline. Nothing here judges how serious it is."""
    episode = _episode(db, episode_id)
    gate.open_patient(db, user, episode["patient_id"], "maternacare.concern.raise")
    due = datetime.now(timezone.utc) + timedelta(hours=episode["concern_response_hours"])
    task_id = care.create_task(
        db,
        episode["patient_id"],
        "Maternal care concern: respond",
        user["id"],
        detail=request.description,
        owner_user_id=episode["care_provider_id"],
        escalate_to_user_id=None,
        priority="high",
        due_at=due.isoformat(timespec="seconds"),
        module="maternacare",
        link_type="maternal_episode",
        link_id=episode_id,
    )
    concern_id = insert(
        db,
        "maternal_concerns",
        episode_id=episode_id,
        description=request.description,
        reported_by=user["id"],
        reported_at=now_iso(),
        task_id=task_id,
    )
    care.notify(db, episode["care_provider_id"], "Maternal care concern raised", request.description[:200], episode["patient_id"], "maternacare")
    return {
        "id": concern_id,
        "respond_by": due.isoformat(timespec="seconds"),
        "contact": episode["escalation_contact"],
        "emergency_help": settings_store.get("contact.emergency_message", db),
    }


@router.post("/concerns/{concern_id}/respond")
def respond_to_concern(
    concern_id: str,
    request: ConcernResponse,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    concern = db.execute("SELECT * FROM maternal_concerns WHERE id = ?", (concern_id,)).fetchone()
    if concern is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that concern.")
    episode = _clinical(db, user, concern["episode_id"], "maternacare.concern.respond")
    timestamp = now_iso()
    update(db, "maternal_concerns", concern_id, acknowledged_by=user["id"], acknowledged_at=timestamp, outcome=request.outcome)
    if concern["task_id"]:
        update(db, "tasks", concern["task_id"], status="done", status_note=request.outcome, updated_at=timestamp)
    audit.record(db, user, "maternacare.concern.responded", episode["patient_id"], "maternal_concern", concern_id)
    return {"id": concern_id, "acknowledged_at": timestamp}


@router.post("/episodes/{episode_id}/close")
def close_episode(episode_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    episode = _clinical(db, user, episode_id, "maternacare.episode.close")
    waiting = db.execute(
        "SELECT COUNT(*) FROM maternal_concerns WHERE episode_id = ? AND acknowledged_at IS NULL", (episode_id,)
    ).fetchone()[0]
    if waiting:
        raise HTTPException(status.HTTP_409_CONFLICT, "Respond to every open concern before closing.")
    update(db, "maternal_episodes", episode_id, status="closed")
    return {"status": "closed"}
