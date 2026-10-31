"""Exports our records as FHIR R4 resources for exchange with other systems.

This is generic R4. It is not validated against the NRCeS/ABDM profiles, so it
must not be described as ABDM-compliant. See FHIR_MAPPING.md.
"""
import json
import sqlite3
from typing import List, Set

from backend.records.store import new_id, now_iso, rows

LOINC = "http://loinc.org"
OBSERVATION_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
ENCOUNTER_CLASS = {
    "outpatient": "AMB",
    "inpatient": "IMP",
    "emergency": "EMER",
    "telehealth": "VR",
    "home": "HH",
}
ENCOUNTER_STATUS = {
    "planned": "planned",
    "in_progress": "in-progress",
    "finished": "finished",
    "cancelled": "cancelled",
}
MEDICATION_STATUS = {"active": "active", "on_hold": "on-hold", "stopped": "stopped", "completed": "completed"}
TASK_STATUS = {
    "held": "on-hold",
    "open": "requested",
    "done": "completed",
    "missed": "failed",
    "blocked": "on-hold",
    "cancelled": "cancelled",
}
REFERRAL_OPEN = {"closed": "completed", "cancelled": "revoked", "declined": "revoked"}


def subject(patient_id: str) -> dict:
    return {"reference": f"Patient/{patient_id}"}


def patient_resource(patient: dict) -> dict:
    resource = {
        "resourceType": "Patient",
        "id": patient["id"],
        "name": [{"family": patient["last_name"], "given": [patient["first_name"]]}],
        "active": patient["status"] == "active",
    }
    if patient["sex"]:
        resource["gender"] = patient["sex"]
    if patient["date_of_birth"]:
        resource["birthDate"] = patient["date_of_birth"]
    if patient["phone"]:
        resource["telecom"] = [{"system": "phone", "value": patient["phone"]}]
    return resource


def encounter_resource(encounter: dict) -> dict:
    period = {"start": encounter["started_at"]}
    if encounter["ended_at"]:
        period["end"] = encounter["ended_at"]
    return {
        "resourceType": "Encounter",
        "id": encounter["id"],
        "status": ENCOUNTER_STATUS[encounter["status"]],
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": ENCOUNTER_CLASS[encounter["type"]],
        },
        "subject": subject(encounter["patient_id"]),
        "period": period,
        "reasonCode": [{"text": encounter["reason"]}] if encounter["reason"] else [],
    }


def approved_loinc_codes(db: sqlite3.Connection) -> Set[str]:
    return {row["proposed_code"] for row in db.execute("SELECT proposed_code FROM loinc_mappings WHERE status = 'approved'")}


def observation_resource(observation: dict, approved_codes: Set[str]) -> dict:
    """A code is exported only once reviewed. Until then the lab's own name stands alone."""
    code = {"text": observation["label"]}
    if observation["code"] in approved_codes:
        code["coding"] = [{"system": LOINC, "code": observation["code"], "display": observation["label"]}]
    category = "vital-signs" if observation["category"] == "vital" else "laboratory"
    if observation["category"] in ("symptom", "maternal"):
        category = "survey"
    resource = {
        "resourceType": "Observation",
        "id": observation["id"],
        "status": "corrected" if observation["review_status"] == "corrected" else "final",
        "category": [{"coding": [{"system": OBSERVATION_CATEGORY, "code": category}]}],
        "code": code,
        "subject": subject(observation["patient_id"]),
        "effectiveDateTime": observation["effective_at"],
    }
    if observation["value_number"] is not None:
        resource["valueQuantity"] = {"value": observation["value_number"], "unit": observation["unit"]}
    elif observation["value_text"]:
        resource["valueString"] = observation["value_text"]
    if observation["reference_text"]:
        resource["referenceRange"] = [{"text": observation["reference_text"]}]
    return resource


def medication_resource(medication: dict) -> dict:
    resource = {
        "resourceType": "MedicationStatement",
        "id": medication["id"],
        "status": MEDICATION_STATUS[medication["status"]],
        "medicationCodeableConcept": {"text": medication["written_name"]},
        "subject": subject(medication["patient_id"]),
        "dosage": [{"text": " ".join(filter(None, (medication["dose"], medication["frequency"])))}],
    }
    if medication["start_date"]:
        resource["effectivePeriod"] = {"start": medication["start_date"]}
    return resource


def allergy_resource(allergy: dict) -> dict:
    return {
        "resourceType": "AllergyIntolerance",
        "id": allergy["id"],
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    "code": "active" if allergy["status"] == "active" else "resolved",
                }
            ]
        },
        "code": {"text": allergy["substance"]},
        "patient": subject(allergy["patient_id"]),
    }


