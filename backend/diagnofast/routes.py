"""The API for DiagnoFast.

Reception creates a single-use link for each patient. The patient needs no
account, and the link stops working when the interview ends. Everything the
patient says is visible to the doctor who created the link and, once the link
belongs to a patient record, to that patient's care team.
"""
import base64
import hashlib
import json
import logging
import secrets
import sqlite3
from dataclasses import asdict
from typing import List, Literal, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from backend import llm, speech
from backend.auth import require_doctor
from backend.careflow import events
from backend.db import connect, get_db
from backend.diagnofast import abha, agent, recordings
from backend.patient_pack import sync as pack
from backend.records.access import READ, WRITE, open_patient

MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_TYPED_ANSWER = 2000
OPEN_STATUSES = ("waiting", "in_progress")

MODULE = "DiagnoFast"

logger = logging.getLogger("diagnosia.diagnofast")
router = APIRouter(prefix="/api/diagnofast")


class NewIntake(BaseModel):
    form_id: str
    patient_name: str = Field(min_length=1, max_length=120)
    patient_age: Optional[int] = Field(default=None, ge=0, le=130)
    patient_sex: Optional[Literal["female", "male", "other"]] = None
    abha_id: Optional[str] = Field(default=None, max_length=32)
    patient_id: Optional[str] = Field(default=None, max_length=64)  # when the patient has a record here


class StartRequest(BaseModel):
    language: speech.Language
    consent: bool
    abha_consent: bool = False


