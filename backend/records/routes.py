"""The API for the shared patient record that every add-on tool uses."""
import hashlib
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from backend.auth import require_doctor, require_staff_account, require_user
from backend import safety, settings_store
from backend.db import get_db
from backend.records import access as gate
from backend.patient_pack import access as pack_access
from backend.patient_pack import sync as pack
from backend.records import audit, care, documents, fhir, vault
from backend.records.access import CLINICAL, WRITE
from backend.records.store import clean, insert, now_iso, one, rows, update

router = APIRouter(prefix="/api")

SHARE_CODE_LIFETIME = timedelta(hours=24)
MAX_LIST = 200


class PatientDetails(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    date_of_birth: Optional[date] = None
    sex: Optional[Literal["female", "male", "other", "unknown"]] = None
    phone: Optional[str] = Field(default=None, max_length=30)
    preferred_language: Literal["en-IN", "hi-IN"] = "en-IN"


class PatientUpdate(BaseModel):
    date_of_birth: Optional[date] = None
    sex: Optional[Literal["female", "male", "other", "unknown"]] = None
    phone: Optional[str] = Field(default=None, max_length=30)
    preferred_language: Optional[Literal["en-IN", "hi-IN"]] = None


class IdentifierDetails(BaseModel):
    type: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=80)
    issuer: Optional[str] = Field(default=None, max_length=120)


class ShareCodeRequest(BaseModel):
    purpose: Literal["clinician", "caregiver", "claim"]
    scope: Literal["full", "read"] = "full"


class JoinRequest(BaseModel):
    code: str = Field(min_length=8, max_length=64)


class ConsentRequest(BaseModel):
    purpose: Literal[
        "ai_processing", "ai_scribe", "transcript_retention", "referral_sharing", "external_messaging",
        "patient_pack", "pack_sharing",
    ]
    scope: str = Field(default="", max_length=200)


class EncounterRequest(BaseModel):
    type: Literal["outpatient", "inpatient", "emergency", "telehealth", "home"] = "outpatient"
    reason: str = Field(default="", max_length=500)
    organization_id: Optional[str] = None


class EncounterStatus(BaseModel):
    status: Literal["planned", "in_progress", "finished", "cancelled"]


class AllergyRequest(BaseModel):
    substance: str = Field(min_length=1, max_length=120)
    reaction: str = Field(default="", max_length=200)
    severity: Literal["mild", "moderate", "severe", "unknown"] = "unknown"


class TaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=1000)
    audience: Literal["patient", "care_team"] = "care_team"
    priority: Literal["routine", "high"] = "routine"
    due_at: Optional[datetime] = None
    owner_user_id: Optional[int] = None
    escalate_to_user_id: Optional[int] = None


class TaskStatus(BaseModel):
    status: Literal["open", "done", "missed", "blocked", "cancelled"]
    note: str = Field(default="", max_length=500)


class OrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    type: Literal["clinic", "hospital", "laboratory", "pharmacy"]
    address: Optional[str] = Field(default=None, max_length=300)
    phone: Optional[str] = Field(default=None, max_length=30)
    specialities: str = Field(default="", max_length=300)


class MemberRequest(BaseModel):
    email: str = Field(max_length=254)
    role: Literal["admin", "staff", "coordinator"]


class PractitionerProfile(BaseModel):
    profession: Literal[
        "doctor", "nurse", "midwife", "pharmacist", "coordinator", "community_health_worker"
    ] = "doctor"
    speciality: Optional[str] = Field(default=None, max_length=120)
    registration_number: Optional[str] = Field(default=None, max_length=60)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _patient_payload(patient: sqlite3.Row, relationship: str) -> dict:
    return {**dict(patient), "relationship": relationship}


# Patients


