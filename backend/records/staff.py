"""Staff onboarding: nobody is clinical staff because they said so.

An operator with server access vouches for the first administrator of each real
organization. That administrator invites staff and records what evidence they
checked. No code here checks a medical registration: a person does that, and this
keeps the record of who did and what they saw.
"""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from backend.auth import require_doctor, require_staff_account, require_user
from backend.db import get_db
from backend.records import access as gate
from backend.records import audit
from backend.records.store import insert, now_iso, one, rows

router = APIRouter(prefix="/api")

INVITATION_LIFETIME = timedelta(days=7)
Profession = Literal[
    "doctor", "nurse", "midwife", "pharmacist", "coordinator", "community_health_worker", "laboratory_professional",
]
MemberRole = Literal["admin", "staff", "coordinator"]


class InvitationRequest(BaseModel):
    email: EmailStr
    role: MemberRole


class AcceptInvitation(BaseModel):
    code: str = Field(min_length=8, max_length=64)


class VerificationRequest(BaseModel):
    verified_profession: Profession
    evidence_type: str = Field(min_length=2, max_length=80)
    evidence_reference: str = Field(min_length=2, max_length=200)
    notes: str = Field(default="", max_length=500)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def record_decision(
    db: sqlite3.Connection,
    user_id: int,
    decision: str,
    decided_by: Optional[int],
    decided_by_label: str,
    actor: Optional[sqlite3.Row] = None,
    organization_id: Optional[str] = None,
    evidence_type: str = "",
    evidence_reference: str = "",
    verified_profession: Optional[str] = None,
    notes: str = "",
) -> None:
    """The only place an account's status changes, so each change is always recorded."""
    new_status = {"verified": "verified", "rejected": "rejected", "suspended": "suspended", "reinstated": "verified"}[decision]
    insert(
        db,
        "staff_verifications",
        user_id=user_id,
        decision=decision,
        organization_id=organization_id,
        decided_by=decided_by,
        decided_by_label=decided_by_label,
        evidence_type=evidence_type,
        evidence_reference=evidence_reference,
        verified_profession=verified_profession,
        notes=notes,
        decided_at=now_iso(),
    )
    db.execute("UPDATE users SET account_status = ? WHERE id = ?", (new_status, user_id))
    if verified_profession:
        db.execute(
            """
            INSERT INTO practitioners (user_id, profession, verified_profession) VALUES (?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET verified_profession = excluded.verified_profession
            """,
            (user_id, verified_profession, verified_profession),
        )
    if decision in ("suspended", "rejected"):
        db.execute("UPDATE practitioners SET verified_profession = NULL WHERE user_id = ?", (user_id,))
    if decision == "suspended":
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit.record(db, actor, f"staff.{decision}", resource_type="user", resource_id=str(user_id), reason=notes or decided_by_label)


def verified_profession(db: sqlite3.Connection, user: sqlite3.Row) -> Optional[str]:
    if user["account_status"] != "verified":
        return None
    row = db.execute("SELECT verified_profession FROM practitioners WHERE user_id = ?", (user["id"],)).fetchone()
    return row["verified_profession"] if row else None


def require_profession(db: sqlite3.Connection, user: sqlite3.Row, profession: str, action: str) -> None:
    """Reviewer duties need the profession an administrator checked, not one typed into a profile."""
    if verified_profession(db, user) != profession:
        audit.record_denied(user, action, None, f"needs verified {profession}")
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"This needs an account verified as {profession.replace('_', ' ')}."
        )


def _verifying_admin(db: sqlite3.Connection, user: sqlite3.Row, organization_id: str, action: str) -> sqlite3.Row:
    gate.require_membership(db, user, organization_id, ("admin",), action)
    organization = db.execute("SELECT * FROM organizations WHERE id = ?", (organization_id,)).fetchone()
    if organization is None or organization["verification_status"] != "verified":
        audit.record_denied(user, action, None, f"organization {organization_id} not verified")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a verified organization can verify staff.")
    return organization


