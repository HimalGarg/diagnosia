"""The API for LabLens: lab reports become reviewed results and trends.

Results reach the patient's record only after a person confirms them, and the
original file and the laboratory's own wording are always kept.
"""
import json
import sqlite3
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from backend.auth import require_user
from backend.db import get_db
from backend.lablens import extract, parser
from backend.records import access as gate
from backend.records import audit, care, documents, fhir
from backend.records.access import WRITE
from backend.records.store import dumps, insert, now_iso, one, rows, update

router = APIRouter(prefix="/api/addons/lablens")

REVIEWED = ("accepted", "corrected")


class ResultReview(BaseModel):
    action: Literal["accept", "reject", "correct"]
    value_text: Optional[str] = Field(default=None, max_length=60)
    unit: Optional[str] = Field(default=None, max_length=40)
    reference_text: Optional[str] = Field(default=None, max_length=80)


class ManualResult(BaseModel):
    test_name: str = Field(min_length=1, max_length=120)
    value_text: str = Field(min_length=1, max_length=60)
    unit: str = Field(default="", max_length=40)
    reference_text: str = Field(default="", max_length=80)


class Confirmation(BaseModel):
    collected_at: Optional[str] = Field(default=None, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    acknowledge_identity: bool = False


def _report(db: sqlite3.Connection, report_id: str) -> sqlite3.Row:
    report = db.execute("SELECT * FROM lab_reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that report.")
    return report


def _results(db: sqlite3.Connection, report_id: str) -> List[dict]:
    found = rows(db.execute("SELECT * FROM lab_results WHERE report_id = ? ORDER BY position", (report_id,)))
    for result in found:
        result["issues"] = json.loads(result["issues"])
        result["range_status"] = parser.range_status(
            result["value_number"], result["reference_low"], result["reference_high"]
        )
    return found


def _payload(db: sqlite3.Connection, report: sqlite3.Row) -> dict:
    results = _results(db, report["id"])
    return {
        **dict(report),
        "results": results,
        "to_review": sum(1 for row in results if row["review_status"] == "pending" and row["confidence"] == "review"),
    }


@router.post("/reports", status_code=status.HTTP_202_ACCEPTED)
def upload_report(
    background: BackgroundTasks,
    patient_id: str = Form(...),
    encounter_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    access = gate.open_patient(db, user, patient_id, "lablens.report.upload", WRITE)
    care.require_belongs(db, "encounters", encounter_id, patient_id)
    document, is_new = documents.store(
        db,
        patient_id,
        file.file.read(documents.MAX_DOCUMENT_BYTES + 1),
        file.filename,
        "laboratory_report",
        "clinician_entered" if access.is_clinician else "patient_entered",
        user["id"],
        encounter_id,
    )
    if not is_new:
        existing = db.execute("SELECT * FROM lab_reports WHERE document_id = ?", (document["id"],)).fetchone()
        if existing is not None:
            return {**_payload(db, existing), "duplicate": True}
    report_id = insert(
        db,
        "lab_reports",
        patient_id=patient_id,
        document_id=document["id"],
        encounter_id=encounter_id,
        uploaded_by=user["id"],
        created_at=now_iso(),
    )
    background.add_task(extract.process, report_id)
    return {**_payload(db, _report(db, report_id)), "duplicate": False}


@router.get("/patients/{patient_id}/reports")
def list_reports(
    patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)
) -> List[dict]:
    gate.open_patient(db, user, patient_id, "lablens.report.list")
    return rows(
        db.execute(
            """
            SELECT lab_reports.*, clinical_documents.filename,
                   (SELECT COUNT(*) FROM lab_results WHERE report_id = lab_reports.id) AS result_count
            FROM lab_reports JOIN clinical_documents ON clinical_documents.id = lab_reports.document_id
            WHERE lab_reports.patient_id = ? ORDER BY lab_reports.created_at DESC
            """,
            (patient_id,),
        )
    )


@router.get("/reports/{report_id}/extraction")
def get_extraction(report_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    report = _report(db, report_id)
    gate.open_patient(db, user, report["patient_id"], "lablens.report.read")
    return _payload(db, report)


@router.post("/reports/{report_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry(
    report_id: str,
    background: BackgroundTasks,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    report = _report(db, report_id)
    gate.open_patient(db, user, report["patient_id"], "lablens.report.retry", WRITE)
    if report["status"] not in ("failed", "needs_manual_entry"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This report doesn't need a retry.")
    update(db, "lab_reports", report_id, status="processing", failure_reason="")
    background.add_task(extract.process, report_id)
    return {"status": "processing"}


def _editable(db: sqlite3.Connection, report_id: str, user, action: str) -> sqlite3.Row:
    report = _report(db, report_id)
    gate.open_patient(db, user, report["patient_id"], action, WRITE)
    if report["status"] == "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This report is confirmed and can't be changed.")
    return report


@router.patch("/results/{result_id}")
def review_result(
    result_id: str,
    request: ResultReview,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    result = db.execute("SELECT * FROM lab_results WHERE id = ?", (result_id,)).fetchone()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that result.")
    _editable(db, result["report_id"], user, "lablens.result.review")
    timestamp = now_iso()
    if request.action != "correct":
        status_value = "accepted" if request.action == "accept" else "rejected"
        update(db, "lab_results", result_id, review_status=status_value, reviewed_by=user["id"], reviewed_at=timestamp)
    else:
        value_text = request.value_text if request.value_text is not None else result["value_text"]
        reference_text = request.reference_text if request.reference_text is not None else result["reference_text"]
        number, _ = parser.parse_number(value_text)
        update(
            db,
            "lab_results",
            result_id,
            value_text=value_text,
            value_number=number,
            unit=request.unit if request.unit is not None else result["unit"],
            **parser.parse_reference(reference_text),
            review_status="corrected",
            reviewed_by=user["id"],
            reviewed_at=timestamp,
            original_value=result["original_value"]
            or dumps({key: result[key] for key in ("value_text", "unit", "reference_text")}),
        )
    return one(db.execute("SELECT * FROM lab_results WHERE id = ?", (result_id,)).fetchone())


@router.post("/reports/{report_id}/results", status_code=status.HTTP_201_CREATED)
def add_manual_result(
    report_id: str,
    request: ManualResult,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """The manual path, for reports nothing could read."""
    report = _editable(db, report_id, user, "lablens.result.add")
    analyte = parser.analyte_named(request.test_name)
    result = parser.build_result(
        request.test_name,
        "Entered by hand",
        {
            "value_text": request.value_text.strip(),
            "reported_flag": "",
            "unit": request.unit.strip(),
            **parser.parse_reference(request.reference_text),
        },
        analyte,
    )
    position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM lab_results WHERE report_id = ?", (report_id,)).fetchone()[0]
    extract.save_results(
        db, report_id, [result], position, review_status="accepted", reviewed_by=user["id"], reviewed_at=now_iso()
    )
    if report["status"] in ("needs_manual_entry", "failed"):
        update(db, "lab_reports", report_id, status="needs_review", extraction_method="manual", failure_reason="")
    return _payload(db, _report(db, report_id))


@router.post("/reports/{report_id}/confirm")
def confirm_report(
    report_id: str,
    request: Confirmation,
    user=Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    report = _editable(db, report_id, user, "lablens.report.confirm")
    if report["status"] != "needs_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "This report isn't ready to confirm.")
    if report["identity_check"] == "mismatch" and not request.acknowledge_identity:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "identity_mismatch",
                "message": f"The name on this report is {report['name_on_report']}. Confirm it belongs to this patient.",
            },
        )
    collected = request.collected_at or report["collected_at"]
    if not collected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "date_needed", "message": "Enter the date the sample was collected, so trends stay correct."},
        )
    results = _results(db, report_id)
    waiting = [row for row in results if row["review_status"] == "pending" and row["confidence"] == "review"]
    if waiting:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "results_pending", "message": f"Highlighted results to check first: {len(waiting)}."},
        )

    timestamp = now_iso()
    saved = 0
    source_type = "ai_extracted" if report["extraction_method"] == "ai-vision" else "imported_document"
    for result in results:
        if result["review_status"] == "rejected":
            continue
        if result["review_status"] == "pending":
            update(db, "lab_results", result["id"], review_status="accepted", reviewed_by=user["id"], reviewed_at=timestamp)
            result["review_status"] = "accepted"
        flag = parser.range_status(result["value_number"], result["reference_low"], result["reference_high"])
        insert(
            db,
            "observations",
            patient_id=report["patient_id"],
            encounter_id=report["encounter_id"],
            category="laboratory",
            code_system="http://loinc.org" if result["loinc_code"] else None,
            code=result["loinc_code"],
            label=result["original_name"],
            value_number=result["value_number"],
            value_text=result["value_text"],
            unit=result["unit"],
            reference_text=result["reference_text"],
            reference_low=result["reference_low"],
            reference_high=result["reference_high"],
            flag=flag,
            effective_at=collected,
            performer=report["laboratory"],
            source_type=source_type,
            source_reference=report["document_id"],
            extraction_method=f"{report['extraction_method']}/{report['parser_version']}",
            review_status=result["review_status"],
            reviewed_by=user["id"],
            reviewed_at=timestamp,
            original_value=result["original_value"],
            created_at=timestamp,
        )
        saved += 1
    update(
        db,
        "lab_reports",
        report_id,
        status="confirmed",
        collected_at=collected,
        confirmed_by=user["id"],
        confirmed_at=timestamp,
        identity_acknowledged_by=user["id"] if request.acknowledge_identity else None,
    )
    audit.record(db, user, "lablens.report.confirmed", report["patient_id"], "lab_report", report_id)
    care.add_timeline(
        db, report["patient_id"], "lablens", "Lab report confirmed", f"{saved} results", "lab_report", report_id, collected
    )
    return _payload(db, _report(db, report_id))


