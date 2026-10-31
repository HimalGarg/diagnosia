"""A synthetic patient journey through every tool, run through the real API.

Nothing here writes to tables directly, so it doubles as an end-to-end check that
the tools connect. Every name is invented. It must never run against real data.
"""
import io
from datetime import date, datetime, timedelta, timezone
from typing import Dict

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

DEMO_ORGANIZATION = "Demo City Clinic"
SPECIALIST_ORGANIZATION = "Demo Heart Centre"
CONSENTS = ("ai_processing", "ai_scribe", "referral_sharing", "maternal_care")


def _days(number: int) -> str:
    return (date.today() + timedelta(days=number)).isoformat()


def _lab_pdf(collected: str, haemoglobin: str, hba1c: str) -> bytes:
    lines = [
        "Demo Diagnostics Laboratory", "Patient Name : Mr. Ravi Kumar   Age : 56 Years", f"Sample Collected On : {collected}",
        "Test Result Unit Reference Range", f"Haemoglobin {haemoglobin} g/dL 13.0 - 17.0", f"HbA1c {hba1c} % 4.0 - 5.6",
        "Creatinine 1.02 mg/dl < 1.3", "Total Cholesterol 212 mg/dL < 200",
    ]
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    height = 800
    for line in lines:
        page.drawString(40, height, line)
        height -= 18
    page.save()
    return buffer.getvalue()


def _check(response, expected=(200, 201, 202, 204)):
    if response.status_code not in expected:
        raise RuntimeError(f"{response.request.method} {response.request.url.path} gave {response.status_code}: {response.text[:200]}")
    return response.json() if response.content else None


def _chronic(doctor, patient_id: str) -> None:
    base = "/api/addons/chroniccare"
    condition = _check(doctor.post(f"{base}/patients/{patient_id}/conditions", json={"condition_name": "Type 2 diabetes", "onset_date": "2021-04-01"}))
    plan = _check(
        doctor.post(
            f"{base}/patients/{patient_id}/plans",
            json={
                "title": "Diabetes and blood pressure follow-up",
                "condition_id": condition["id"],
                "goals": ["Measure blood pressure each week", "Bring readings to the next visit"],
                "measures": [
                    {"measure": "bp_systolic", "target_text": "As agreed with your doctor", "cadence_days": 7, "alert_high": 170, "escalation_text": "Rest for 15 minutes, measure again, and call the clinic today if it is still high."},
                    {"measure": "glucose_fasting", "target_text": "As agreed with your doctor", "cadence_days": 3},
                ],
            },
        )
    )
    _check(doctor.post(f"{base}/plans/{plan['id']}/approve"))
    for days_ago, systolic, glucose in ((21, 148, 131), (14, 142, 126), (7, 138, 122), (1, 134, 118)):
        when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        _check(doctor.post(f"{base}/patients/{patient_id}/readings", json={"measure": "bp_systolic", "value": systolic, "effective_at": when}))
        _check(doctor.post(f"{base}/patients/{patient_id}/readings", json={"measure": "glucose_fasting", "value": glucose, "effective_at": when}))


def _labs(doctor, patient_id: str) -> None:
    for collected, haemoglobin, hba1c in (("12/03/2026", "11.2 L", "7.4"), ("20/08/2026", "12.6 L", "6.8")):
        report = _check(
            doctor.post(
                "/api/addons/lablens/reports",
                data={"patient_id": patient_id},
                files={"file": ("demo-report.pdf", _lab_pdf(collected, haemoglobin, hba1c), "application/pdf")},
            )
        )
        _check(doctor.post(f"/api/addons/lablens/reports/{report['id']}/confirm", json={}))


def _medicines(doctor, patient_id: str) -> None:
    base = "/api/addons/medguard"
    _check(doctor.post(f"/api/patients/{patient_id}/allergies", json={"substance": "Penicillin", "reaction": "Rash", "severity": "moderate"}))
    for name, source, frequency in (("Glycomet 500", "prescription", "1-0-1"), ("Telma 40", "prescription", "OD"), ("Combiflam", "patient_reported", "SOS"), ("Dolo 650", "patient_reported", "BD")):
        _check(doctor.post(f"{base}/patients/{patient_id}/medications", json={"written_name": name, "source": source, "frequency": frequency}))
    _check(doctor.post(f"{base}/reconcile", json={"patient_id": patient_id}))


def _scribe(doctor, patient_id: str, encounter_id: str) -> str:
    base = "/api/addons/clinscribe"
    session = _check(doctor.post(f"{base}/encounters/{encounter_id}/sessions", json={"language": "en-IN"}))
    for speaker, text in (
        ("clinician", "How has your sugar been since the last visit?"),
        ("patient", "Better. My fasting reading was 118 this week, and I have had no chest pain."),
        ("patient", "I still take the painkiller when my knee hurts."),
        ("clinician", "Continue metformin 500 mg twice daily. We will review your medicines at the next visit."),
    ):
        _check(doctor.post(f"{base}/sessions/{session['id']}/segments", json={"text": text, "speaker": speaker}))
    _check(doctor.post(f"{base}/sessions/{session['id']}/state", json={"state": "stopped"}))
    return session["id"]


def _referral(doctor, specialist, patient_id: str, organization_id: str) -> None:
    base = "/api/addons/referrals"
    referral = _check(
        doctor.post(
            base,
            json={
                "patient_id": patient_id, "receiving_organization_id": organization_id, "specialty": "Cardiology",
                "reason": "Palpitations on exertion for two months", "clinical_question": "Is further heart testing needed?",
                "priority": "routine", "respond_by": _days(10),
            },
        )
    )
    _check(specialist.post(f"{base}/{referral['id']}/acknowledge"))
    _check(specialist.patch(f"{base}/{referral['id']}/status", json={"status": "accepted"}))
    _check(specialist.post(f"{base}/{referral['id']}/appointments", json={"scheduled_at": f"{_days(5)}T10:30", "location": "Room 2", "booked": True}))


