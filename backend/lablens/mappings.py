"""LOINC mapping review. A code is only a proposal until a laboratory professional approves it.

The starter codes were written by the project, not checked against a LOINC release.
They help group a patient's own results, and they never appear as a coded value in
a clinical export until approved. A missing code stays missing: nothing guesses one.
"""
import sqlite3
from typing import List, Set

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.auth import require_doctor
from backend.db import get_db
from backend.lablens import parser
from backend.records import audit, staff
from backend.records.store import insert, now_iso, one, rows

router = APIRouter(prefix="/api/addons/lablens/mappings")

CODE_SYSTEM = "http://loinc.org"
STARTER_SOURCE = "Diagnosia starter catalogue, not checked against a LOINC release"


class Approval(BaseModel):
    source_version: str = Field(min_length=3, max_length=40)
    note: str = Field(default="", max_length=300)


class Rejection(BaseModel):
    note: str = Field(min_length=3, max_length=300)


class Correction(BaseModel):
    code: str = Field(min_length=3, max_length=10)
    source_version: str = Field(min_length=3, max_length=40)
    note: str = Field(min_length=3, max_length=300)


def check_digit_valid(code: str) -> bool:
    """LOINC's published Mod 10 check. It catches typing errors, not a wrong concept."""
    if "-" not in code:
        return False
    body, _, check = code.partition("-")
    if not (body.isdigit() and check.isdigit() and len(check) == 1):
        return False
    total = 0
    for position, char in enumerate(reversed(body)):
        digit = int(char)
        if position % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10 == int(check)


def seed(db: sqlite3.Connection) -> None:
    """Adds starter proposals. Rows already here are never changed."""
    for analyte in parser.catalog()["analytes"]:
        label, code = analyte["name"], analyte["loinc"]
        db.execute(
            """
            INSERT OR IGNORE INTO loinc_mappings (id, local_label, proposed_code, source, status, created_at)
            VALUES (?, ?, ?, ?, 'proposed', ?)
            """,
            (label.lower(), label, code, STARTER_SOURCE, now_iso()),
        )


def approved_codes(db: sqlite3.Connection) -> Set[str]:
    return {row["proposed_code"] for row in db.execute("SELECT proposed_code FROM loinc_mappings WHERE status = 'approved'")}


def _mapping(db: sqlite3.Connection, mapping_id: str) -> sqlite3.Row:
    mapping = db.execute("SELECT * FROM loinc_mappings WHERE id = ?", (mapping_id,)).fetchone()
    if mapping is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that mapping.")
    return mapping


def _log(db, mapping: sqlite3.Row, new_status: str, code: str, user, note: str) -> None:
    insert(
        db,
        "loinc_mapping_events",
        mapping_id=mapping["id"],
        from_status=mapping["status"],
        to_status=new_status,
        from_code=mapping["proposed_code"],
        to_code=code,
        actor_id=user["id"],
        actor_name=f"{user['first_name']} {user['last_name']}",
        note=note,
        created_at=now_iso(),
    )
    audit.record(db, user, f"lablens.mapping.{new_status}", resource_type="loinc_mapping", resource_id=mapping["id"], reason=note[:200])


@router.get("")
def list_mappings(_user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    found = rows(db.execute("SELECT * FROM loinc_mappings ORDER BY status DESC, local_label"))
    for mapping in found:
        mapping["check_digit_valid"] = check_digit_valid(mapping["proposed_code"])
    return found


@router.post("/{mapping_id}/approve")
def approve(
    mapping_id: str, request: Approval, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    staff.require_profession(db, user, "laboratory_professional", "lablens.mapping.approve")
    mapping = _mapping(db, mapping_id)
    if mapping["status"] != "proposed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a proposed mapping can be approved.")
    if mapping["proposed_by"] == user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A different reviewer must approve your correction.")
    if not check_digit_valid(mapping["proposed_code"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This code fails the LOINC check digit. Correct it first.")
    _log(db, mapping, "approved", mapping["proposed_code"], user, request.note or "Approved")
    db.execute(
        """
        UPDATE loinc_mappings SET status = 'approved', source_version = ?, source = 'LOINC', reviewer_id = ?,
            reviewer_name = ?, reviewed_at = ?, review_note = ? WHERE id = ?
        """,
        (request.source_version, user["id"], f"{user['first_name']} {user['last_name']}", now_iso(), request.note, mapping_id),
    )
    return one(db.execute("SELECT * FROM loinc_mappings WHERE id = ?", (mapping_id,)).fetchone())


@router.post("/{mapping_id}/reject")
def reject(
    mapping_id: str, request: Rejection, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    staff.require_profession(db, user, "laboratory_professional", "lablens.mapping.reject")
    mapping = _mapping(db, mapping_id)
    if mapping["status"] not in ("proposed", "approved"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This mapping is already rejected.")
    _log(db, mapping, "rejected", mapping["proposed_code"], user, request.note)
    db.execute(
        "UPDATE loinc_mappings SET status = 'rejected', reviewer_id = ?, reviewer_name = ?, reviewed_at = ?, review_note = ? WHERE id = ?",
        (user["id"], f"{user['first_name']} {user['last_name']}", now_iso(), request.note, mapping_id),
    )
    return one(db.execute("SELECT * FROM loinc_mappings WHERE id = ?", (mapping_id,)).fetchone())


@router.post("/{mapping_id}/correct")
def correct(
    mapping_id: str, request: Correction, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    """A correction is a new proposal, which someone else must approve."""
    staff.require_profession(db, user, "laboratory_professional", "lablens.mapping.correct")
    mapping = _mapping(db, mapping_id)
    if not check_digit_valid(request.code):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That code fails the LOINC check digit.")
    _log(db, mapping, "proposed", request.code, user, request.note)
    db.execute(
        """
        UPDATE loinc_mappings SET status = 'proposed', proposed_code = ?, source = 'LOINC', source_version = ?,
            proposed_by = ?, review_note = ?, reviewer_id = NULL, reviewer_name = NULL, reviewed_at = NULL WHERE id = ?
        """,
        (request.code, request.source_version, user["id"], request.note, mapping_id),
    )
    return one(db.execute("SELECT * FROM loinc_mappings WHERE id = ?", (mapping_id,)).fetchone())
