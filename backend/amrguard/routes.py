"""The API for AMR Guard: antimicrobial stewardship for a hospital's own team.

Open only to stewardship and microbiology staff of an organization, never to
patients. It never chooses, recommends or doses an antibiotic. What a steward
writes in a review is a person's judgement, stored as written.
"""
import json
import sqlite3
from datetime import date
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from backend.amrguard import antibiogram
from backend.auth import require_doctor
from backend.db import get_db
from backend.records import access as gate
from backend.records import audit
from backend.records.store import dumps, insert, new_id, now_iso, one, rows, update

router = APIRouter(prefix="/api/addons/amrguard")

STAFF = ("admin", "steward", "microbiologist")
REVIEWERS = ("steward", "microbiologist")
# Stated in the API as well as on screen. Changing this needs a qualified partner and a new review.
DECISION_SUPPORT_STATUS = "unavailable"
MAX_CSV_BYTES = 5 * 1024 * 1024
NEXT_STATE = {"positive": "reviewed", "reviewed": "acknowledged", "acknowledged": "actioned"}


class AntibiogramRequest(BaseModel):
    period_start: date
    period_end: date
    min_isolates: int = Field(default=antibiogram.MIN_ISOLATES, ge=antibiogram.MIN_ISOLATES, le=500)


class StewardshipRequest(BaseModel):
    patient_ref: str = Field(min_length=1, max_length=80)
    culture_review_id: Optional[str] = None
    intervention: Literal["review_requested", "de_escalation_considered", "duration_review", "guideline_discussion", "other"]
    finding: str = Field(min_length=1, max_length=1000)
    recommendation: str = Field(default="", max_length=1000)
    rationale: str = Field(default="", max_length=1000)
    follow_up: str = Field(default="", max_length=500)


class StewardshipUpdate(BaseModel):
    disposition: Literal["accepted", "declined", "no_action"]
    follow_up: str = Field(default="", max_length=500)


class GuidelineRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    issuer: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=40)
    effective_date: date
    clinical_owner: str = Field(min_length=1, max_length=120)


def _results(db: sqlite3.Connection, organization_id: str, start: Optional[str] = None, end: Optional[str] = None) -> List[dict]:
    return rows(
        db.execute(
            """
            SELECT * FROM microbiology_results WHERE organization_id = ?
              AND (? IS NULL OR collected_on >= ?) AND (? IS NULL OR collected_on <= ?)
            """,
            (organization_id, start, start, end, end),
        )
    )


def _staff_gate(db: sqlite3.Connection, user, organization_id: str, roles: tuple, action: str) -> None:
    gate.require_membership(db, user, organization_id, roles, action)
    organization = db.execute("SELECT verification_status FROM organizations WHERE id = ?", (organization_id,)).fetchone()
    if organization is None or organization["verification_status"] != "verified":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "AMR Guard is only open to verified organizations.")


def _run_payload(run: sqlite3.Row) -> dict:
    payload = dict(run)
    payload["clinical_decision_support"] = DECISION_SUPPORT_STATUS
    payload["use"] = "Aggregate surveillance only. Not advice for an individual patient."
    payload["output"] = json.loads(payload["output"])
    payload["data_quality"] = json.loads(payload["data_quality"])
    return payload


@router.get("/catalog")
def get_catalog(_user=Depends(require_doctor)) -> dict:
    data = antibiogram.catalog()
    return {
        "clinical_decision_support": DECISION_SUPPORT_STATUS,
        "catalog_use": "Checking imported rows only. This list isn't a formulary or guidance on what to use.",
        "version": data["version"],
        "review_status": data["review_status"],
        "organisms": list(data["organisms"]),
        "antibiotics": data["antibiotics"],
        "standards": data["standards"],
        "columns": [*antibiogram.REQUIRED_COLUMNS, "mic", "zone_mm"],
        "method": antibiogram.METHOD,
    }


