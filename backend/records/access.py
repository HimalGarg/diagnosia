"""Who may open a patient's record.

A signed-in account sees nothing by default. Access comes from being the patient,
or from an active place on their care team, which the patient can revoke. People
without access get the same 404 as for a record that doesn't exist.
"""
import sqlite3
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status

from backend.records import audit

NOT_FOUND = "We couldn't find that patient."

READ, WRITE, CLINICAL = "read", "write", "clinical"


@dataclass
class Access:
    patient: sqlite3.Row
    relationship: str  # self, clinician or caregiver
    can_write: bool

    @property
    def is_clinician(self) -> bool:
        return self.relationship == "clinician"


def lookup(db: sqlite3.Connection, user: sqlite3.Row, patient_id: str) -> Optional[Access]:
    patient = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if patient is None:
        return None
    if patient["user_id"] == user["id"]:
        return Access(patient, "self", True)
    member = db.execute(
        "SELECT role, scope FROM care_team WHERE patient_id = ? AND user_id = ? AND revoked_at IS NULL",
        (patient_id, user["id"]),
    ).fetchone()
    if member is None:
        return None
    # Only verified staff can act as clinicians, however the place was granted.
    if member["role"] == "clinician" and (user["role"] != "doctor" or user["account_status"] != "verified"):
        return None
    return Access(patient, member["role"], member["scope"] == "full")


def open_patient(
    db: sqlite3.Connection, user: sqlite3.Row, patient_id: str, action: str, level: str = READ
) -> Access:
    """The one gate into a patient's record. Every use is written to the audit log."""
    access = lookup(db, user, patient_id)
    if access is None:
        audit.record_denied(user, action, patient_id, "not on this patient's care team")
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if level == WRITE and not access.can_write:
        audit.record_denied(user, action, patient_id, "read-only access")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can view this record but not change it.")
    if level == CLINICAL and not (access.is_clinician and access.can_write):
        audit.record_denied(user, action, patient_id, "clinician access needed")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A clinician on this patient's care team must do this.")
    audit.record(db, user, action, patient_id)
    return access


def require_membership(
    db: sqlite3.Connection, user: sqlite3.Row, organization_id: str, roles: tuple, action: str
) -> sqlite3.Row:
    member = db.execute(
        "SELECT * FROM organization_members WHERE organization_id = ? AND user_id = ?",
        (organization_id, user["id"]),
    ).fetchone()
    if member is None or member["role"] not in roles or user["account_status"] != "verified":
        audit.record_denied(user, action, None, f"organization {organization_id}: role needed")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role in this organization doesn't allow this.")
    audit.record(db, user, action, resource_type="organization", resource_id=organization_id)
    return member
