"""The API for ChronicCare: long-term conditions tracked across visits.

It shows what is happening over time and supports follow-up. It doesn't diagnose
or change treatment, and alerts come only from limits a clinician set for this
patient. Home readings are always labelled as patient-entered.
"""
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from backend import settings_store
from backend.auth import require_doctor, require_user
from backend.db import get_db
from backend.lablens.routes import build_trends
from backend.records import access as gate
from backend.records import care
from backend.records.access import CLINICAL, WRITE
from backend.records.store import insert, now_iso, one, rows, update

router = APIRouter(prefix="/api/addons/chroniccare")

# The limits catch typing mistakes. They say nothing about what is healthy.
MEASURES: Dict[str, dict] = {
    "bp_systolic": {"label": "Systolic blood pressure", "unit": "mmHg", "loinc": "8480-6", "min": 40, "max": 300},
    "bp_diastolic": {"label": "Diastolic blood pressure", "unit": "mmHg", "loinc": "8462-4", "min": 20, "max": 200},
    "pulse": {"label": "Pulse", "unit": "beats/min", "loinc": "8867-4", "min": 20, "max": 300},
    "glucose_fasting": {"label": "Fasting blood glucose", "unit": "mg/dL", "loinc": "1558-6", "min": 10, "max": 1500},
    "glucose_post_meal": {"label": "Blood glucose after a meal", "unit": "mg/dL", "loinc": "1521-4", "min": 10, "max": 1500},
    "weight": {"label": "Weight", "unit": "kg", "loinc": "29463-7", "min": 1, "max": 400},
    "oxygen_saturation": {"label": "Oxygen saturation", "unit": "%", "loinc": "59408-5", "min": 30, "max": 100},
    "temperature": {"label": "Temperature", "unit": "°C", "loinc": "8310-5", "min": 25, "max": 45},
    "peak_flow": {"label": "Peak flow", "unit": "L/min", "loinc": "19935-6", "min": 20, "max": 1000},
}
MeasureName = Literal[
    "bp_systolic", "bp_diastolic", "pulse", "glucose_fasting", "glucose_post_meal", "weight",
    "oxygen_saturation", "temperature", "peak_flow",
]
ADHERENCE_WINDOW_DAYS = 30


class ConditionRequest(BaseModel):
    condition_name: str = Field(min_length=1, max_length=120)
    onset_date: Optional[date] = None
    notes: str = Field(default="", max_length=500)


class MeasureRequest(BaseModel):
    measure: MeasureName
    target_text: str = Field(default="", max_length=120)
    cadence_days: Optional[int] = Field(default=None, ge=1, le=365)
    alert_low: Optional[float] = None
    alert_high: Optional[float] = None
    escalation_text: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def alerts_say_what_to_do(self):
        if (self.alert_low is not None or self.alert_high is not None) and not self.escalation_text.strip():
            raise ValueError("An alert limit needs instructions on what the patient should do.")
        return self


class PlanRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    condition_id: Optional[str] = None
    review_date: Optional[date] = None
    goals: List[str] = Field(default_factory=list, max_length=20)
    measures: List[MeasureRequest] = Field(default_factory=list, max_length=12)


class ReadingRequest(BaseModel):
    measure: MeasureName
    value: float
    effective_at: Optional[datetime] = None
    note: str = Field(default="", max_length=300)


class SymptomRequest(BaseModel):
    symptom: str = Field(min_length=1, max_length=120)
    severity: int = Field(ge=0, le=10)
    duration: str = Field(default="", max_length=80)
    triggers: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=500)
    effective_at: Optional[datetime] = None


class AdherenceRequest(BaseModel):
    medicine_name: str = Field(min_length=1, max_length=160)
    event: Literal["taken", "missed", "held"]
    effective_at: Optional[datetime] = None
    note: str = Field(default="", max_length=300)


def _when(moment: Optional[datetime]) -> str:
    return moment.isoformat(timespec="seconds") if moment else now_iso()


def _source(access: gate.Access) -> tuple:
    if access.is_clinician:
        return "clinician_entered", "accepted"
    return "patient_entered", "pending"


def _plan(db: sqlite3.Connection, plan_id: str) -> sqlite3.Row:
    plan = db.execute("SELECT * FROM care_plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that care plan.")
    return plan


def _plan_payload(db: sqlite3.Connection, plan: sqlite3.Row) -> dict:
    return {
        **dict(plan),
        "goals": rows(db.execute("SELECT * FROM care_plan_goals WHERE plan_id = ?", (plan["id"],))),
        "measures": rows(db.execute("SELECT * FROM care_plan_measures WHERE plan_id = ?", (plan["id"],))),
    }


@router.get("/measures")
def list_measures(_user=Depends(require_user)) -> dict:
    return MEASURES