def task_resource(task: dict) -> dict:
    return {
        "resourceType": "Task",
        "id": task["id"],
        "status": TASK_STATUS[task["status"]],
        "intent": "plan",
        "priority": "urgent" if task["priority"] == "high" else "routine",
        "description": task["title"],
        "for": subject(task["patient_id"]),
    }


def referral_resource(referral: dict) -> dict:
    return {
        "resourceType": "ServiceRequest",
        "id": referral["id"],
        "status": REFERRAL_OPEN.get(referral["status"], "active"),
        "intent": "order",
        "priority": referral["priority"],
        "code": {"text": f"Referral to {referral['specialty']}"},
        "subject": subject(referral["patient_id"]),
        "authoredOn": referral["created_at"],
        "reasonCode": [{"text": referral["reason"]}],
    }


def appointment_resource(appointment: dict, patient_id: str, referral_id: str) -> dict:
    status = {
        "offered": "proposed",
        "booked": "booked",
        "rescheduled": "booked",
        "cancelled": "cancelled",
        "attended": "fulfilled",
        "no_show": "noshow",
    }[appointment["status"]]
    return {
        "resourceType": "Appointment",
        "id": appointment["id"],
        "status": status,
        "basedOn": [{"reference": f"ServiceRequest/{referral_id}"}],
        "start": appointment["scheduled_at"],
        "participant": [{"actor": subject(patient_id), "status": "accepted"}],
    }


def composition_resource(note: dict, content: dict, author_name: str) -> dict:
    return {
        "resourceType": "Composition",
        "id": note["id"],
        "status": "final",
        "type": {"coding": [{"system": LOINC, "code": "11506-3", "display": "Progress note"}]},
        "subject": subject(note["patient_id"]),
        "encounter": {"reference": f"Encounter/{note['encounter_id']}"},
        "date": note["approved_at"],
        "author": [{"display": author_name}],
        "title": "Clinical note",
        "section": [
            {
                "title": section["heading"],
                "text": {
                    "status": "generated",
                    "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\">"
                    + " ".join(_xml_text(line["text"]) for line in section["statements"])
                    + "</div>",
                },
            }
            for section in content["sections"]
        ],
    }


def _xml_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bundle(resources: List[dict], bundle_type: str = "collection") -> dict:
    return {
        "resourceType": "Bundle",
        "id": new_id(),
        "type": bundle_type,
        "timestamp": now_iso(),
        "entry": [{"fullUrl": f"urn:uuid:{item['id']}", "resource": item} for item in resources],
    }


def patient_bundle(db: sqlite3.Connection, patient: dict) -> dict:
    """Reviewed information only. Anything still waiting for review stays out."""
    patient_id = patient["id"]
    query = lambda sql: rows(db.execute(sql, (patient_id,)))  # noqa: E731
    resources = [patient_resource(patient)]
    resources += [
        encounter_resource(row)
        for row in query("SELECT * FROM encounters WHERE patient_id = ? ORDER BY started_at")
    ]
    approved_codes = approved_loinc_codes(db)
    resources += [
        observation_resource(row, approved_codes)
        for row in query(
            """
            SELECT * FROM observations WHERE patient_id = ?
              AND review_status IN ('accepted', 'corrected') ORDER BY effective_at
            """
        )
    ]
    resources += [
        medication_resource(row)
        for row in query(
            "SELECT * FROM medication_records WHERE patient_id = ? AND source = 'reconciled' AND review_status = 'accepted'"
        )
    ]
    resources += [allergy_resource(row) for row in query("SELECT * FROM allergies WHERE patient_id = ?")]
    resources += [task_resource(row) for row in query("SELECT * FROM tasks WHERE patient_id = ?")]
    for referral in query("SELECT * FROM referrals WHERE patient_id = ?"):
        resources.append(referral_resource(referral))
        for appointment in rows(
            db.execute("SELECT * FROM referral_appointments WHERE referral_id = ?", (referral["id"],))
        ):
            resources.append(appointment_resource(appointment, patient_id, referral["id"]))
    for note in query("SELECT * FROM clinical_notes WHERE patient_id = ? AND status = 'approved'"):
        version = db.execute(
            "SELECT content, author_name FROM clinical_note_versions WHERE note_id = ? ORDER BY version DESC LIMIT 1",
            (note["id"],),
        ).fetchone()
        resources.append(composition_resource(note, json.loads(version["content"]), version["author_name"]))
    return bundle(resources)