def _discharge(doctor, patient_id: str) -> None:
    base = "/api/addons/dischargecare"
    episode = _check(
        doctor.post(
            f"{base}/episodes",
            json={
                "patient_id": patient_id, "discharge_date": _days(1), "destination": "Home with family",
                "problems": "Observation after low blood sugar, resolved", "emergency_contact": "Demo City Clinic: 040-555-0100",
            },
        )
    )
    url = f"{base}/episodes/{episode['id']}"
    _check(doctor.post(f"{url}/instructions", json={"topic": "Food", "text": "Eat regular meals, and keep something sweet with you."}))
    _check(doctor.post(f"{url}/barriers", json={"description": "Transport home not arranged", "owner": "Ward coordinator"}))
    _check(doctor.post(f"{url}/pending-results", json={"test_name": "Repeat HbA1c", "expected_by": _days(3)}))
    _check(doctor.post(f"{url}/tasks", json={"title": "Book a follow-up visit", "audience": "patient"}))
    _check(doctor.post(f"{url}/approve"))


def _maternal(doctor, patient_id: str) -> None:
    base = "/api/addons/maternacare"
    episode = _check(
        doctor.post(
            f"{base}/episodes",
            json={"patient_id": patient_id, "facility": DEMO_ORGANIZATION, "gravida": 2, "para": 1, "escalation_contact": "Demo City Clinic: 040-555-0100", "concern_response_hours": 4},
        )
    )
    url = f"{base}/episodes/{episode['id']}"
    _check(doctor.post(f"{url}/due-date", json={"estimated_due_date": _days(70), "basis": "Synthetic demo value"}))
    _check(doctor.post(f"{url}/visits", json={"planned_date": _days(14), "purpose": "Demo clinic visit"}))
    _check(doctor.post(f"{url}/observations", json={"label": "Weight", "value_number": 62, "unit": "kg"}))


def _diagnofast(doctor, patient_id: str, name: str) -> None:
    """A link waiting in the queue. Finishing the interview needs AI, so that part is left to try."""
    _check(doctor.post("/api/diagnofast/intakes", json={"form_id": "general_physician", "patient_name": name, "patient_id": patient_id}))


def _amr(admin, organization_id: str) -> None:
    base = f"/api/addons/amrguard/organizations/{organization_id}"
    header = "patient_ref,isolate_ref,specimen,organism,antibiotic,mic,zone_mm,interpretation,standard,standard_version,collected_on,laboratory"
    rows = [
        f"DEMO{n},DI{n},Urine,E. coli,CIP,0.25,,{'R' if n < 9 else 'S'},CLSI,DEMO-EDITION,{date.today().year}-01-15,Demo Lab"
        for n in range(34)
    ]
    data = "\n".join([header, *rows]).encode()
    _check(admin.post(f"{base}/imports", files={"file": ("demo.csv", data, "text/csv")}))
    _check(admin.post(f"{base}/antibiograms", json={"period_start": f"{date.today().year}-01-01", "period_end": date.today().isoformat()}))


def seed(people: Dict[str, object], organizations: Dict[str, str]) -> Dict[str, str]:
    """Runs the journey. `people` are signed-in clients. Returns the patient records it made."""
    doctor, patient_client = people["doctor"], people["patient"]
    ravi = _check(doctor.post("/api/patients", json={"first_name": "Ravi", "last_name": "Kumar", "date_of_birth": "1970-05-01", "sex": "male", "phone": "+91 90000 00001"}))
    sita = _check(doctor.post("/api/patients", json={"first_name": "Sita", "last_name": "Devi", "date_of_birth": "1995-02-11", "sex": "female", "phone": "+91 90000 00002"}))
    code = _check(doctor.post(f"/api/patients/{ravi['id']}/share-codes", json={"purpose": "claim"}))["code"]
    _check(patient_client.post("/api/patients/join", json={"code": code}))
    for patient in (ravi, sita):
        for purpose in CONSENTS:
            _check(doctor.post(f"/api/patients/{patient['id']}/consents", json={"purpose": purpose}))

    encounter = _check(doctor.post(f"/api/patients/{ravi['id']}/encounters", json={"reason": "Diabetes follow-up"}))
    _diagnofast(doctor, ravi["id"], "Ravi Kumar")
    _labs(doctor, ravi["id"])
    _medicines(doctor, ravi["id"])
    _chronic(doctor, ravi["id"])
    session_id = _scribe(doctor, ravi["id"], encounter["id"])
    _referral(doctor, people["specialist"], ravi["id"], organizations["specialist"])
    _discharge(doctor, ravi["id"])
    _maternal(doctor, sita["id"])
    _amr(doctor, organizations["clinic"])
    _check(
        doctor.post(
            f"/api/patients/{sita['id']}/reminders",
            json={"title": "Clinic visit next week", "module": "maternacare", "scheduled_for": datetime.now(timezone.utc).isoformat()},
        )
    )
    return {"ravi": ravi["id"], "sita": sita["id"], "session": session_id, "encounter": encounter["id"]}


def describe_accounts(accounts: Dict[str, dict]) -> str:
    rows = [f"{item['label']}: {item['email']}  password: {item['password']}" for item in accounts.values()]
    return "Diagnosia local demo accounts. Synthetic data only.\n\n" + "\n".join(rows) + "\n"

