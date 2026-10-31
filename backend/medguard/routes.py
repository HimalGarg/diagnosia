"""The API for MedGuard: keeps a reconciled medication list and raises questions about it.

Each source stays separate and as written. Only a clinician or pharmacist can
confirm the list, and MedGuard never starts, stops or changes a medicine.
"""
import json
import sqlite3
from datetime import date
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.auth import require_doctor, require_user
from backend.careflow import events
from backend.db import get_db
from backend.medguard import drugs, engine, references
from backend.medguard import rules as rule_store
from backend.prescription.analyzer import ConfirmedMedicine, is_explainable
from backend.records import access as gate
from backend.records import audit, care
from backend.records.access import CLINICAL, WRITE
from backend.records.store import dumps, insert, now_iso, one, rows, update

router = APIRouter(prefix="/api/addons/medguard")

MedicationSource = Literal["prescription", "patient_reported", "discharge_summary", "imported_record", "clinician_entered"]


class MedicationRequest(BaseModel):
    written_name: str = Field(min_length=1, max_length=160)
    strength: str = Field(default="", max_length=60)
    dose: str = Field(default="", max_length=60)
    route: str = Field(default="", max_length=40)
    frequency: str = Field(default="", max_length=60)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    source: Optional[MedicationSource] = None


class PrescriptionImport(BaseModel):
    medicines: List[ConfirmedMedicine] = Field(max_length=30)
    encounter_id: Optional[str] = None


class IngredientCorrection(BaseModel):
    ingredients: List[str] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=300)


class ReconcileRequest(BaseModel):
    patient_id: str
    encounter_id: Optional[str] = None


class InteractionCheck(BaseModel):
    medicines: List[str] = Field(min_length=2, max_length=30)


class AlertReview(BaseModel):
    disposition: Literal["acknowledged", "action_planned", "not_applicable"]
    reason: str = Field(min_length=1, max_length=500)


class Decision(BaseModel):
    medication_id: str
    decision: Literal["continue", "hold", "stop", "unresolved"]
    note: str = Field(default="", max_length=300)


class Finalise(BaseModel):
    decisions: List[Decision] = Field(max_length=100)


def _matched(written: str) -> dict:
    return drugs.normalise(written)


def _add_record(db: sqlite3.Connection, patient_id: str, user_id: int, source: str, source_type: str, **fields) -> str:
    found = _matched(fields["written_name"])
    return insert(
        db,
        "medication_records",
        patient_id=patient_id,
        ingredients=dumps(found["ingredients"]),
        match_status=found["match_status"],
        source=source,
        source_type=source_type,
        created_by=user_id,
        created_at=now_iso(),
        **fields,
    )


def _reconciliation(db: sqlite3.Connection, reconciliation_id: str) -> sqlite3.Row:
    found = db.execute("SELECT * FROM medication_reconciliations WHERE id = ?", (reconciliation_id,)).fetchone()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that reconciliation.")
    return found


def _payload(db: sqlite3.Connection, reconciliation: sqlite3.Row) -> dict:
    alerts = rows(
        db.execute(
            """
            SELECT * FROM medication_alerts WHERE reconciliation_id = ?
            ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'moderate' THEN 1 ELSE 2 END
            """,
            (reconciliation["id"],),
        )
    )
    for alert in alerts:
        alert["evidence"] = json.loads(alert["evidence"])
    payload = dict(reconciliation)
    payload["source_snapshot"] = json.loads(payload["source_snapshot"])
    payload["summary"] = json.loads(payload["summary"]) if payload["summary"] else None
    payload["rule_coverage"] = json.loads(payload["rule_coverage"]) if payload["rule_coverage"] else None
    return {**payload, "alerts": alerts, "knowledge": knowledge_details(db)}


def knowledge_details(db: sqlite3.Connection) -> dict:
    data = drugs.rules()
    return {
        "version": data["version"],
        "drug_list_review_status": drugs.catalog()["review_status"],
        "rule_counts": rule_store.counts(db),
        "note": "Only pharmacist-approved rules raise clinical alerts. No alert never means a medicine is suitable.",
    }