@router.post("/organizations/{organization_id}/imports", status_code=status.HTTP_201_CREATED)
def import_results(
    organization_id: str,
    file: UploadFile = File(...),
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.import")
    raw = file.file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "CSV files must be under 5 MB.")
    try:
        good, rejected, total = antibiogram.parse_csv(raw)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from None

    already = {
        (row["isolate_ref"], row["antibiotic"])
        for row in db.execute("SELECT isolate_ref, antibiotic FROM microbiology_results WHERE organization_id = ?", (organization_id,))
    }
    fresh = []
    for row in good:
        if (row["isolate_ref"], row["antibiotic"]) in already:
            rejected.append({"line": None, "problems": [f"Already imported: {row['isolate_ref']} {row['antibiotic']}"]})
        else:
            fresh.append(row)

    batch_id = insert(
        db,
        "amr_import_batches",
        organization_id=organization_id,
        filename=(file.filename or "import.csv")[:200],
        total_rows=total,
        accepted_rows=len(fresh),
        rejected_rows=len(rejected),
        errors=dumps(rejected[:200]),
        imported_by=user["id"],
        created_at=now_iso(),
    )
    for row in fresh:
        insert(db, "microbiology_results", organization_id=organization_id, batch_id=batch_id, **row)
        db.execute(
            """
            INSERT OR IGNORE INTO culture_reviews
                (id, organization_id, isolate_ref, patient_ref, organism, specimen, collected_on, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id(), organization_id, row["isolate_ref"], row["patient_ref"], row["organism"], row["specimen"], row["collected_on"], now_iso()),
        )
    return {"batch_id": batch_id, "total": total, "accepted": len(fresh), "rejected": rejected[:200], "rejected_count": len(rejected)}


@router.get("/organizations/{organization_id}/imports")
def list_imports(
    organization_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.import.list")
    return rows(
        db.execute(
            """
            SELECT id, filename, total_rows, accepted_rows, rejected_rows, created_at
            FROM amr_import_batches WHERE organization_id = ? ORDER BY created_at DESC LIMIT 50
            """,
            (organization_id,),
        )
    )


@router.post("/organizations/{organization_id}/antibiograms", status_code=status.HTTP_201_CREATED)
def run_antibiogram(
    organization_id: str,
    request: AntibiogramRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.antibiogram.run")
    if request.period_end < request.period_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The period's end is before its start.")
    results = _results(db, organization_id, request.period_start.isoformat(), request.period_end.isoformat())
    if not results:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "There are no results in that period.")
    built = antibiogram.build(results, request.min_isolates)
    run_id = insert(
        db,
        "antibiogram_runs",
        organization_id=organization_id,
        period_start=request.period_start.isoformat(),
        period_end=request.period_end.isoformat(),
        min_isolates=request.min_isolates,
        method=antibiogram.METHOD,
        output=dumps(built["table"]),
        data_quality=dumps(built["quality"]),
        created_by=user["id"],
        created_at=now_iso(),
    )
    return _run_payload(db.execute("SELECT * FROM antibiogram_runs WHERE id = ?", (run_id,)).fetchone())


@router.get("/organizations/{organization_id}/antibiograms")
def list_antibiograms(
    organization_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.antibiogram.list")
    found = db.execute(
        "SELECT * FROM antibiogram_runs WHERE organization_id = ? ORDER BY created_at DESC LIMIT 20", (organization_id,)
    ).fetchall()
    return [_run_payload(run) for run in found]


@router.post("/antibiograms/{run_id}/approve")
def approve_antibiogram(run_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """A second person signs off, so nobody approves their own run."""
    run = db.execute("SELECT * FROM antibiogram_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that antibiogram.")
    _staff_gate(db, user, run["organization_id"], REVIEWERS, "amrguard.antibiogram.approve")
    if run["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This antibiogram is already approved.")
    if run["created_by"] == user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A different steward or microbiologist must approve this.")
    update(db, "antibiogram_runs", run_id, status="approved", reviewed_by=user["id"], reviewed_at=now_iso())
    audit.record(db, user, "amrguard.antibiogram.approved", resource_type="antibiogram_run", resource_id=run_id)
    return _run_payload(db.execute("SELECT * FROM antibiogram_runs WHERE id = ?", (run_id,)).fetchone())


@router.get("/organizations/{organization_id}/trends")
def resistance_trend(
    organization_id: str,
    organism: str = Query(max_length=120),
    antibiotic: str = Query(max_length=8),
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.trend")
    return {
        "organism": organism,
        "antibiotic": antibiotic.upper(),
        "points": antibiogram.trend(_results(db, organization_id), organism, antibiotic.upper()),
        "aggregate_only": True,
    }


@router.get("/organizations/{organization_id}/culture-queue")
def culture_queue(
    organization_id: str,
    state: Optional[Literal["positive", "reviewed", "acknowledged", "actioned"]] = None,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> List[dict]:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.culture.queue")
    return rows(
        db.execute(
            """
            SELECT * FROM culture_reviews WHERE organization_id = ? AND (? IS NULL OR state = ?)
            ORDER BY CASE state WHEN 'positive' THEN 0 WHEN 'reviewed' THEN 1 WHEN 'acknowledged' THEN 2 ELSE 3 END,
                     collected_on LIMIT 200
            """,
            (organization_id, state, state),
        )
    )


@router.post("/culture-reviews/{review_id}/advance")
def advance_culture(review_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    review = db.execute("SELECT * FROM culture_reviews WHERE id = ?", (review_id,)).fetchone()
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that culture.")
    _staff_gate(db, user, review["organization_id"], STAFF, "amrguard.culture.advance")
    next_state = NEXT_STATE.get(review["state"])
    if next_state is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This culture has been fully actioned.")
    update(db, "culture_reviews", review_id, state=next_state, updated_by=user["id"], updated_at=now_iso())
    return {"id": review_id, "state": next_state}


@router.post("/organizations/{organization_id}/stewardship-reviews", status_code=status.HTTP_201_CREATED)
def add_stewardship_review(
    organization_id: str,
    request: StewardshipRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.stewardship.add")
    timestamp = now_iso()
    review_id = insert(
        db,
        "stewardship_reviews",
        organization_id=organization_id,
        **request.model_dump(),
        reviewer_id=user["id"],
        created_at=timestamp,
        updated_at=timestamp,
    )
    return one(db.execute("SELECT * FROM stewardship_reviews WHERE id = ?", (review_id,)).fetchone())


@router.get("/organizations/{organization_id}/stewardship-reviews")
def list_stewardship_reviews(
    organization_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.stewardship.list")
    return rows(
        db.execute(
            "SELECT * FROM stewardship_reviews WHERE organization_id = ? ORDER BY created_at DESC LIMIT 100",
            (organization_id,),
        )
    )


@router.patch("/stewardship-reviews/{review_id}")
def update_stewardship_review(
    review_id: str,
    request: StewardshipUpdate,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    review = db.execute("SELECT * FROM stewardship_reviews WHERE id = ?", (review_id,)).fetchone()
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that review.")
    _staff_gate(db, user, review["organization_id"], STAFF, "amrguard.stewardship.update")
    update(
        db,
        "stewardship_reviews",
        review_id,
        disposition=request.disposition,
        follow_up=request.follow_up or review["follow_up"],
        updated_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM stewardship_reviews WHERE id = ?", (review_id,)).fetchone())


@router.post("/organizations/{organization_id}/guidelines", status_code=status.HTTP_201_CREATED)
def add_guideline(
    organization_id: str,
    request: GuidelineRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.guideline.add")
    values = request.model_dump()
    values["effective_date"] = request.effective_date.isoformat()
    guideline_id = insert(
        db, "amr_guidelines", organization_id=organization_id, **values, added_by=user["id"], created_at=now_iso()
    )
    return one(db.execute("SELECT * FROM amr_guidelines WHERE id = ?", (guideline_id,)).fetchone())


@router.get("/organizations/{organization_id}/guidelines")
def list_guidelines(
    organization_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    _staff_gate(db, user, organization_id, STAFF, "amrguard.guideline.list")
    return rows(
        db.execute("SELECT * FROM amr_guidelines WHERE organization_id = ? ORDER BY effective_date DESC", (organization_id,))
    )
