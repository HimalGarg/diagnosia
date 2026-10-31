"""The medication rule register and its pharmacist review workflow.

Rules live in the database with their evidence, reviewer and history. A rule only
produces a clinical alert once a verified pharmacist has approved it. The code
provides the workflow. The clinical decisions are the pharmacist's, so nothing
here approves anything by itself, and the starter rules begin as drafts.
"""
import json
import sqlite3
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.auth import require_doctor
from backend.db import get_db
from backend.medguard import drugs
from backend.records import audit, staff
from backend.records.store import dumps, insert, now_iso, rows

router = APIRouter(prefix="/api/addons/medguard/rules")

EVIDENCE_FIELDS = ("evidence_source", "evidence_version", "evidence_date")
Severity = Literal["high", "moderate"]


class RuleDraft(BaseModel):
    id: str = Field(min_length=3, max_length=60, pattern=r"^[a-z0-9-]+$")
    category: Literal["drug_interaction", "allergy_cross_reaction"]
    side_a: Dict[str, str]
    side_b: Dict[str, str]
    severity: Severity
    summary: str = Field(min_length=3, max_length=200)
    basis: str = Field(min_length=3, max_length=500)
    evidence_source: Optional[str] = Field(default=None, max_length=200)
    evidence_version: Optional[str] = Field(default=None, max_length=80)
    evidence_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class EvidenceUpdate(BaseModel):
    evidence_source: str = Field(min_length=3, max_length=200)
    evidence_version: str = Field(min_length=1, max_length=80)
    evidence_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ReviewNote(BaseModel):
    note: str = Field(min_length=3, max_length=500)


def seed(db: sqlite3.Connection) -> None:
    """Copies starter rules in as unverified drafts. Rows already here are never changed."""
    data = drugs.rules()
    created = now_iso()
    for rule in data["rules"]:
        _seed_one(db, rule["id"], "drug_interaction", rule["a"], rule["b"], rule["severity"], rule["summary"], rule["basis"], data["version"], created)
    for cross in data["allergy_cross_reactions"]:
        _seed_one(
            db, cross["id"], "allergy_cross_reaction", {"allergy_class": cross["allergy_class"]},
            {"drug_class": cross["drug_class"]}, "moderate", cross["summary"], cross["summary"], data["version"], created,
        )


def _seed_one(db, rule_id, category, side_a, side_b, severity, summary, basis, version, created) -> None:
    db.execute(
        """
        INSERT OR IGNORE INTO medication_rules
            (id, category, side_a, side_b, severity, summary, basis, rule_set_version, status, created_at, created_by_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, 'starter file, unreviewed')
        """,
        (rule_id, category, dumps(side_a), dumps(side_b), severity, summary, basis, version, created),
    )


def load(db: sqlite3.Connection) -> List[dict]:
    """Every rule that hasn't been retired, for the engine to sort by status."""
    found = rows(db.execute("SELECT * FROM medication_rules WHERE status != 'retired'"))
    for rule in found:
        rule["side_a"] = json.loads(rule["side_a"])
        rule["side_b"] = json.loads(rule["side_b"])
    return found


def counts(db: sqlite3.Connection) -> Dict[str, int]:
    return {row["status"]: row["total"] for row in db.execute("SELECT status, COUNT(*) AS total FROM medication_rules GROUP BY status")}


def _rule(db: sqlite3.Connection, rule_id: str) -> sqlite3.Row:
    rule = db.execute("SELECT * FROM medication_rules WHERE id = ?", (rule_id,)).fetchone()
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that rule.")
    return rule


def _name(user: sqlite3.Row) -> str:
    return f"{user['first_name']} {user['last_name']}"


def _move(db: sqlite3.Connection, rule: sqlite3.Row, new_status: str, user: sqlite3.Row, note: str, **fields) -> None:
    values = {"status": new_status, **fields}
    assignments = ", ".join(f"{column} = ?" for column in values)
    db.execute(f"UPDATE medication_rules SET {assignments} WHERE id = ?", (*values.values(), rule["id"]))
    insert(
        db,
        "medication_rule_events",
        rule_id=rule["id"],
        from_status=rule["status"],
        to_status=new_status,
        actor_id=user["id"],
        actor_name=_name(user),
        note=note,
        created_at=now_iso(),
    )
    audit.record(db, user, f"medguard.rule.{new_status}", resource_type="medication_rule", resource_id=rule["id"], reason=note[:200])


