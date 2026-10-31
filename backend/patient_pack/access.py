"""Who may open a patient's pack. SQLite alone decides this, and Meko is never asked.

A referral doesn't copy anything. The doctor who accepted it can open the pack while it's open and
the patient's consent stands, and this is worked out on every request, so ending either ends access
at once.
"""
import sqlite3
from typing import Optional

from fastapi import HTTPException, status

from backend.patient_pack.sync import SHARE_PURPOSE
from backend.records import access as gate
from backend.records import audit, care

OPEN_REFERRAL = ("accepted", "appointment_offered", "booked", "rescheduled", "no_show", "attended", "outcome_returned")


def accepted_referral(db: sqlite3.Connection, user: sqlite3.Row, patient_id: str) -> Optional[sqlite3.Row]:
    """The referral that lets this doctor in. Other staff at the service don't get the whole pack."""
    if user["role"] != "doctor" or user["account_status"] != "verified":
        return None
    if care.active_consent(db, patient_id, SHARE_PURPOSE) is None:
        return None
    marks = ",".join("?" for _ in OPEN_REFERRAL)
    return db.execute(
        f"""
        SELECT referrals.* FROM referrals
        JOIN referral_events ON referral_events.referral_id = referrals.id
        WHERE referrals.patient_id = ? AND referrals.status IN ({marks})
          AND referral_events.to_status = 'accepted' AND referral_events.actor_user_id = ?
        LIMIT 1
        """,
        (patient_id, *OPEN_REFERRAL, user["id"]),
    ).fetchone()


def can_open(db: sqlite3.Connection, user: sqlite3.Row, patient_id: str) -> bool:
    access = gate.lookup(db, user, patient_id)
    if access is not None and access.relationship in ("self", "clinician"):
        return True
    return accepted_referral(db, user, patient_id) is not None


def open_pack(db: sqlite3.Connection, user: sqlite3.Row, patient_id: str, action: str) -> None:
    """The one gate into a pack. Every use is audited, and a refusal looks like no such patient."""
    if not can_open(db, user, patient_id):
        audit.record_denied(user, action, patient_id, "no access to this pack")
        raise HTTPException(status.HTTP_404_NOT_FOUND, gate.NOT_FOUND)
    audit.record(db, user, action, patient_id)