@router.post("/patients/{patient_id}/conditions", status_code=status.HTTP_201_CREATED)
def add_condition(
    patient_id: str,
    request: ConditionRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "chroniccare.condition.add", CLINICAL)
    condition_id = insert(
        db,
        "condition_profiles",
        patient_id=patient_id,
        condition_name=request.condition_name,
        onset_date=request.onset_date.isoformat() if request.onset_date else None,
        managing_clinician_id=user["id"],
        notes=request.notes,
        created_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM condition_profiles WHERE id = ?", (condition_id,)).fetchone())


@router.post("/patients/{patient_id}/plans", status_code=status.HTTP_201_CREATED)
def create_plan(
    patient_id: str,
    request: PlanRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "chroniccare.plan.create", CLINICAL)
    care.require_belongs(db, "condition_profiles", request.condition_id, patient_id)
    plan_id = insert(
        db,
        "care_plans",
        patient_id=patient_id,
        condition_id=request.condition_id,
        title=request.title,
        review_date=request.review_date.isoformat() if request.review_date else None,
        created_by=user["id"],
        created_at=now_iso(),
    )
    for goal in request.goals:
        insert(db, "care_plan_goals", plan_id=plan_id, description=goal[:300])
    for measure in request.measures:
        insert(db, "care_plan_measures", plan_id=plan_id, **measure.model_dump())
    return _plan_payload(db, _plan(db, plan_id))


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    plan = _plan(db, plan_id)
    gate.open_patient(db, user, plan["patient_id"], "chroniccare.plan.approve", CLINICAL)
    if plan["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft plan can be approved.")
    db.execute(
        "UPDATE care_plans SET status = 'retired' WHERE patient_id = ? AND status = 'active' AND (condition_id IS ? OR condition_id = ?)",
        (plan["patient_id"], plan["condition_id"], plan["condition_id"]),
    )
    update(db, "care_plans", plan_id, status="active", approved_by=user["id"], approved_at=now_iso())
    care.add_timeline(db, plan["patient_id"], "chroniccare", "Care plan approved", plan["title"], "care_plan", plan_id)
    return _plan_payload(db, _plan(db, plan_id))


def _active_measures(db: sqlite3.Connection, patient_id: str) -> List[dict]:
    return rows(
        db.execute(
            """
            SELECT care_plan_measures.*, care_plans.title AS plan_title, care_plans.approved_by
            FROM care_plan_measures JOIN care_plans ON care_plans.id = care_plan_measures.plan_id
            WHERE care_plans.patient_id = ? AND care_plans.status = 'active'
            """,
            (patient_id,),
        )
    )


def _crossed(value: float, measure: dict) -> Optional[str]:
    if measure["alert_low"] is not None and value < measure["alert_low"]:
        return "below"
    if measure["alert_high"] is not None and value > measure["alert_high"]:
        return "above"
    return None


def _raise_attention(
    db: sqlite3.Connection, patient_id: str, user_id: int, details: dict, value: float, side: str, measure: dict, source_type: str
) -> dict:
    title = f"{details['label']} {value:g} {details['unit']} is {side} the limit set in the care plan"
    today = now_iso()[:10]  # stored times are UTC, so the day must be too
    already = db.execute(
        "SELECT 1 FROM tasks WHERE patient_id = ? AND module = 'chroniccare' AND link_id = ? AND created_at >= ? AND status = 'open'",
        (patient_id, measure["id"], today),
    ).fetchone()
    if already is None:
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
        care.create_task(
            db,
            patient_id,
            title,
            user_id,
            detail="Patient-entered reading: not clinician-verified." if source_type == "patient_entered" else "",
            priority="high",
            due_at=due,
            module="chroniccare",
            link_type="care_plan_measure",
            link_id=measure["id"],
            escalate_to_user_id=measure["approved_by"],
        )
        care.notify_care_team(db, patient_id, title, module="chroniccare")
    return {
        "message": measure["escalation_text"],
        "reminder": f"This isn't an emergency service. {settings_store.get('contact.emergency_message', db)}",
    }


@router.post("/patients/{patient_id}/readings", status_code=status.HTTP_201_CREATED)
def add_reading(
    patient_id: str,
    request: ReadingRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, "chroniccare.reading.add", WRITE)
    details = MEASURES[request.measure]
    if not details["min"] <= request.value <= details["max"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{request.value:g} doesn't look right for {details['label'].lower()}. Please check the number.",
        )
    source_type, review_status = _source(access)
    reading_id = insert(
        db,
        "observations",
        patient_id=patient_id,
        category="vital",
        code_system="http://loinc.org",
        code=details["loinc"],
        label=details["label"],
        value_number=request.value,
        unit=details["unit"],
        effective_at=_when(request.effective_at),
        note=request.note,
        source_type=source_type,
        review_status=review_status,
        created_at=now_iso(),
    )
    attention = None
    for measure in _active_measures(db, patient_id):
        side = _crossed(request.value, measure) if measure["measure"] == request.measure else None
        if side:
            attention = _raise_attention(db, patient_id, user["id"], details, request.value, side, measure, source_type)
    return {"id": reading_id, "source_type": source_type, "attention": attention}


@router.post("/patients/{patient_id}/symptoms", status_code=status.HTTP_201_CREATED)
def add_symptom(
    patient_id: str,
    request: SymptomRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, "chroniccare.symptom.add", WRITE)
    source_type, review_status = _source(access)
    note = " ".join(filter(None, [request.duration and f"For {request.duration}.", request.triggers and f"Trigger: {request.triggers}.", request.note]))
    symptom_id = insert(
        db,
        "observations",
        patient_id=patient_id,
        category="symptom",
        label=request.symptom,
        value_number=request.severity,
        unit="/10",
        effective_at=_when(request.effective_at),
        note=note,
        source_type=source_type,
        review_status=review_status,
        created_at=now_iso(),
    )
    return {"id": symptom_id, "source_type": source_type}


@router.post("/patients/{patient_id}/adherence", status_code=status.HTTP_201_CREATED)
def add_adherence(
    patient_id: str,
    request: AdherenceRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, "chroniccare.adherence.add", WRITE)
    event_id = insert(
        db,
        "adherence_events",
        patient_id=patient_id,
        medicine_name=request.medicine_name,
        event=request.event,
        effective_at=_when(request.effective_at),
        note=request.note,
        source_type="clinician_entered" if access.is_clinician else "patient_entered",
        recorded_by=user["id"],
    )
    return {"id": event_id}


def _adherence_summary(db: sqlite3.Connection, patient_id: str) -> List[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=ADHERENCE_WINDOW_DAYS)).isoformat()
    events = rows(
        db.execute(
            "SELECT medicine_name, event FROM adherence_events WHERE patient_id = ? AND effective_at >= ?",
            (patient_id, since),
        )
    )
    counts: Dict[str, Counter] = {}
    for event in events:
        counts.setdefault(event["medicine_name"], Counter())[event["event"]] += 1
    return [
        {"medicine": name, "taken": tally["taken"], "missed": tally["missed"], "held": tally["held"]}
        for name, tally in sorted(counts.items())
    ]