def build_trends(observations: List[dict]) -> dict:
    """A line is one test in one unit. Different units are never joined."""
    series: Dict[tuple, dict] = {}
    for row in observations:
        if row["value_number"] is None:
            continue
        key = (row["code"] or row["label"].lower(), parser.normal_unit(row["unit"]))
        line = series.setdefault(
            key,
            {"code": row["code"], "label": row["label"], "unit": row["unit"], "points": []},
        )
        line["points"].append(
            {
                "date": row["effective_at"],
                "value": row["value_number"],
                "reference_text": row["reference_text"],
                "reference_low": row["reference_low"],
                "reference_high": row["reference_high"],
                "laboratory": row["performer"],
                "range_status": parser.range_status(row["value_number"], row["reference_low"], row["reference_high"]),
            }
        )
    units_by_test: Dict[str, List[str]] = {}
    for (test, unit) in series:
        units_by_test.setdefault(test, []).append(unit)
    notes = [
        f"{series[(test, units[0])]['label']} was reported in different units ({', '.join(sorted(units))}), so these are shown separately."
        for test, units in units_by_test.items()
        if len(units) > 1
    ]
    lines = sorted(series.values(), key=lambda line: line["label"].lower())
    return {"series": lines, "notes": notes}


@router.get("/patients/{patient_id}/trends")
def lab_trends(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    gate.open_patient(db, user, patient_id, "lablens.trends.read")
    return build_trends(
        rows(
            db.execute(
                """
                SELECT * FROM observations
                WHERE patient_id = ? AND category = 'laboratory' AND review_status IN ('accepted', 'corrected')
                ORDER BY effective_at
                """,
                (patient_id,),
            )
        )
    )


@router.get("/reports/{report_id}/fhir")
def export_report(report_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    report = _report(db, report_id)
    gate.open_patient(db, user, report["patient_id"], "lablens.report.export")
    if report["status"] != "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a confirmed report can be exported.")
    observations = rows(
        db.execute(
            "SELECT * FROM observations WHERE source_reference = ? AND category = 'laboratory'", (report["document_id"],)
        )
    )
    approved_codes = fhir.approved_loinc_codes(db)
    resources = [fhir.observation_resource(row, approved_codes) for row in observations]
    diagnostic = {
        "resourceType": "DiagnosticReport",
        "id": report["id"],
        "status": "final",
        "code": {"text": "Laboratory report"},
        "subject": fhir.subject(report["patient_id"]),
        "effectiveDateTime": report["collected_at"],
        "performer": [{"display": report["laboratory"]}] if report["laboratory"] else [],
        "result": [{"reference": f"Observation/{item['id']}"} for item in resources],
    }
    return fhir.bundle([diagnostic, *resources])