def _payload(db: sqlite3.Connection, rule: sqlite3.Row) -> dict:
    payload = dict(rule)
    payload["side_a"] = json.loads(rule["side_a"])
    payload["side_b"] = json.loads(rule["side_b"])
    payload["evidence_complete"] = all(rule[field] for field in EVIDENCE_FIELDS)
    payload["history"] = rows(
        db.execute("SELECT * FROM medication_rule_events WHERE rule_id = ? ORDER BY created_at, rowid", (rule["id"],))
    )
    return payload


@router.get("")
def list_rules(_user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    found = db.execute("SELECT * FROM medication_rules ORDER BY status, id").fetchall()
    return [_payload(db, rule) for rule in found]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_rule(request: RuleDraft, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    staff.require_profession(db, user, "pharmacist", "medguard.rule.create")
    values = request.model_dump()
    values["side_a"], values["side_b"] = dumps(request.side_a), dumps(request.side_b)
    insert(
        db,
        "medication_rules",
        **values,
        rule_set_version="local",
        status="draft",
        created_at=now_iso(),
        created_by_label=_name(user),
    )
    audit.record(db, user, "medguard.rule.created", resource_type="medication_rule", resource_id=request.id)
    return _payload(db, _rule(db, request.id))


@router.patch("/{rule_id}/evidence")
def set_evidence(
    rule_id: str, request: EvidenceUpdate, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    staff.require_profession(db, user, "pharmacist", "medguard.rule.evidence")
    rule = _rule(db, rule_id)
    if rule["status"] not in ("draft", "rejected"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft or rejected rule can be edited.")
    db.execute(
        "UPDATE medication_rules SET evidence_source = ?, evidence_version = ?, evidence_date = ? WHERE id = ?",
        (request.evidence_source, request.evidence_version, request.evidence_date, rule_id),
    )
    audit.record(db, user, "medguard.rule.evidence", resource_type="medication_rule", resource_id=rule_id)
    return _payload(db, _rule(db, rule_id))


@router.post("/{rule_id}/submit")
def submit_for_review(rule_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    staff.require_profession(db, user, "pharmacist", "medguard.rule.submit")
    rule = _rule(db, rule_id)
    if rule["status"] not in ("draft", "rejected"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft or rejected rule can be submitted.")
    if not all(rule[field] for field in EVIDENCE_FIELDS):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Add the evidence source, version and date first.")
    _move(db, rule, "pending_pharmacist_review", user, "Submitted for review", submitted_by=user["id"], submitted_at=now_iso())
    return _payload(db, _rule(db, rule_id))


@router.post("/{rule_id}/approve")
def approve_rule(
    rule_id: str, request: ReviewNote, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    """A second pharmacist decides: nobody approves what they submitted."""
    staff.require_profession(db, user, "pharmacist", "medguard.rule.approve")
    rule = _rule(db, rule_id)
    if rule["status"] != "pending_pharmacist_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a rule pending review can be approved.")
    if rule["submitted_by"] == user["id"]:
        audit.record_denied(user, "medguard.rule.approve", None, "own submission")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A different pharmacist must approve this rule.")
    if not all(rule[field] for field in EVIDENCE_FIELDS):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This rule has no complete evidence.")
    _move(
        db, rule, "approved", user, request.note,
        last_reviewed_at=now_iso(), reviewer_id=user["id"], reviewer_name=_name(user), review_note=request.note,
    )
    return _payload(db, _rule(db, rule_id))


@router.post("/{rule_id}/reject")
def reject_rule(
    rule_id: str, request: ReviewNote, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    staff.require_profession(db, user, "pharmacist", "medguard.rule.reject")
    rule = _rule(db, rule_id)
    if rule["status"] != "pending_pharmacist_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a rule pending review can be rejected.")
    _move(
        db, rule, "rejected", user, request.note,
        last_reviewed_at=now_iso(), reviewer_id=user["id"], reviewer_name=_name(user), review_note=request.note,
    )
    return _payload(db, _rule(db, rule_id))


@router.post("/{rule_id}/retire")
def retire_rule(
    rule_id: str, request: ReviewNote, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    staff.require_profession(db, user, "pharmacist", "medguard.rule.retire")
    rule = _rule(db, rule_id)
    if rule["status"] == "retired":
        raise HTTPException(status.HTTP_409_CONFLICT, "This rule is already retired.")
    _move(db, rule, "retired", user, request.note, last_reviewed_at=now_iso(), reviewer_id=user["id"], reviewer_name=_name(user))
    return _payload(db, _rule(db, rule_id))

