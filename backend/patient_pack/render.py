"""Turns a finished record into the text a patient's datapack holds.

Only what a person finished goes in: signed notes, confirmed results, approved plans. Names, dates of
birth, phone numbers and ABHA numbers stay out, and every section repeats the item's tag, because
Meko splits long files and a search hit carries only one piece of one.
"""
import json
import re
import sqlite3
from typing import Callable, Dict, List, Optional

TAG_EVERY = 500  # characters, so a piece of a long file can still be traced to its record


def tag(item_id: str) -> str:
    return f"[pack-item:{item_id}]"


TAG = re.compile(r"\[pack-item:([0-9a-f-]{36})\]")


def tags_in(text: str) -> List[str]:
    return TAG.findall(text)


def without_tags(text: str) -> str:
    return TAG.sub("", text)


def _tagged(item_id: str, body: str) -> str:
    pieces, length = [tag(item_id)], 0
    for line in body.splitlines():
        if length > TAG_EVERY:
            pieces.append(tag(item_id))
            length = 0
        pieces.append(line)
        length += len(line)
    return "\n".join(pieces)


def _document(item_id: str, title: str, sections: List[tuple]) -> str:
    parts = [f"# {title}"]
    for heading, body in sections:
        if body.strip():
            parts.append(f"## {heading}\n\n{_tagged(item_id, body.strip())}")
    return "\n\n".join(parts) + "\n"


def _note(db: sqlite3.Connection, item_id: str, note_id: str) -> Optional[str]:
    note = db.execute("SELECT status, approved_at FROM clinical_notes WHERE id = ?", (note_id,)).fetchone()
    if note is None or note["status"] != "approved":
        return None
    latest = db.execute(
        "SELECT content, author_name FROM clinical_note_versions WHERE note_id = ? ORDER BY version DESC LIMIT 1", (note_id,)
    ).fetchone()
    content = json.loads(latest["content"])
    sections = [
        (section["heading"], "\n".join(statement["text"] for statement in section.get("statements", [])))
        for section in content.get("sections", [])
    ]
    return _document(item_id, f"Signed consultation note, {note['approved_at'][:10]}", sections)


