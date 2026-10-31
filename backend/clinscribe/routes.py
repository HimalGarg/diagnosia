"""The API for ClinScribe: a clinician-side consultation note assistant.

Recording never starts on its own, audio is not kept, and a note stays a draft
until the clinician who held the consultation signs it.
"""
import json
import sqlite3
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from backend import settings_store, speech
from backend.auth import require_doctor, require_user
from backend.careflow import events
from backend.clinscribe import drafting, verifier
from backend.db import get_db
from backend.patient_pack import sync as pack
from backend.records import access as gate
from backend.records import audit, care, fhir
from backend.records.access import CLINICAL
from backend.records.store import dumps, insert, now_iso, one, rows, update
from backend.speech import Language

router = APIRouter(prefix="/api/addons/clinscribe")

MAX_CHUNK_BYTES = 5 * 1024 * 1024
Speaker = Literal["clinician", "patient", "other", "unknown"]


class SessionRequest(BaseModel):
    language: Language = "en-IN"


class StateRequest(BaseModel):
    state: Literal["recording", "paused", "stopped"]


class TypedSegment(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    speaker: Speaker = "unknown"


class SegmentCorrection(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    speaker: Optional[Speaker] = None


class DraftRequest(BaseModel):
    template: Literal["soap", "history_physical", "custom"] = "soap"
    custom_headings: List[str] = Field(default_factory=list, max_length=12)


class EditedStatement(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class EditedSection(BaseModel):
    heading: str = Field(min_length=1, max_length=120)
    statements: List[EditedStatement] = Field(max_length=100)


class NoteEdit(BaseModel):
    sections: List[EditedSection] = Field(max_length=20)


class Approval(BaseModel):
    acknowledge_flags: bool = False


def _session(db: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    session = db.execute("SELECT * FROM scribe_sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that session.")
    return session


def _note(db: sqlite3.Connection, note_id: str) -> sqlite3.Row:
    note = db.execute("SELECT * FROM clinical_notes WHERE id = ?", (note_id,)).fetchone()
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that note.")
    return note


def _latest(db: sqlite3.Connection, note_id: str) -> dict:
    version = one(
        db.execute(
            "SELECT * FROM clinical_note_versions WHERE note_id = ? ORDER BY version DESC LIMIT 1", (note_id,)
        ).fetchone()
    )
    version["content"] = json.loads(version["content"])
    return version


def _segments(db: sqlite3.Connection, session_id: str) -> List[dict]:
    return rows(
        db.execute("SELECT * FROM transcript_segments WHERE session_id = ? ORDER BY sequence", (session_id,))
    )


def _add_segment(db: sqlite3.Connection, session: sqlite3.Row, text: str, speaker: str, source: str) -> dict:
    sequence = db.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM transcript_segments WHERE session_id = ?", (session["id"],)
    ).fetchone()[0]
    segment_id = insert(
        db,
        "transcript_segments",
        session_id=session["id"],
        sequence=sequence,
        speaker=speaker,
        text=text.strip(),
        language=session["language"],
        source=source,
        recorded_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM transcript_segments WHERE id = ?", (segment_id,)).fetchone())


def _note_payload(db: sqlite3.Connection, note: sqlite3.Row) -> dict:
    latest = _latest(db, note["id"])
    return {
        **dict(note),
        "version": latest["version"],
        "author_type": latest["author_type"],
        "content": latest["content"],
        "open_flags": verifier.open_flags(latest["content"]),
    }


def _author_name(user: sqlite3.Row) -> str:
    return f"{user['first_name']} {user['last_name']}"


@router.post("/encounters/{encounter_id}/sessions", status_code=status.HTTP_201_CREATED)
def start_session(
    encounter_id: str,
    request: SessionRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    encounter = db.execute("SELECT * FROM encounters WHERE id = ?", (encounter_id,)).fetchone()
    if encounter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that encounter.")
    patient_id = encounter["patient_id"]
    gate.open_patient(db, user, patient_id, "clinscribe.session.start", CLINICAL)
    consent = care.require_consent(db, patient_id, "ai_scribe")
    session_id = insert(
        db,
        "scribe_sessions",
        patient_id=patient_id,
        encounter_id=encounter_id,
        clinician_id=user["id"],
        consent_id=consent["id"],
        language=request.language,
        started_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM scribe_sessions WHERE id = ?", (session_id,)).fetchone())


@router.get("/patients/{patient_id}/sessions")
def list_sessions(
    patient_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "clinscribe.session.list", CLINICAL)
    return rows(
        db.execute(
            """
            SELECT scribe_sessions.*,
                   (SELECT COUNT(*) FROM transcript_segments WHERE session_id = scribe_sessions.id) AS segments
            FROM scribe_sessions WHERE patient_id = ? ORDER BY started_at DESC
            """,
            (patient_id,),
        )
    )


@router.get("/sessions/{session_id}")
def get_session(session_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    session = _session(db, session_id)
    gate.open_patient(db, user, session["patient_id"], "clinscribe.session.read", CLINICAL)
    return {**dict(session), "segments": _segments(db, session_id)}


@router.post("/sessions/{session_id}/state")
def set_state(
    session_id: str,
    request: StateRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    session = _session(db, session_id)
    gate.open_patient(db, user, session["patient_id"], f"clinscribe.session.{request.state}", CLINICAL)
    if session["state"] == "stopped":
        raise HTTPException(status.HTTP_409_CONFLICT, "This session has ended. Start a new one to record more.")
    if request.state == "recording":
        care.require_consent(db, session["patient_id"], "ai_scribe")
    ended = now_iso() if request.state == "stopped" else None
    update(db, "scribe_sessions", session_id, state=request.state, ended_at=ended)
    return {"state": request.state}


@router.post("/sessions/{session_id}/transcribe", status_code=status.HTTP_201_CREATED)
def transcribe_chunk(
    session_id: str,
    audio: UploadFile = File(...),
    speaker: Speaker = Form("unknown"),
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """The browser sends short chunks, so text appears as the consultation goes. Audio is not stored."""
    session = _session(db, session_id)
    gate.open_patient(db, user, session["patient_id"], "clinscribe.transcribe", CLINICAL)
    if session["state"] != "recording":
        raise HTTPException(status.HTTP_409_CONFLICT, "Recording isn't on for this session.")
    care.require_consent(db, session["patient_id"], "ai_scribe")
    data = audio.file.read(MAX_CHUNK_BYTES + 1)
    if len(data) > MAX_CHUNK_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "This audio chunk is too large.")
    text = speech.transcribe(data, audio.content_type or "audio/webm", session["language"])
    if not text.strip():
        return {"segment": None}
    return {"segment": _add_segment(db, session, text, speaker, "speech")}


@router.post("/sessions/{session_id}/segments", status_code=status.HTTP_201_CREATED)
def add_typed_segment(
    session_id: str,
    request: TypedSegment,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Keeps the tool usable when the microphone or the speech service isn't."""
    session = _session(db, session_id)
    gate.open_patient(db, user, session["patient_id"], "clinscribe.segment.add", CLINICAL)
    return _add_segment(db, session, request.text, request.speaker, "typed")


@router.patch("/segments/{segment_id}")
def correct_segment(
    segment_id: str,
    request: SegmentCorrection,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    segment = db.execute("SELECT * FROM transcript_segments WHERE id = ?", (segment_id,)).fetchone()
    if segment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that line.")
    session = _session(db, segment["session_id"])
    gate.open_patient(db, user, session["patient_id"], "clinscribe.segment.correct", CLINICAL)
    update(
        db,
        "transcript_segments",
        segment_id,
        text=request.text or segment["text"],
        speaker=request.speaker or segment["speaker"],
        corrected=1,
    )
    return one(db.execute("SELECT * FROM transcript_segments WHERE id = ?", (segment_id,)).fetchone())


@router.post("/sessions/{session_id}/draft-note", status_code=status.HTTP_201_CREATED)
def draft_note(
    session_id: str,
    request: DraftRequest,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    session = _session(db, session_id)
    gate.open_patient(db, user, session["patient_id"], "clinscribe.note.draft", CLINICAL)
    care.require_consent(db, session["patient_id"], "ai_scribe")
    if session["state"] != "stopped":
        raise HTTPException(status.HTTP_409_CONFLICT, "Stop the recording before drafting the note.")
    segments = _segments(db, session_id)
    if not segments:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "There's no transcript to draft from.")
    headings = request.custom_headings if request.template == "custom" else drafting.TEMPLATES[request.template]
    headings = [heading.strip() for heading in headings if heading.strip()]
    if not headings:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Add at least one section heading.")

    model = settings_store.model_for("clinscribe_note")
    content = drafting.draft_note(segments, headings)
    note_id = insert(
        db,
        "clinical_notes",
        session_id=session_id,
        patient_id=session["patient_id"],
        encounter_id=session["encounter_id"],
        clinician_id=session["clinician_id"],
        template=request.template,
        model=model,
        prompt_version=drafting.PROMPT_VERSION,
        verifier_version=verifier.VERSION,
        created_at=now_iso(),
    )
    insert(
        db,
        "clinical_note_versions",
        note_id=note_id,
        version=1,
        author_type="ai",
        author_name=f"AI draft ({model})",
        content=dumps(content),
        created_at=now_iso(),
    )
    events.emit(
        db, "ClinScribe", "note_draft.created", events.for_patient(db, session["patient_id"], user), "review_required", user,
        key=note_id, metadata={"review_required": True}, used_ai=True,
    )
    return _note_payload(db, _note(db, note_id))


@router.get("/patients/{patient_id}/notes")
def list_notes(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    """Patients and caregivers see signed notes only, never a draft."""
    access = gate.open_patient(db, user, patient_id, "clinscribe.note.list")
    notes = db.execute(
        """
        SELECT * FROM clinical_notes WHERE patient_id = ? AND status != 'discarded'
          AND (status = 'approved' OR ? = 1) ORDER BY created_at DESC
        """,
        (patient_id, int(access.is_clinician)),
    ).fetchall()
    return [_note_payload(db, note) for note in notes]


@router.get("/notes/{note_id}")
def get_note(note_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    note = _note(db, note_id)
    access = gate.open_patient(db, user, note["patient_id"], "clinscribe.note.read")
    if note["status"] != "approved" and not access.is_clinician:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that note.")
    payload = _note_payload(db, note)
    if access.is_clinician:
        payload["versions"] = rows(
            db.execute(
                "SELECT version, author_type, author_name, created_at FROM clinical_note_versions WHERE note_id = ? ORDER BY version",
                (note_id,),
            )
        )
    return payload


@router.patch("/notes/{note_id}")
def edit_note(
    note_id: str,
    request: NoteEdit,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    note = _note(db, note_id)
    gate.open_patient(db, user, note["patient_id"], "clinscribe.note.edit", CLINICAL)
    if note["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "A signed note can't be changed.")
    latest = _latest(db, note_id)
    previous = {
        statement["text"]: statement
        for section in latest["content"]["sections"]
        for statement in section["statements"]
    }
    sections = []
    for section in request.sections:
        statements = []
        for statement in section.statements:
            # Text the clinician typed is theirs; anything left alone keeps its check.
            statements.append(
                previous.get(statement.text)
                or {
                    "text": statement.text,
                    "origin": "clinician_statement",
                    "evidence_segments": [],
                    "uncertain": False,
                    "uncertainty_reason": "",
                    "check": {"verdict": "clinician_entered", "score": 1.0, "reasons": [], "evidence": []},
                }
            )
        sections.append({"heading": section.heading, "statements": statements})
    content = {**latest["content"], "sections": sections}
    insert(
        db,
        "clinical_note_versions",
        note_id=note_id,
        version=latest["version"] + 1,
        author_type="clinician",
        author_id=user["id"],
        author_name=_author_name(user),
        content=dumps(content),
        created_at=now_iso(),
    )
    return _note_payload(db, note)


@router.post("/notes/{note_id}/approve")
def approve_note(
    note_id: str,
    request: Approval,
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    note = _note(db, note_id)
    gate.open_patient(db, user, note["patient_id"], "clinscribe.note.approve", CLINICAL)
    if note["clinician_id"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the clinician who held the consultation can sign this note.")
    if note["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This note is already signed.")
    latest = _latest(db, note_id)
    flags = verifier.open_flags(latest["content"])
    if flags and not request.acknowledge_flags:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "flags_open", "open_flags": flags, "message": f"Lines still to check: {flags}."},
        )
    signed_at = now_iso()
    update(db, "clinical_notes", note_id, status="approved", approved_at=signed_at)
    insert(
        db,
        "clinical_note_versions",
        note_id=note_id,
        version=latest["version"] + 1,
        author_type="clinician",
        author_id=user["id"],
        author_name=_author_name(user),
        content=dumps({**latest["content"], "signed_with_open_flags": flags}),
        created_at=signed_at,
    )
    audit.record(db, user, "clinscribe.note.signed", note["patient_id"], "clinical_note", note_id, reason=f"{flags} open flags")
    care.add_timeline(db, note["patient_id"], "clinscribe", "Consultation note signed", _author_name(user), "clinical_note", note_id)
    pack.enqueue(db, note["patient_id"], "clinical_note", note_id)

    retained = care.active_consent(db, note["patient_id"], "transcript_retention") is not None
    if not retained:
        _delete_transcript(db, note["session_id"])
    return {**_note_payload(db, _note(db, note_id)), "transcript_retained": retained}


def _delete_transcript(db: sqlite3.Connection, session_id: str) -> None:
    db.execute("DELETE FROM transcript_segments WHERE session_id = ?", (session_id,))
    update(db, "scribe_sessions", session_id, transcript_deleted_at=now_iso())


@router.delete("/sessions/{session_id}/transcript", status_code=status.HTTP_204_NO_CONTENT)
def delete_transcript(
    session_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> None:
    session = _session(db, session_id)
    gate.open_patient(db, user, session["patient_id"], "clinscribe.transcript.delete", CLINICAL)
    _delete_transcript(db, session_id)


@router.post("/notes/{note_id}/discard")
def discard_note(note_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    note = _note(db, note_id)
    gate.open_patient(db, user, note["patient_id"], "clinscribe.note.discard", CLINICAL)
    if note["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "A signed note can't be discarded.")
    update(db, "clinical_notes", note_id, status="discarded")
    return {"status": "discarded"}


@router.get("/notes/{note_id}/fhir")
def export_note(note_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    note = _note(db, note_id)
    gate.open_patient(db, user, note["patient_id"], "clinscribe.note.export")
    if note["status"] != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a signed note can be exported.")
    latest = _latest(db, note_id)
    return fhir.composition_resource(dict(note), latest["content"], latest["author_name"])
