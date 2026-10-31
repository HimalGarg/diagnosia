"""One repeatable synthetic journey for CareFlow, through the real routes.

Every event comes from a tool doing its real work, so nothing here writes to the event store. By
default AI is replaced with fixed answers, labelled `demo_stub`, so the demo is the same each time
and costs nothing. With `--real-ai` it uses the real providers, with synthetic data only.

    python -m backend.operator_cli careflow-demo [--real-ai]
"""
import io
import sqlite3
from typing import Dict, Optional

from fastapi.testclient import TestClient

from backend import demo, llm
from backend.careflow import delivery, events
from backend.db import connect
from backend.diagnofast import agent
from backend.integrations import execution
from backend.lablens.extract import ReadReport, ReadResult
from backend.records import staff

CASE = "case_demo_001"
DOCTOR = "careflow.demo.doctor@example.com"
SPECIALIST = "careflow.demo.specialist@example.com"
PASSWORD = "Synthetic-demo-only-1"
SYNTHETIC_NAME = {"first_name": "Demo", "last_name": "Person"}
# Answers everything on the form at once, so a real model can finish the interview quickly.
INTERVIEW_ANSWER = "Synthetic answer: mild cough for two days, slowly improving, no fever, no other symptoms, no long-term illness, no medicines, no allergies."


class StubAI:
    """Fixed answers. It records a run like the real thing, so the timeline shows the stub by name."""

    def __call__(self, system, content, schema, tool, **_options):
        run = execution.AgentRun(tool)
        run.tried("demo_stub", "demo-fixed-answers", "succeeded")
        run.finish("succeeded")
        if schema is agent.Turn:
            form = agent.load_form("general_physician")
            updates = [agent.FieldUpdate(key=field["key"], value="Synthetic answer") for field in form["fields"] if field.get("required")]
            return agent.Turn(updates=updates, next_question="", finished=True, urgent_symptoms=[])
        if schema is agent.Report:
            return agent.Report(chief_complaint="Synthetic", present_illness="Synthetic demo history.", highlights=[], current_medicines=[], allergies=[], past_history="", gaps=[])
        if schema is ReadReport:
            result = ReadResult(test_name="Haemoglobin", value="12.9", unit="g/dL", reference_range="13.0 - 17.0", printed_flag="L", legibility="clear")
            return ReadReport(is_lab_report=True, name_on_report="Mr. Demo Person", laboratory="Demo Diagnostics Laboratory", collection_date="25/10/2026", results=[result])
        return schema.model_validate({"sections": [{"heading": "Plan", "statements": [{"text": "Synthetic follow-up.", "origin": "clinician_statement", "evidence_segments": [1], "uncertain": False, "uncertainty_reason": ""}]}], "missing_information": []})


def _person(email: str, first: str, organization: str) -> tuple:
    client = TestClient(demo_app())
    registered = client.post("/api/auth/register", json={"first_name": first, "last_name": "Demo", "email": email, "password": PASSWORD, "role": "doctor"})
    if registered.status_code != 201:
        demo._check(client.post("/api/auth/login", json={"email": email, "password": PASSWORD}))
        db = connect()
        organization_id = db.execute("SELECT id FROM organizations WHERE name = ?", (organization,)).fetchone()["id"]
        db.close()
        return client, organization_id
    db = connect()
    organization_id = staff.operator_verify_organization(db, email, organization, "clinic", "CareFlow demo", "SYNTHETIC", "careflow-demo")
    db.commit()
    db.close()
    return client, organization_id


def demo_app():
    from backend.app import app

    return app


def _existing(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM careflow_workflows WHERE workflow_ref = ?", (CASE,)).fetchone() is not None


def _name_the_case(db: sqlite3.Connection, patient_id: str) -> None:
    """A fixed, readable reference for the demo. Real workflows get a random one."""
    workflow = events.for_patient(db, patient_id)
    db.execute("PRAGMA defer_foreign_keys = ON")
    db.execute("UPDATE careflow_outbox SET workflow_ref = ? WHERE workflow_ref = ?", (CASE, workflow["workflow_ref"]))
    db.execute("UPDATE careflow_workflows SET workflow_ref = ? WHERE workflow_ref = ?", (CASE, workflow["workflow_ref"]))


def _scanned_report(doctor, patient_id: str) -> Optional[dict]:
    """A report with no text layer, so it takes the AI reading path instead of the local parser."""
    import pypdfium2

    pdf = pypdfium2.PdfDocument(demo._lab_pdf("25/10/2026", "12.9 L", "6.5", "Mr. Demo Person"))
    image = pdf[0].render(scale=2).to_pil()
    pdf.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    report = demo._check(doctor.post("/api/addons/lablens/reports", data={"patient_id": patient_id}, files={"file": ("demo-scan.png", buffer.getvalue(), "image/png")}))
    return demo._check(doctor.get(f"/api/addons/lablens/reports/{report['id']}/extraction"))


def run(real_ai: bool = False) -> Dict[str, object]:
    """Repeat runs change nothing and report what's there."""
    db = connect()
    already = _existing(db)
    db.close()
    if already:
        return {"workflow_ref": CASE, "created": False}

    real_ask = llm.ask
    if not real_ai:
        llm.ask = StubAI()
    try:
        doctor, organization_id = _person(DOCTOR, "Demo Doctor", "CareFlow Demo Clinic")
        specialist, specialist_org = _person(SPECIALIST, "Demo Specialist", "CareFlow Demo Heart Centre")
        patient = demo._check(doctor.post("/api/patients", json={**SYNTHETIC_NAME, "sex": "unknown"}))
        patient_id = patient["id"]
        db = connect()
        _name_the_case(db, patient_id)
        db.commit()
        db.close()
        for purpose in demo.CONSENTS:
            demo._check(doctor.post(f"/api/patients/{patient_id}/consents", json={"purpose": purpose}))

        link = demo._check(doctor.post("/api/diagnofast/intakes", json={"form_id": "general_physician", "patient_name": "Demo Person", "patient_id": patient_id}))
        token = link["link"].split("token=")[1]
        interview = TestClient(demo_app())
        demo._check(interview.post(f"/api/diagnofast/patient/{token}/start", json={"language": "en-IN", "consent": True}))
        for _question in range(agent.MAX_QUESTIONS):
            reply = demo._check(interview.post(f"/api/diagnofast/patient/{token}/answer", data={"text": INTERVIEW_ANSWER}))
            if reply["finished"]:
                break

        demo._labs(doctor, patient_id, "Mr. Demo Person")
        _scanned_report(doctor, patient_id)
        demo._medicines(doctor, patient_id)
        encounter = demo._check(doctor.post(f"/api/patients/{patient_id}/encounters", json={"reason": "Synthetic review"}))
        session = demo._scribe(doctor, patient_id, encounter["id"])
        demo._check(doctor.post(f"/api/addons/clinscribe/sessions/{session}/draft-note", json={}))
        demo._referral(doctor, specialist, patient_id, specialist_org)
    finally:
        llm.ask = real_ask
    return {"workflow_ref": CASE, "created": True, "ai": "real" if real_ai else "demo_stub", "delivery": delivery.deliver_once()}