def _lines(values: List[str]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _intake(db: sqlite3.Connection, item_id: str, intake_id: str) -> Optional[str]:
    intake = db.execute("SELECT report, records, created_at, status FROM intakes WHERE id = ?", (intake_id,)).fetchone()
    if intake is None or intake["status"] != "done" or not intake["report"]:
        return None
    report = json.loads(intake["report"])
    return _document(
        item_id,
        f"DiagnoFast report, {intake['created_at'][:10]}. AI-written from the patient's answers, not yet reviewed by a clinician",
        [
            ("Chief complaint", report.get("chief_complaint", "")),
            ("Present illness", report.get("present_illness", "")),
            ("Highlights", _lines(report.get("highlights", []))),
            ("Medicines the patient reported", _lines(report.get("current_medicines", []))),
            ("Allergies the patient reported", _lines(report.get("allergies", []))),
            ("Past history", report.get("past_history", "")),
            ("Gaps in the interview", _lines(report.get("gaps", []))),
        ],
    )


def _lab(db: sqlite3.Connection, item_id: str, report_id: str) -> Optional[str]:
    report = db.execute("SELECT status, collected_at, laboratory, document_id FROM lab_reports WHERE id = ?", (report_id,)).fetchone()
    if report is None or report["status"] != "confirmed":
        return None
    found = db.execute(
        """
        SELECT label, value_number, value_text, unit, reference_text, flag FROM observations
        WHERE source_reference = ? AND review_status IN ('accepted', 'corrected') ORDER BY label
        """,
        (report["document_id"],),
    ).fetchall()
    results = []
    for row in found:
        value = row["value_text"] if row["value_number"] is None else row["value_number"]
        reference = f" (reference {row['reference_text']})" if row["reference_text"] else ""
        flag = f" [{row['flag']}]" if row["flag"] else ""
        results.append(f"{row['label']}: {value} {row['unit']}{reference}{flag}".strip())
    return _document(item_id, f"Lab results collected {report['collected_at']}, reviewed by a person", [("Results", _lines(results))])


def _outcome(db: sqlite3.Connection, item_id: str, outcome_id: str) -> Optional[str]:
    outcome = db.execute(
        """
        SELECT referral_outcomes.summary, referral_outcomes.recommendations, referral_outcomes.created_at, referrals.specialty
        FROM referral_outcomes JOIN referrals ON referrals.id = referral_outcomes.referral_id
        WHERE referral_outcomes.id = ?
        """,
        (outcome_id,),
    ).fetchone()
    if outcome is None:
        return None
    return _document(
        item_id,
        f"{outcome['specialty']} referral outcome, {outcome['created_at'][:10]}",
        [("Summary", outcome["summary"]), ("Recommendations", outcome["recommendations"])],
    )


def _discharge(db: sqlite3.Connection, item_id: str, episode_id: str) -> Optional[str]:
    episode = db.execute(
        "SELECT status, discharge_date, destination, problems, procedures FROM discharge_episodes WHERE id = ?", (episode_id,)
    ).fetchone()
    if episode is None or episode["status"] == "draft":
        return None
    instructions = db.execute(
        "SELECT topic, text FROM discharge_instructions WHERE episode_id = ? ORDER BY created_at", (episode_id,)
    ).fetchall()
    return _document(
        item_id,
        f"Approved discharge plan, {episode['discharge_date']}",
        [
            ("Destination", episode["destination"]),
            ("Problems", episode["problems"]),
            ("Procedures", episode["procedures"]),
            ("Instructions", _lines([f"{row['topic']}: {row['text']}" for row in instructions])),
        ],
    )


def _file(db: sqlite3.Connection, item_id: str, document_id: str) -> Optional[str]:
    """A description only. The file stays in the encrypted vault, and Meko can't search images anyway."""
    document = db.execute(
        "SELECT document_type, description, source_type, created_at, retention_status FROM clinical_documents WHERE id = ?", (document_id,)
    ).fetchone()
    if document is None or document["retention_status"] != "retained":
        return None
    kind = document["document_type"].replace("_", " ")
    return _document(
        item_id,
        f"{kind.capitalize()} added {document['created_at'][:10]}. The file itself is in the patient's chart",
        [("Description", document["description"] or f"A {kind} with no description written.")],
    )


def medicine_line(medicine: dict) -> str:
    """Exactly what the patient confirmed. Each field is a value read from the photo."""
    details = ", ".join(medicine[field]["value"] for field in ("dose", "frequency", "duration") if medicine[field]["value"])
    name = medicine["name"]["value"]
    return f"{name}: {details}" if details else name


def _prescription(db: sqlite3.Connection, item_id: str, saved_id: str) -> Optional[str]:
    saved = db.execute("SELECT medicines, created_at FROM saved_prescriptions WHERE id = ?", (saved_id,)).fetchone()
    if saved is None:
        return None
    medicines = [medicine_line(medicine) for medicine in json.loads(saved["medicines"])]
    return _document(
        item_id,
        f"Prescription saved {saved['created_at'][:10]}. Patient-confirmed, not checked by a clinician",
        [("Medicines", _lines(medicines))],
    )


RENDERERS: Dict[str, Callable[[sqlite3.Connection, str, str], Optional[str]]] = {
    "clinical_note": _note,
    "intake": _intake,
    "lab_report": _lab,
    "referral_outcome": _outcome,
    "discharge": _discharge,
    "document": _file,
    "prescription": _prescription,
}


def render(db: sqlite3.Connection, item_id: str, source_type: str, source_id: str) -> Optional[str]:
    """None means the record isn't finished or has gone, so nothing may be sent."""
    return RENDERERS[source_type](db, item_id, source_id)