def _measure_status(measure: dict, readings: List[dict]) -> dict:
    """What a clinician sees for one measure: the latest value and what's overdue."""
    details = MEASURES[measure["measure"]]
    latest = readings[-1] if readings else None
    overdue = False
    if measure["cadence_days"]:
        if latest is None:
            overdue = True
        else:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(latest["effective_at"]).astimezone(timezone.utc)
            overdue = age.days >= measure["cadence_days"]
    side = _crossed(latest["value_number"], measure) if latest else None
    return {
        **measure,
        "label": details["label"],
        "unit": details["unit"],
        "latest": latest,
        "overdue": overdue,
        "crossed": side,
        "points": [
            {
                "date": row["effective_at"],
                "value": row["value_number"],
                "source_type": row["source_type"],
            }
            for row in readings[-60:]
        ],
    }


@router.get("/patients/{patient_id}/dashboard")
def dashboard(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    access = gate.open_patient(db, user, patient_id, "chroniccare.dashboard.read")
    conditions = rows(db.execute("SELECT * FROM condition_profiles WHERE patient_id = ?", (patient_id,)))
    plans = db.execute(
        "SELECT * FROM care_plans WHERE patient_id = ? AND (status = 'active' OR (status = 'draft' AND ? = 1)) ORDER BY created_at DESC",
        (patient_id, int(access.is_clinician)),
    ).fetchall()
    vitals = rows(
        db.execute(
            "SELECT * FROM observations WHERE patient_id = ? AND category = 'vital' ORDER BY effective_at", (patient_id,)
        )
    )
    measures = []
    for measure in _active_measures(db, patient_id):
        details = MEASURES[measure["measure"]]
        measures.append(_measure_status(measure, [row for row in vitals if row["code"] == details["loinc"]]))
    labs = rows(
        db.execute(
            """
            SELECT * FROM observations WHERE patient_id = ? AND category = 'laboratory'
              AND review_status IN ('accepted', 'corrected') ORDER BY effective_at
            """,
            (patient_id,),
        )
    )
    return {
        "relationship": access.relationship,
        "conditions": conditions,
        "plans": [_plan_payload(db, plan) for plan in plans],
        "measures": measures,
        "vitals": vitals[-40:][::-1],
        "symptoms": rows(
            db.execute(
                "SELECT * FROM observations WHERE patient_id = ? AND category = 'symptom' ORDER BY effective_at DESC LIMIT 30",
                (patient_id,),
            )
        ),
        "adherence": _adherence_summary(db, patient_id),
        "adherence_window_days": ADHERENCE_WINDOW_DAYS,
        "lab_trends": build_trends(labs),
    }