@router.post("/patients", status_code=status.HTTP_201_CREATED)
def create_patient(
    details: PatientDetails, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    timestamp = now_iso()
    patient_id = insert(
        db,
        "patients",
        first_name=details.first_name.strip(),
        last_name=details.last_name.strip(),
        date_of_birth=details.date_of_birth.isoformat() if details.date_of_birth else None,
        sex=details.sex,
        phone=details.phone,
        preferred_language=details.preferred_language,
        created_by=user["id"],
        created_at=timestamp,
    )
    insert(
        db,
        "care_team",
        patient_id=patient_id,
        user_id=user["id"],
        role="clinician",
        scope="full",
        added_by=user["id"],
        granted_at=timestamp,
    )
    audit.record(db, user, "patient.create", patient_id)
    patient = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    return _patient_payload(patient, "clinician")


@router.post("/patients/me", status_code=status.HTTP_201_CREATED)
def create_my_record(
    details: PatientDetails, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    if user["role"] != "patient":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only patient accounts have a personal record.")
    existing = db.execute("SELECT id FROM patients WHERE user_id = ?", (user["id"],)).fetchone()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a record.")
    patient_id = insert(
        db,
        "patients",
        user_id=user["id"],
        first_name=details.first_name.strip(),
        last_name=details.last_name.strip(),
        date_of_birth=details.date_of_birth.isoformat() if details.date_of_birth else None,
        sex=details.sex,
        phone=details.phone,
        preferred_language=details.preferred_language,
        created_by=user["id"],
        created_at=now_iso(),
    )
    audit.record(db, user, "patient.create_self", patient_id)
    return _patient_payload(db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone(), "self")


@router.get("/patients")
def list_patients(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    result = db.execute(
        """
        SELECT patients.*, 'self' AS relationship FROM patients WHERE user_id = ?
        UNION ALL
        SELECT patients.*, care_team.role AS relationship FROM patients
        JOIN care_team ON care_team.patient_id = patients.id
        WHERE care_team.user_id = ? AND care_team.revoked_at IS NULL
          AND (care_team.role != 'clinician' OR ? = 'verified')
        ORDER BY last_name, first_name LIMIT ?
        """,
        (user["id"], user["id"], user["account_status"], MAX_LIST),
    )
    return rows(result)


@router.post("/patients/join")
def join_with_code(
    request: JoinRequest, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    code = db.execute(
        "SELECT * FROM share_codes WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
        (_hash_code(request.code.strip()), now_iso()),
    ).fetchone()
    if code is None:
        audit.record_denied(user, "patient.join", None, "invalid or expired code")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That code isn't valid or has expired.")
    patient_id = code["patient_id"]

    if code["purpose"] == "claim":
        if user["role"] != "patient":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in with a patient account to claim this record.")
        if db.execute("SELECT 1 FROM patients WHERE user_id = ?", (user["id"],)).fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "This account already has a record.")
        update(db, "patients", patient_id, user_id=user["id"])
    else:
        if code["purpose"] == "clinician" and (user["role"] != "doctor" or user["account_status"] != "verified"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This code is for a verified clinician account.")
        db.execute(
            "UPDATE care_team SET revoked_at = ? WHERE patient_id = ? AND user_id = ? AND revoked_at IS NULL",
            (now_iso(), patient_id, user["id"]),
        )
        insert(
            db,
            "care_team",
            patient_id=patient_id,
            user_id=user["id"],
            role=code["purpose"],
            scope=code["scope"],
            added_by=code["created_by"],
            granted_at=now_iso(),
        )
    db.execute("UPDATE share_codes SET used_at = ? WHERE code_hash = ?", (now_iso(), code["code_hash"]))
    audit.record(db, user, f"patient.join.{code['purpose']}", patient_id)
    return {"patient_id": patient_id}


@router.get("/patients/{patient_id}")
def get_patient(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    access = gate.open_patient(db, user, patient_id, "patient.read")
    return _patient_payload(access.patient, access.relationship)


@router.patch("/patients/{patient_id}")
def update_patient(
    patient_id: str,
    changes: PatientUpdate,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "patient.update", WRITE)
    values = clean(changes.model_dump())
    if "date_of_birth" in values:
        values["date_of_birth"] = values["date_of_birth"].isoformat()
    if values:
        update(db, "patients", patient_id, **values)
    return one(db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone())


@router.get("/patients/{patient_id}/identifiers")
def list_identifiers(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "patient.identifiers.read", WRITE)
    return rows(db.execute("SELECT * FROM patient_identifiers WHERE patient_id = ?", (patient_id,)))


@router.post("/patients/{patient_id}/identifiers", status_code=status.HTTP_201_CREATED)
def add_identifier(
    patient_id: str,
    details: IdentifierDetails,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "patient.identifiers.add", WRITE)
    try:
        identifier_id = insert(db, "patient_identifiers", patient_id=patient_id, **details.model_dump())
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "This identifier is already recorded.") from None
    return {"id": identifier_id, "verified": False}


# Care team and sharing


@router.post("/patients/{patient_id}/share-codes", status_code=status.HTTP_201_CREATED)
def create_share_code(
    patient_id: str,
    request: ShareCodeRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, f"patient.share.{request.purpose}", WRITE)
    if access.relationship == "caregiver":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the patient or their clinician can share this record.")
    if request.purpose == "claim" and not access.is_clinician:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a clinician can issue a code to claim a record.")
    if request.purpose == "claim" and access.patient["user_id"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This record already belongs to an account.")
    if request.purpose == "caregiver" and access.relationship != "self" and access.patient["user_id"] is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the patient can give a caregiver access.")
    code = secrets.token_urlsafe(9)
    expires_at = (datetime.now(timezone.utc) + SHARE_CODE_LIFETIME).isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO share_codes (code_hash, patient_id, purpose, scope, created_by, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (_hash_code(code), patient_id, request.purpose, request.scope, user["id"], expires_at),
    )
    return {"code": code, "expires_at": expires_at}


@router.get("/patients/{patient_id}/care-team")
def list_care_team(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "careteam.read")
    return rows(
        db.execute(
            """
            SELECT care_team.id, care_team.user_id, care_team.role, care_team.scope, care_team.granted_at,
                   users.first_name, users.last_name
            FROM care_team JOIN users ON users.id = care_team.user_id
            WHERE care_team.patient_id = ? AND care_team.revoked_at IS NULL
            ORDER BY care_team.granted_at
            """,
            (patient_id,),
        )
    )


@router.delete("/patients/{patient_id}/care-team/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_care_team_member(
    patient_id: str,
    member_id: str,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> None:
    access = gate.open_patient(db, user, patient_id, "careteam.remove")
    member = db.execute(
        "SELECT * FROM care_team WHERE id = ? AND patient_id = ? AND revoked_at IS NULL", (member_id, patient_id)
    ).fetchone()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person isn't on this care team.")
    if access.relationship != "self" and member["user_id"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the patient can remove someone else.")
    update(db, "care_team", member_id, revoked_at=now_iso())


# Consent


@router.get("/patients/{patient_id}/consents")
def list_consents(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    gate.open_patient(db, user, patient_id, "consent.read")
    return {
        "purposes": care.CONSENT_PURPOSES,
        "consents": rows(
            db.execute("SELECT * FROM consents WHERE patient_id = ? ORDER BY granted_at DESC", (patient_id,))
        ),
    }


@router.post("/patients/{patient_id}/consents", status_code=status.HTTP_201_CREATED)
def grant_consent(
    patient_id: str,
    request: ConsentRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, f"consent.grant.{request.purpose}", WRITE)
    if access.relationship == "caregiver":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the patient or their clinician can record consent.")
    existing = care.active_consent(db, patient_id, request.purpose)
    if existing is not None:
        return dict(existing)
    consent_id = insert(
        db,
        "consents",
        patient_id=patient_id,
        purpose=request.purpose,
        scope=request.scope,
        method="patient_in_app" if access.relationship == "self" else "clinician_recorded",
        policy_version=care.POLICY_VERSION,
        recorded_by=user["id"],
        granted_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM consents WHERE id = ?", (consent_id,)).fetchone())


@router.post("/patients/{patient_id}/consents/{consent_id}/revoke")
def revoke_consent(
    patient_id: str,
    consent_id: str,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, "consent.revoke", WRITE)
    if access.relationship == "caregiver":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the patient or their clinician can revoke consent.")
    consent = db.execute(
        "SELECT * FROM consents WHERE id = ? AND patient_id = ? AND revoked_at IS NULL", (consent_id, patient_id)
    ).fetchone()
    if consent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That consent isn't active.")
    update(db, "consents", consent_id, revoked_at=now_iso(), revoked_by=user["id"])
    if consent["purpose"] == pack.STORE_PURPOSE:
        pack.request_erasure(db, patient_id)
    return {"revoked": True}


# Encounters and the patient journey


@router.get("/patients/{patient_id}/encounters")
def list_encounters(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "encounter.list")
    return rows(
        db.execute(
            "SELECT * FROM encounters WHERE patient_id = ? ORDER BY started_at DESC LIMIT ?", (patient_id, MAX_LIST)
        )
    )


@router.post("/patients/{patient_id}/encounters", status_code=status.HTTP_201_CREATED)
def create_encounter(
    patient_id: str,
    request: EncounterRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "encounter.create", CLINICAL)
    if request.organization_id:
        gate.require_membership(db, user, request.organization_id, ("admin", "staff", "coordinator"), "encounter.organization")
    encounter_id = insert(
        db,
        "encounters",
        patient_id=patient_id,
        organization_id=request.organization_id,
        type=request.type,
        reason=request.reason,
        started_at=now_iso(),
        created_by=user["id"],
    )
    care.add_timeline(db, patient_id, "encounters", f"{request.type.title()} visit", request.reason, "encounter", encounter_id)
    return one(db.execute("SELECT * FROM encounters WHERE id = ?", (encounter_id,)).fetchone())


@router.patch("/patients/{patient_id}/encounters/{encounter_id}")
def update_encounter(
    patient_id: str,
    encounter_id: str,
    request: EncounterStatus,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "encounter.update", CLINICAL)
    encounter = db.execute(
        "SELECT * FROM encounters WHERE id = ? AND patient_id = ?", (encounter_id, patient_id)
    ).fetchone()
    if encounter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that encounter.")
    ended = now_iso() if request.status in ("finished", "cancelled") else None
    update(db, "encounters", encounter_id, status=request.status, ended_at=ended)
    return one(db.execute("SELECT * FROM encounters WHERE id = ?", (encounter_id,)).fetchone())


@router.get("/patients/{patient_id}/timeline")
def timeline(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    gate.open_patient(db, user, patient_id, "timeline.read")
    return rows(
        db.execute(
            "SELECT * FROM timeline_events WHERE patient_id = ? ORDER BY occurred_at DESC LIMIT ?",
            (patient_id, MAX_LIST),
        )
    )


@router.get("/patients/{patient_id}/observations")
def list_observations(
    patient_id: str,
    category: Optional[Literal["laboratory"]] = None,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "observation.list")
    return rows(
        db.execute(
            """
            SELECT * FROM observations
            WHERE patient_id = ? AND (? IS NULL OR category = ?)
              AND review_status IN ('accepted', 'corrected', 'pending')
            ORDER BY effective_at DESC LIMIT ?
            """,
            (patient_id, category, category, MAX_LIST),
        )
    )


@router.get("/patients/{patient_id}/allergies")
def list_allergies(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "allergy.list")
    return rows(db.execute("SELECT * FROM allergies WHERE patient_id = ? ORDER BY created_at DESC", (patient_id,)))


@router.post("/patients/{patient_id}/allergies", status_code=status.HTTP_201_CREATED)
def add_allergy(
    patient_id: str,
    request: AllergyRequest,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, "allergy.add", WRITE)
    allergy_id = insert(
        db,
        "allergies",
        patient_id=patient_id,
        **request.model_dump(),
        source_type="clinician_entered" if access.is_clinician else "patient_entered",
        recorded_by=user["id"],
        created_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM allergies WHERE id = ?", (allergy_id,)).fetchone())


# Documents


@router.get("/patients/{patient_id}/documents")
def list_documents(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "document.list")
    return [
        documents.public(row)
        for row in rows(
            db.execute(
                "SELECT * FROM clinical_documents WHERE patient_id = ? AND retention_status = 'retained' ORDER BY created_at DESC",
                (patient_id,),
            )
        )
    ]


@router.post("/patients/{patient_id}/documents", status_code=status.HTTP_201_CREATED)
def upload_document(
    patient_id: str,
    file: UploadFile = File(...),
    document_type: str = Form("other", max_length=40),
    description: str = Form("", max_length=2000),
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, "document.upload", WRITE)
    document, _ = documents.store(
        db,
        patient_id,
        file.file.read(documents.MAX_DOCUMENT_BYTES + 1),
        file.filename,
        document_type,
        "clinician_entered" if access.is_clinician else "patient_entered",
        user["id"],
    )
    if description.strip():
        update(db, "clinical_documents", document["id"], description=description.strip())
    pack.enqueue(db, patient_id, "document", document["id"])
    return documents.public(document)


@router.get("/documents/{document_id}/file")
def download_document(
    document_id: str,
    inline: bool = Query(False),
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    document = one(db.execute("SELECT * FROM clinical_documents WHERE id = ?", (document_id,)).fetchone())
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that document.")
    allowed = (
        gate.lookup(db, user, document["patient_id"]) is not None
        or _shared_by_referral(db, user, document_id)
        or pack_access.accepted_referral(db, user, document["patient_id"]) is not None
    )
    if not allowed:
        audit.record_denied(user, "document.download", document["patient_id"], "no access")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that document.")
    try:
        data = documents.read(document)
    except vault.IntegrityFailure:
        audit.record_denied(user, "document.integrity_failure", document["patient_id"], f"document {document_id}")
        raise
    audit.record(db, user, "document.download", document["patient_id"], "document", document_id)
    disposition = "inline" if inline else "attachment"
    return Response(
        data,
        media_type=document["content_type"],
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(document['filename'])}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


def _shared_by_referral(db: sqlite3.Connection, user: sqlite3.Row, document_id: str) -> bool:
    """A receiving service sees only the files placed in the referral it received."""
    return (
        db.execute(
            """
            SELECT 1 FROM referral_documents
            JOIN referrals ON referrals.id = referral_documents.referral_id
            LEFT JOIN organization_members AS member
              ON member.organization_id = referrals.receiving_organization_id AND member.user_id = ?
            WHERE referral_documents.document_id = ?
              AND (referrals.receiving_user_id = ? OR member.user_id IS NOT NULL)
              AND referrals.status NOT IN ('draft', 'declined', 'redirected', 'cancelled')
              AND ? = 'verified'
              AND EXISTS (
                SELECT 1 FROM consents WHERE consents.patient_id = referrals.patient_id
                  AND consents.purpose = 'referral_sharing' AND consents.revoked_at IS NULL
              )
            """,
            (user["id"], document_id, user["id"], user["account_status"]),
        ).fetchone()
        is not None
    )


# Tasks and notifications


def _visible_tasks(db: sqlite3.Connection, patient_id: str, relationship: str) -> List[dict]:
    audience = "" if relationship == "clinician" else "AND audience = 'patient'"
    tasks = rows(
        db.execute(
            f"""
            SELECT * FROM tasks WHERE patient_id = ? AND status != 'held' {audience}
            ORDER BY CASE priority WHEN 'high' THEN 0 ELSE 1 END, due_at IS NULL, due_at LIMIT ?
            """,
            (patient_id, MAX_LIST),
        )
    )
    timestamp = now_iso()
    for task in tasks:
        task["overdue"] = bool(task["due_at"] and task["due_at"] < timestamp and task["status"] in ("open", "blocked"))
    return tasks


@router.get("/patients/{patient_id}/tasks")
def list_tasks(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    access = gate.open_patient(db, user, patient_id, "task.list")
    return _visible_tasks(db, patient_id, access.relationship)


@router.post("/patients/{patient_id}/tasks", status_code=status.HTTP_201_CREATED)
def add_task(
    patient_id: str,
    request: TaskRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.open_patient(db, user, patient_id, "task.create", CLINICAL)
    task_id = care.create_task(
        db,
        patient_id,
        request.title,
        user["id"],
        detail=request.detail,
        audience=request.audience,
        owner_user_id=request.owner_user_id,
        escalate_to_user_id=request.escalate_to_user_id or user["id"],
        priority=request.priority,
        due_at=request.due_at.isoformat() if request.due_at else None,
    )
    return one(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


@router.post("/tasks/{task_id}/status")
def set_task_status(
    task_id: str,
    request: TaskStatus,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that task.")
    access = gate.open_patient(db, user, task["patient_id"], "task.update")
    allowed = access.is_clinician or (task["audience"] == "patient" and access.can_write)
    if not allowed or task["status"] == "held":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change this task.")
    if request.status == "cancelled" and not access.is_clinician:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the care team can cancel a task.")
    update(db, "tasks", task_id, status=request.status, status_note=request.note, updated_at=now_iso())
    if request.status in ("missed", "blocked") and task["priority"] == "high":
        message = f"{task['title']}: marked {request.status}"
        if task["escalate_to_user_id"] is not None:
            care.notify(db, task["escalate_to_user_id"], message, request.note, task["patient_id"], task["module"])
        else:
            care.notify_care_team(db, task["patient_id"], message, request.note, task["module"])
    return one(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


@router.get("/notifications")
def list_notifications(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> List[dict]:
    care.escalate_overdue(db)
    return rows(
        db.execute(
            "SELECT * FROM notifications WHERE recipient_user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user["id"], 50),
        )
    )


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_notification_read(
    notification_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> Response:
    db.execute(
        "UPDATE notifications SET read_at = ? WHERE id = ? AND recipient_user_id = ?",
        (now_iso(), notification_id, user["id"]),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Practitioners and organizations


@router.put("/practitioner")
def save_practitioner(
    profile: PractitionerProfile, user=Depends(require_staff_account), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    """What a person says about themselves. Once verified, profession and registration are locked."""
    current = db.execute("SELECT * FROM practitioners WHERE user_id = ?", (user["id"],)).fetchone()
    changed = current is not None and (
        current["profession"] != profile.profession or current["registration_number"] != profile.registration_number
    )
    if user["account_status"] == "verified" and changed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ask your organization administrator to review this change.")
    db.execute(
        """
        INSERT INTO practitioners (user_id, profession, speciality, registration_number)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (user_id) DO UPDATE SET profession = excluded.profession,
            speciality = excluded.speciality, registration_number = excluded.registration_number
        """,
        (user["id"], profile.profession, profile.speciality, profile.registration_number),
    )
    return one(db.execute("SELECT * FROM practitioners WHERE user_id = ?", (user["id"],)).fetchone())


@router.get("/practitioner")
def get_practitioner(user=Depends(require_staff_account), db: sqlite3.Connection = Depends(get_db)) -> dict:
    row = db.execute("SELECT * FROM practitioners WHERE user_id = ?", (user["id"],)).fetchone()
    return one(row) or {"user_id": user["id"], "profession": "doctor", "speciality": None, "registration_number": None, "verified_profession": None}


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def create_organization(
    request: OrganizationRequest, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    organization_id = insert(db, "organizations", **request.model_dump(), created_by=user["id"], created_at=now_iso())
    db.execute(
        "INSERT INTO organization_members (organization_id, user_id, role, added_by, created_at) VALUES (?, ?, 'admin', ?, ?)",
        (organization_id, user["id"], user["id"], now_iso()),
    )
    audit.record(db, user, "organization.create", resource_type="organization", resource_id=organization_id)
    return one(db.execute("SELECT * FROM organizations WHERE id = ?", (organization_id,)).fetchone())


@router.get("/organizations")
def list_organizations(
    mine: bool = Query(False), user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    """The directory of services a referral can be sent to, or the caller's own memberships."""
    return rows(
        db.execute(
            """
            SELECT organizations.id, organizations.name, organizations.type, organizations.address,
                   organizations.phone, organizations.specialities, organizations.verification_status,
                   member.role AS my_role
            FROM organizations
            LEFT JOIN organization_members AS member
              ON member.organization_id = organizations.id AND member.user_id = ?
            WHERE ? = 0 OR member.user_id IS NOT NULL
            ORDER BY organizations.name LIMIT ?
            """,
            (user["id"], int(mine), MAX_LIST),
        )
    )


@router.post("/organizations/{organization_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(
    organization_id: str,
    request: MemberRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    gate.require_membership(db, user, organization_id, ("admin",), "organization.member.add")
    member = db.execute(
        "SELECT id FROM users WHERE email = ? AND role = 'doctor' AND account_status = 'verified'",
        (request.email.lower(),),
    ).fetchone()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no verified staff account with that email.")
    if member["id"] == user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change your own role.")
    previous = db.execute(
        "SELECT role FROM organization_members WHERE organization_id = ? AND user_id = ?", (organization_id, member["id"])
    ).fetchone()
    db.execute(
        """
        INSERT INTO organization_members (organization_id, user_id, role, added_by, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (organization_id, user_id) DO UPDATE SET role = excluded.role
        """,
        (organization_id, member["id"], request.role, user["id"], now_iso()),
    )
    audit.record(
        db, user, "organization.role_changed", resource_type="user", resource_id=str(member["id"]),
        reason=f"{organization_id}: {previous['role'] if previous else 'none'} to {request.role}",
    )
    return {"user_id": member["id"], "role": request.role}


@router.get("/system/status")
def system_status(_user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    return safety.public_status(db)


@router.get("/system/readiness")
def system_readiness(user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """What still stands in the way of real patients. For administrators of verified organizations."""
    if not settings_store.is_platform_admin(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is for administrators of verified organizations.")
    return safety.report(db)


# Accountability and export


@router.get("/patients/{patient_id}/audit-events")
def list_audit_events(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    """Patients can see who opened their record, not just staff."""
    access = gate.open_patient(db, user, patient_id, "audit.read")
    if access.relationship == "caregiver":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the patient or their clinicians can see this.")
    return rows(
        db.execute(
            """
            SELECT audit_events.action, audit_events.outcome, audit_events.created_at,
                   users.first_name, users.last_name
            FROM audit_events LEFT JOIN users ON users.id = audit_events.actor_user_id
            WHERE audit_events.patient_id = ? ORDER BY audit_events.created_at DESC, audit_events.rowid DESC LIMIT ?
            """,
            (patient_id, MAX_LIST),
        )
    )


@router.get("/patients/{patient_id}/fhir")
def export_fhir(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    access = gate.open_patient(db, user, patient_id, "fhir.export")
    return fhir.patient_bundle(db, dict(access.patient))


@router.get("/consent-purposes")
def consent_purposes(_user=Depends(require_user)) -> dict:
    return care.CONSENT_PURPOSES