def _workflow(db: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    """A link for a patient joins that patient's workflow. A loose link is its own."""
    return events.for_patient(db, row["patient_id"]) if row["patient_id"] else events.for_intake(db, row["id"], row["doctor_id"])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _spoken(text: str, language: speech.Language) -> Optional[str]:
    """The question as audio. Without it, the page reads the words in the browser's voice."""
    if language not in speech.VOICED_LANGUAGES:
        return None
    try:
        return "data:audio/wav;base64," + base64.b64encode(speech.speak(text, language)).decode()
    except (speech.SpeechUnavailable, speech.SpeechRejected) as error:
        logger.warning("Couldn't speak a question: %s", error)
        return None


def _open_intake(token: str, db: sqlite3.Connection) -> sqlite3.Row:
    row = db.execute("SELECT * FROM intakes WHERE token_hash = ?", (_hash(token),)).fetchone()
    if row is None or row["status"] not in OPEN_STATUSES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link isn't active. Please ask reception.")
    return row


def _readable_intake(intake_id: int, doctor: sqlite3.Row, db: sqlite3.Connection, action: str) -> sqlite3.Row:
    """The doctor who sent the link, or the care team of the patient it belongs to."""
    row = db.execute("SELECT * FROM intakes WHERE id = ?", (intake_id,)).fetchone()
    if row is None or (row["doctor_id"] != doctor["id"] and row["patient_id"] is None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found.")
    if row["patient_id"]:
        open_patient(db, doctor, row["patient_id"], action, READ)
    return row


@router.get("/forms")
def forms(_doctor=Depends(require_doctor)) -> List[dict]:
    return agent.list_forms()


@router.post("/intakes", status_code=status.HTTP_201_CREATED)
def create_intake(
    details: NewIntake,
    doctor: sqlite3.Row = Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    if agent.load_form(details.form_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown form.")
    if details.patient_id:
        open_patient(db, doctor, details.patient_id, "diagnofast.link.created", WRITE)
    token = secrets.token_urlsafe(24)
    cursor = db.execute(
        """
        INSERT INTO intakes
            (token_hash, doctor_id, form_id, patient_name, patient_age, patient_sex, abha_id, patient_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _hash(token),
            doctor["id"],
            details.form_id,
            details.patient_name.strip(),
            details.patient_age,
            details.patient_sex,
            details.abha_id,
            details.patient_id,
        ),
    )
    if not details.patient_id:  # with a patient, the audit entry above has already recorded this
        events.emit(db, MODULE, "intake.link_created", events.for_intake(db, cursor.lastrowid, doctor["id"]), user=doctor)
    return {"id": cursor.lastrowid, "link": f"intake.html?token={token}"}


@router.get("/intakes")
def queue(
    patient_id: Optional[str] = Query(default=None, max_length=64),
    doctor: sqlite3.Row = Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> List[dict]:
    """The doctor's own queue, or everything for one patient the doctor cares for."""
    if patient_id:
        open_patient(db, doctor, patient_id, "diagnofast.intakes.listed", READ)
        where, value = "patient_id = ?", patient_id
    else:
        where, value = "doctor_id = ?", doctor["id"]
    rows = db.execute(
        f"""
        SELECT id, patient_name, patient_id, status, urgent, created_at FROM intakes
        WHERE {where} ORDER BY urgent = '[]', id DESC
        """,
        (value,),
    ).fetchall()
    return [{**dict(row), "urgent": json.loads(row["urgent"])} for row in rows]


@router.get("/intakes/{intake_id}")
def intake_detail(
    intake_id: int,
    doctor: sqlite3.Row = Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = _readable_intake(intake_id, doctor, db, "diagnofast.report.viewed")
    form = agent.load_form(row["form_id"])
    answers = json.loads(row["answers"])
    transcript = json.loads(row["transcript"])
    return {
        "id": row["id"],
        "status": row["status"],
        "patient": {
            "name": row["patient_name"],
            "age": row["patient_age"],
            "sex": row["patient_sex"],
            "abha_id": row["abha_id"],
            "record_id": row["patient_id"],
        },
        "language": row["language"],
        "form_title": form["title"],
        "answers": [
            {"label": item["label"], "value": answers.get(item["key"], "")}
            for item in form["fields"]
        ],
        "urgent": json.loads(row["urgent"]),
        "records": json.loads(row["records"]) if row["records"] else None,
        "report": json.loads(row["report"]) if row["report"] else None,
        "transcript": [
            {"question": turn["question"], "answer": turn["answer"], "has_audio": bool(turn.get("audio"))}
            for turn in transcript
        ],
    }


@router.get("/intakes/{intake_id}/audio/{turn}")
def intake_audio(
    intake_id: int,
    turn: int,
    doctor: sqlite3.Row = Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    row = _readable_intake(intake_id, doctor, db, "diagnofast.recording.played")
    transcript = json.loads(row["transcript"])
    if not 0 <= turn < len(transcript) or not transcript[turn].get("audio"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No recording for this answer.")
    data, content_type = recordings.load(intake_id, transcript[turn]["audio"])
    return Response(data, media_type=content_type)


@router.get("/patient/{token}")
def patient_page(token: str, db: sqlite3.Connection = Depends(get_db)) -> dict:
    row = _open_intake(token, db)
    return {"patient_name": row["patient_name"], "has_abha_id": bool(row["abha_id"])}


@router.post("/patient/{token}/start")
def start_interview(
    token: str, request: StartRequest, db: sqlite3.Connection = Depends(get_db)
) -> dict:
    row = _open_intake(token, db)
    if not request.consent:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Consent is needed to continue.")
    if row["status"] != "waiting":
        raise HTTPException(status.HTTP_409_CONFLICT, "This interview has already started.")

    records = None
    if request.abha_consent and row["abha_id"]:
        records = asdict(abha.get_abha_client().fetch(row["abha_id"]))
    form = agent.load_form(row["form_id"])
    question = agent.first_question(form, request.language)
    db.execute(
        """
        UPDATE intakes SET status = 'in_progress', language = ?, records = ?, current_question = ?,
            questions_asked = 1 WHERE id = ?
        """,
        (request.language, json.dumps(records) if records else None, question, row["id"]),
    )
    events.emit(db, MODULE, "intake.started", _workflow(db, row), "started", actor="patient", key=str(row["id"]), metadata={"language": request.language})
    return {"question": question, "audio": _spoken(question, request.language)}


def _build_report(intake_id: int) -> None:
    """Runs after the patient has their thank-you, so they aren't kept waiting."""
    db = connect()
    try:
        row = db.execute("SELECT * FROM intakes WHERE id = ?", (intake_id,)).fetchone()
        try:
            report = agent.write_report(
                agent.load_form(row["form_id"]),
                json.loads(row["answers"]),
                [
                    {"question": turn["question"], "answer": turn["answer"]}
                    for turn in json.loads(row["transcript"])
                ],
                json.loads(row["records"]) if row["records"] else None,
            )
            db.execute(
                "UPDATE intakes SET report = ?, status = 'done' WHERE id = ?",
                (report.model_dump_json(), intake_id),
            )
            if row["patient_id"]:  # an intake with no record has no patient to attach it to
                pack.enqueue(db, row["patient_id"], "intake", intake_id)
            events.emit(db, MODULE, "intake.review_required", _workflow(db, row), "review_required", key=str(intake_id), metadata={"review_required": True}, used_ai=True)
        except (llm.AIUnavailable, llm.AIDeclined) as error:
            # The doctor can still read the answers and transcript.
            logger.warning("Report failed for intake %s: %s", intake_id, error)
            db.execute("UPDATE intakes SET status = 'report_failed' WHERE id = ?", (intake_id,))
            events.emit(db, MODULE, "intake.failed", _workflow(db, row), "failed", key=str(intake_id), metadata={"error_category": events.category_of(error)})
        db.commit()
    finally:
        db.close()


@router.post("/patient/{token}/answer")
def answer(
    token: str,
    background: BackgroundTasks,
    audio: Optional[UploadFile] = File(default=None),
    text: Optional[str] = Form(default=None, max_length=MAX_TYPED_ANSWER),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = _open_intake(token, db)
    if row["status"] != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "Please start the interview first.")
    language = row["language"]
    transcript = json.loads(row["transcript"])

    audio_name = None
    if audio is not None:
        content_type = (audio.content_type or "").split(";")[0].strip()
        if content_type not in recordings.EXTENSIONS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This audio format isn't supported.")
        data = audio.file.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "This recording is too long.")
        said = speech.transcribe(data, content_type, language).strip()
        if not said:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "We couldn't hear that. Please try again, or type your answer.")
        audio_name = recordings.save(row["id"], len(transcript), data, content_type)
    elif text and text.strip():
        said = text.strip()
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Please answer the question.")

    transcript.append({"question": row["current_question"], "answer": said, "audio": audio_name})
    form = agent.load_form(row["form_id"])
    answers = json.loads(row["answers"])
    conversation = [{"question": turn["question"], "answer": turn["answer"]} for turn in transcript]
    try:
        turn = agent.next_turn(form, answers, conversation, language)
    except (llm.AIUnavailable, llm.AIDeclined) as error:
        events.emit_separately(MODULE, "intake.failed", lambda connection: _workflow(connection, row), "failed", metadata={"error_category": events.category_of(error)})
        raise
    step = agent.apply_turn(form, answers, turn, row["questions_asked"], language)

    finished = step.question is None
    flow = _workflow(db, row)
    if audio_name:
        events.emit(db, MODULE, "intake.transcription.completed", flow, actor="patient", metadata={"language": language})
    db.execute(
        """
        UPDATE intakes SET answers = ?, transcript = ?, urgent = ?, current_question = ?,
            questions_asked = ?, status = ? WHERE id = ?
        """,
        (
            json.dumps(step.answers, ensure_ascii=False),
            json.dumps(transcript, ensure_ascii=False),
            json.dumps(step.urgent),
            step.question,
            row["questions_asked"] + (0 if finished else 1),
            "preparing_report" if finished else "in_progress",
            row["id"],
        ),
    )
    if finished:
        events.emit(db, MODULE, "intake.completed", flow, actor="patient", key=str(row["id"]), metadata={"question_count": row["questions_asked"], "urgent_stop": bool(step.urgent)}, used_ai=True)
        # Commit first: the report task reads this row on its own connection.
        db.commit()
        background.add_task(_build_report, row["id"])
        message = agent.URGENT_MESSAGE[language] if step.urgent else agent.THANK_YOU[language]
        return {"finished": True, "urgent": bool(step.urgent), "message": message, "audio": _spoken(message, language)}
    return {"finished": False, "question": step.question, "audio": _spoken(step.question, language)}