@router.get("/knowledge")
def knowledge(_user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    return knowledge_details(db)


@router.post("/patients/{patient_id}/medications", status_code=status.HTTP_201_CREATED)
def add_medication(
    patient_id: str,
    request: MedicationRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, "medguard.medication.add", WRITE)
    values = request.model_dump(exclude={"source"})
    for field in ("start_date", "end_date"):
        values[field] = values[field].isoformat() if values[field] else None
    source = "clinician_entered" if access.is_clinician else "patient_reported"
    if access.is_clinician and request.source:
        source = request.source
    source_type = "clinician_entered" if access.is_clinician else "patient_entered"
    medication_id = _add_record(db, patient_id, user["id"], source, source_type, **values)
    return one(db.execute("SELECT * FROM medication_records WHERE id = ?", (medication_id,)).fetchone())


@router.post("/patients/{patient_id}/import-prescription", status_code=status.HTTP_201_CREATED)
def import_prescription(
    patient_id: str,
    request: PrescriptionImport,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Takes what the patient confirmed in the Prescription Analyzer. Unreadable items stay out."""
    gate.open_patient(db, user, patient_id, "medguard.prescription.import", WRITE)
    care.require_belongs(db, "encounters", request.encounter_id, patient_id)
    imported, skipped = [], []
    for medicine in request.medicines:
        if not is_explainable(medicine):
            skipped.append(medicine.name.value or "A medicine we couldn't read")
            continue
        imported.append(
            _add_record(
                db,
                patient_id,
                user["id"],
                "prescription",
                "ai_extracted",
                written_name=medicine.name.value.strip(),
                dose=medicine.dose.value.strip(),
                frequency=medicine.frequency.value.strip(),
                encounter_id=request.encounter_id,
                source_reference="prescription-analyzer",
            )
        )
    return {"imported": imported, "skipped": skipped}


@router.patch("/medications/{medication_id}")
def set_ingredients(
    medication_id: str,
    request: IngredientCorrection,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """A clinician's identification of a medicine. What was first recorded is kept."""
    record = db.execute("SELECT * FROM medication_records WHERE id = ?", (medication_id,)).fetchone()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that medicine.")
    gate.open_patient(db, user, record["patient_id"], "medguard.medication.identify", CLINICAL)
    ingredients = [drugs.known_ingredient(name) or name.strip().lower() for name in request.ingredients]
    update(
        db,
        "medication_records",
        medication_id,
        ingredients=dumps(ingredients),
        match_status="matched",
        review_status="corrected",
        reviewed_by=user["id"],
        reviewed_at=now_iso(),
        original_value=dumps({"ingredients": json.loads(record["ingredients"]), "reason": request.reason}),
    )
    return one(db.execute("SELECT * FROM medication_records WHERE id = ?", (medication_id,)).fetchone())


def _active_sources(db: sqlite3.Connection, patient_id: str) -> List[dict]:
    return rows(
        db.execute(
            """
            SELECT * FROM medication_records
            WHERE patient_id = ? AND status = 'active' AND review_status != 'superseded'
              AND review_status != 'rejected' ORDER BY source, written_name
            """,
            (patient_id,),
        )
    )


@router.post("/reconcile", status_code=status.HTTP_201_CREATED)
def reconcile(
    request: ReconcileRequest, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    """Runs or re-runs the checks. Reviews already recorded on an open reconciliation are kept."""
    gate.open_patient(db, user, request.patient_id, "medguard.reconcile", CLINICAL)
    care.require_belongs(db, "encounters", request.encounter_id, request.patient_id)
    records = _active_sources(db, request.patient_id)
    if not records:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Add at least one medicine first.")
    allergies = rows(db.execute("SELECT * FROM allergies WHERE patient_id = ?", (request.patient_id,)))
    rules = rule_store.load(db)
    findings = engine.review(records, allergies, rules)
    coverage = dumps(engine.coverage(findings, rules, records))
    version = drugs.rules()["version"]

    existing = db.execute(
        "SELECT * FROM medication_reconciliations WHERE patient_id = ? AND status = 'draft'", (request.patient_id,)
    ).fetchone()
    if existing is None:
        reconciliation_id = insert(
            db,
            "medication_reconciliations",
            patient_id=request.patient_id,
            encounter_id=request.encounter_id,
            source_snapshot=dumps(records),
            knowledge_version=version,
            rule_coverage=coverage,
            created_by=user["id"],
            created_at=now_iso(),
        )
    else:
        reconciliation_id = existing["id"]
        update(
            db, "medication_reconciliations", reconciliation_id,
            source_snapshot=dumps(records), knowledge_version=version, rule_coverage=coverage,
        )

    db.execute(
        "DELETE FROM medication_alerts WHERE reconciliation_id = ? AND disposition = 'pending'", (reconciliation_id,)
    )
    reviewed = {
        row["fingerprint"]
        for row in db.execute("SELECT fingerprint FROM medication_alerts WHERE reconciliation_id = ?", (reconciliation_id,))
    }
    for finding in findings:
        if finding["fingerprint"] in reviewed:
            continue
        insert(
            db,
            "medication_alerts",
            reconciliation_id=reconciliation_id,
            patient_id=request.patient_id,
            alert_type=finding["alert_type"],
            severity=finding["severity"],
            title=finding["title"],
            explanation=finding["explanation"],
            evidence=dumps(finding["evidence"]),
            rule_id=finding["rule_id"],
            authority=finding["authority"],
            knowledge_version=version,
            fingerprint=finding["fingerprint"],
        )
    waiting = db.execute("SELECT COUNT(*) FROM medication_alerts WHERE reconciliation_id = ? AND disposition = 'pending'", (reconciliation_id,)).fetchone()[0]
    flow = events.for_patient(db, request.patient_id, user)
    events.emit(db, "MedGuard", "safety_check.completed", flow, user=user, metadata={"alert_count": waiting}, operation="medguard.reconcile")
    if waiting:
        events.emit(db, "MedGuard", "safety_check.review_required", flow, "review_required", user, metadata={"alert_count": waiting, "review_required": True}, operation="medguard.reconcile")
    return _payload(db, _reconciliation(db, reconciliation_id))


@router.post("/check-interactions")
def check_interactions(
    request: InteractionCheck, _user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    """A quick look at a list of names, for example while writing a prescription."""
    records = []
    for index, name in enumerate(request.medicines):
        found = drugs.normalise(name)
        records.append(
            {
                "id": str(index),
                "written_name": name,
                "ingredients": found["ingredients"],
                "match_status": found["match_status"],
                "source": "clinician_entered",
                "strength": "",
                "frequency": "",
                "status": "active",
            }
        )
    rules = rule_store.load(db)
    findings = engine.unmatched_findings(records) + engine.interaction_findings(records, rules)
    return {
        "findings": findings,
        "coverage": engine.coverage(findings, rules, records),
        "knowledge": knowledge_details(db),
    }


@router.post("/alerts/{alert_id}/review")
def review_alert(
    alert_id: str,
    request: AlertReview,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    alert = db.execute("SELECT * FROM medication_alerts WHERE id = ?", (alert_id,)).fetchone()
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that alert.")
    gate.open_patient(db, user, alert["patient_id"], "medguard.alert.review", CLINICAL)
    update(
        db,
        "medication_alerts",
        alert_id,
        disposition=request.disposition,
        disposition_reason=request.reason,
        reviewed_by=user["id"],
        reviewed_at=now_iso(),
    )
    return {"id": alert_id, "disposition": request.disposition}


@router.post("/reconciliations/{reconciliation_id}/finalise")
def finalise(
    reconciliation_id: str,
    request: Finalise,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    reconciliation = _reconciliation(db, reconciliation_id)
    patient_id = reconciliation["patient_id"]
    gate.open_patient(db, user, patient_id, "medguard.reconciliation.finalise", CLINICAL)
    if reconciliation["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This reconciliation is already finalised.")
    unreviewed = db.execute(
        "SELECT COUNT(*) FROM medication_alerts WHERE reconciliation_id = ? AND severity = 'high' AND disposition = 'pending'",
        (reconciliation_id,),
    ).fetchone()[0]
    if unreviewed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "alerts_open", "message": f"High-severity alerts to review first: {unreviewed}."},
        )

    records = {record["id"]: record for record in _active_sources(db, patient_id)}
    decisions = {decision.medication_id: decision for decision in request.decisions}
    if set(decisions) - set(records):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A decision refers to a medicine not on this list.")

    db.execute(
        "UPDATE medication_records SET review_status = 'superseded' WHERE patient_id = ? AND source = 'reconciled' AND review_status = 'accepted'",
        (patient_id,),
    )
    timestamp = now_iso()
    confirmed, unresolved = [], []
    for medication_id, record in records.items():
        decision = decisions.get(medication_id)
        if record["source"] == "reconciled":
            continue
        if decision is None or decision.decision == "unresolved":
            unresolved.append({"medication_id": medication_id, "name": record["written_name"], "note": decision.note if decision else ""})
            continue
        update(db, "medication_records", medication_id, review_status="accepted", reviewed_by=user["id"], reviewed_at=timestamp)
        new_status = {"continue": "active", "hold": "on_hold", "stop": "stopped"}[decision.decision]
        insert(
            db,
            "medication_records",
            patient_id=patient_id,
            encounter_id=reconciliation["encounter_id"],
            written_name=record["written_name"],
            ingredients=record["ingredients"],
            match_status=record["match_status"],
            strength=record["strength"],
            dose=record["dose"],
            route=record["route"],
            frequency=record["frequency"],
            start_date=record["start_date"],
            end_date=record["end_date"],
            status=new_status,
            source="reconciled",
            source_type="clinician_entered",
            source_reference=reconciliation_id,
            review_status="accepted",
            reviewed_by=user["id"],
            reviewed_at=timestamp,
            created_by=user["id"],
            created_at=timestamp,
        )
        confirmed.append({"name": record["written_name"], "decision": decision.decision, "note": decision.note})

    summary = {
        "confirmed": confirmed,
        "unresolved": unresolved,
        "alerts": rows(
            db.execute(
                "SELECT title, severity, disposition, disposition_reason FROM medication_alerts WHERE reconciliation_id = ?",
                (reconciliation_id,),
            )
        ),
    }
    update(
        db,
        "medication_reconciliations",
        reconciliation_id,
        status="finalised",
        summary=dumps(summary),
        finalised_by=user["id"],
        finalised_at=timestamp,
    )
    audit.record(db, user, "medguard.reconciliation.finalised", patient_id, "medication_reconciliation", reconciliation_id)
    care.add_timeline(
        db,
        patient_id,
        "medguard",
        "Medication list reconciled",
        f"{len(confirmed)} confirmed, {len(unresolved)} unresolved",
        "medication_reconciliation",
        reconciliation_id,
    )
    return _payload(db, _reconciliation(db, reconciliation_id))


@router.get("/patients/{patient_id}/reconciliations")
def list_reconciliations(
    patient_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "medguard.reconciliation.list", CLINICAL)
    found = db.execute(
        "SELECT * FROM medication_reconciliations WHERE patient_id = ? ORDER BY created_at DESC", (patient_id,)
    ).fetchall()
    return [_payload(db, reconciliation) for reconciliation in found]


@router.get("/lookup")
def lookup(
    name: str = Query(min_length=2, max_length=120),
    _user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {**drugs.normalise(name), "rxnorm": references.rxnorm_ingredients(db, drugs.clean_name(name) or name)}


@router.get("/label-evidence")
def label_evidence(
    drug: str = Query(min_length=2, max_length=80),
    other: str = Query(min_length=2, max_length=80),
    _user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return references.label_mentions(db, drug, other)