def _member_target(db: sqlite3.Connection, user: sqlite3.Row, organization_id: str, user_id: int, action: str) -> sqlite3.Row:
    if user_id == user["id"]:
        audit.record_denied(user, action, None, "attempted to act on own account")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't decide on your own account.")
    target = db.execute(
        """
        SELECT users.* FROM users JOIN organization_members AS member ON member.user_id = users.id
        WHERE member.organization_id = ? AND users.id = ? AND users.role = 'doctor'
        """,
        (organization_id, user_id),
    ).fetchone()
    if target is None:
        audit.record_denied(user, action, None, "target is not a member of this organization")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person isn't on this organization's staff.")
    return target


@router.get("/staff/me")
def my_status(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    return {
        "account_status": user["account_status"],
        "verified_profession": verified_profession(db, user),
        "memberships": rows(
            db.execute(
                """
                SELECT organizations.name, organizations.verification_status, member.role
                FROM organization_members AS member JOIN organizations ON organizations.id = member.organization_id
                WHERE member.user_id = ?
                """,
                (user["id"],),
            )
        ),
    }


@router.post("/organizations/{organization_id}/invitations", status_code=status.HTTP_201_CREATED)
def invite_staff(
    organization_id: str,
    request: InvitationRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _verifying_admin(db, user, organization_id, "staff.invite")
    code = secrets.token_urlsafe(12)
    expires_at = (datetime.now(timezone.utc) + INVITATION_LIFETIME).isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO organization_invitations (code_hash, organization_id, email, role, invited_by, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (_hash_code(code), organization_id, request.email.lower(), request.role, user["id"], expires_at, now_iso()),
    )
    audit.record(db, user, "staff.invited", resource_type="organization", resource_id=organization_id, reason=request.role)
    return {"code": code, "expires_at": expires_at}


@router.post("/staff/invitations/accept")
def accept_invitation(
    request: AcceptInvitation, user=Depends(require_staff_account), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    """Joins the organization. It gives no clinical access until an administrator verifies the account."""
    invitation = db.execute(
        """
        SELECT * FROM organization_invitations
        WHERE code_hash = ? AND accepted_at IS NULL AND expires_at > ?
        """,
        (_hash_code(request.code.strip()), now_iso()),
    ).fetchone()
    if invitation is None or invitation["email"] != user["email"].lower():
        audit.record_denied(user, "staff.invitation.accept", None, "invalid code or different email")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That invitation isn't valid for this account.")
    db.execute(
        """
        INSERT INTO organization_members (organization_id, user_id, role, added_by, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (organization_id, user_id) DO NOTHING
        """,
        (invitation["organization_id"], user["id"], invitation["role"], invitation["invited_by"], now_iso()),
    )
    db.execute(
        "UPDATE organization_invitations SET accepted_by = ?, accepted_at = ? WHERE code_hash = ?",
        (user["id"], now_iso(), invitation["code_hash"]),
    )
    audit.record(db, user, "staff.invitation.accepted", resource_type="organization", resource_id=invitation["organization_id"])
    return {"organization_id": invitation["organization_id"], "account_status": user["account_status"]}


@router.get("/organizations/{organization_id}/staff")
def list_staff(
    organization_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    _verifying_admin(db, user, organization_id, "staff.list")
    return rows(
        db.execute(
            """
            SELECT users.id AS user_id, users.first_name, users.last_name, users.email, users.account_status,
                   member.role, practitioners.profession AS claimed_profession, practitioners.speciality,
                   practitioners.registration_number, practitioners.verified_profession
            FROM organization_members AS member
            JOIN users ON users.id = member.user_id
            LEFT JOIN practitioners ON practitioners.user_id = users.id
            WHERE member.organization_id = ? ORDER BY users.account_status, users.last_name
            """,
            (organization_id,),
        )
    )


@router.post("/organizations/{organization_id}/staff/{user_id}/verify")
def verify_staff(
    organization_id: str,
    user_id: int,
    request: VerificationRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _verifying_admin(db, user, organization_id, "staff.verify")
    target = _member_target(db, user, organization_id, user_id, "staff.verify")
    if target["account_status"] != "pending_verification":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a pending account can be verified.")
    record_decision(
        db,
        user_id,
        "verified",
        user["id"],
        f"{user['first_name']} {user['last_name']}",
        actor=user,
        organization_id=organization_id,
        evidence_type=request.evidence_type,
        evidence_reference=request.evidence_reference,
        verified_profession=request.verified_profession,
        notes=request.notes,
    )
    return {"user_id": user_id, "account_status": "verified"}


def _decide(db, user, organization_id: str, user_id: int, decision: str, allowed_from: tuple, reason: str) -> dict:
    action = f"staff.{decision}"
    _verifying_admin(db, user, organization_id, action)
    target = _member_target(db, user, organization_id, user_id, action)
    if target["account_status"] not in allowed_from:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This account is {target['account_status'].replace('_', ' ')}.")
    record_decision(
        db, user_id, decision, user["id"], f"{user['first_name']} {user['last_name']}", actor=user,
        organization_id=organization_id, notes=reason,
    )
    return one(db.execute("SELECT id, account_status FROM users WHERE id = ?", (user_id,)).fetchone())


@router.post("/organizations/{organization_id}/staff/{user_id}/reject")
def reject_staff(
    organization_id: str, user_id: int, request: ReasonRequest,
    user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return _decide(db, user, organization_id, user_id, "rejected", ("pending_verification",), request.reason)


@router.post("/organizations/{organization_id}/staff/{user_id}/suspend")
def suspend_staff(
    organization_id: str, user_id: int, request: ReasonRequest,
    user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Takes effect at once: every session of that account ends."""
    return _decide(db, user, organization_id, user_id, "suspended", ("verified", "pending_verification"), request.reason)


@router.post("/organizations/{organization_id}/staff/{user_id}/reinstate")
def reinstate_staff(
    organization_id: str, user_id: int, request: ReasonRequest,
    user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return _decide(db, user, organization_id, user_id, "reinstated", ("suspended",), request.reason)


def operator_verify_organization(
    db: sqlite3.Connection,
    admin_email: str,
    organization_name: str,
    organization_type: str,
    evidence_type: str,
    evidence_reference: str,
    operator: str,
    profession: str = "doctor",
) -> str:
    """Server command line only: there is deliberately no web route for this first step."""
    admin = db.execute("SELECT * FROM users WHERE email = ? AND role = 'doctor'", (admin_email.lower(),)).fetchone()
    if admin is None:
        raise ValueError("That email has no staff account. They must register first.")
    if not (evidence_type.strip() and evidence_reference.strip() and operator.strip()):
        raise ValueError("Evidence type, evidence reference and operator name are all required.")
    if admin["account_status"] == "suspended":
        raise ValueError("That account is suspended.")
    timestamp = now_iso()
    organization_id = insert(
        db,
        "organizations",
        name=organization_name,
        type=organization_type,
        verification_status="verified",
        verification_reference=evidence_reference,
        verified_at=timestamp,
        created_by=admin["id"],
        created_at=timestamp,
    )
    db.execute(
        "INSERT INTO organization_members (organization_id, user_id, role, added_by, created_at) VALUES (?, ?, 'admin', ?, ?)",
        (organization_id, admin["id"], admin["id"], timestamp),
    )
    if admin["account_status"] != "verified":
        record_decision(
            db, admin["id"], "verified", None, f"operator:{operator}", organization_id=organization_id,
            evidence_type=evidence_type, evidence_reference=evidence_reference, verified_profession=profession,
        )
    audit.record(db, None, "organization.verified", resource_type="organization", resource_id=organization_id, reason=f"operator:{operator}")
    return organization_id
