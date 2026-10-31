from datetime import datetime, timedelta, timezone

import pytest

from backend.config import settings
from backend.db import connect
from backend.reminders import adapters, service
from backend.tests.conftest import grant, linked_patient


def moment(hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def schedule(client, patient_id, hours=-1, **fields):
    return client.post(
        f"/api/patients/{patient_id}/reminders",
        json={"title": "Antenatal visit next week", "scheduled_for": moment(hours), **fields},
    )


def reminder_row(reminder_id):
    db = connect()
    try:
        return dict(db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())
    finally:
        db.close()


def sweep():
    db = connect()
    service.process_due(db)
    db.commit()
    db.close()


def states(reminder):
    return [event["to_state"] for event in reminder["events"]]


class Provider:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, phone, message):
        self.sent.append((phone, message))
        if self.fail:
            raise adapters.DeliveryFailed("synthetic failure")
        return "provider-ref-1"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, "enable_external_messaging", True)
    fake = Provider()
    adapters.register("sms", fake)
    yield fake
    adapters.clear()


def test_a_future_reminder_waits(doctor, patient):
    assert schedule(doctor, patient["id"], hours=5).json()["state"] == "pending"


def test_a_patient_without_the_app_goes_straight_to_staff(doctor, patient):
    reminder = schedule(doctor, patient["id"]).json()
    assert reminder["state"] == "staff_followup_required"
    assert reminder["followup_task_id"]
    task = doctor.get(f"/api/patients/{patient['id']}/tasks").json()[0]
    assert task["owner_user_id"] == doctor.id
    assert "Contact patient" in task["title"]


def test_in_app_sent_is_never_called_delivered(doctor, person):
    account, record = linked_patient(doctor, person)
    reminder = schedule(doctor, record["id"]).json()
    assert reminder["state"] == "in_app_sent"
    assert "delivered" not in states(reminder)
    assert any("Reminder" in note["title"] for note in account.get("/api/notifications").json())


def test_no_reply_in_time_moves_to_staff_follow_up(doctor, person):
    account, record = linked_patient(doctor, person)
    reminder = schedule(doctor, record["id"], respond_within_hours=1).json()
    db = connect()
    db.execute("UPDATE reminders SET sent_at = ?", (moment(-2),))
    db.commit()
    db.close()
    sweep()
    assert reminder_row(reminder["id"])["state"] == "staff_followup_required"


def test_acknowledging_closes_the_loop(doctor, person):
    account, record = linked_patient(doctor, person)
    reminder = schedule(doctor, record["id"]).json()
    response = account.post(f"/api/reminders/{reminder['id']}/acknowledge")
    assert response.json()["state"] == "acknowledged"


def test_sms_is_not_used_without_consent(doctor, patient, provider):
    doctor.patch(f"/api/patients/{patient['id']}", json={"phone": "+91 90000 00000"})
    response = doctor.put(f"/api/patients/{patient['id']}/communication-preferences", json={"sms": True})
    assert response.json()["detail"]["code"] == "consent_required"
    reminder = schedule(doctor, patient["id"]).json()
    assert provider.sent == []
    assert reminder["state"] == "staff_followup_required"


def configured_for_sms(doctor, patient):
    doctor.patch(f"/api/patients/{patient['id']}", json={"phone": "+91 90000 00000"})
    grant(doctor, patient["id"], "external_messaging")
    doctor.put(f"/api/patients/{patient['id']}/communication-preferences", json={"sms": True, "in_app": False})


def test_handing_to_a_provider_is_not_delivery(doctor, patient, provider):
    configured_for_sms(doctor, patient)
    reminder = schedule(doctor, patient["id"]).json()
    assert reminder["state"] == "external_delivery_pending"
    assert reminder["channel"] == "sms"


def test_outside_carriers_never_see_clinical_text(doctor, patient, provider):
    configured_for_sms(doctor, patient)
    schedule(doctor, patient["id"], title="Check your blood sugar and Hb", module="lablens")
    message = provider.sent[0][1].lower()
    assert "blood" not in message
    assert message == adapters.NEUTRAL_MESSAGE.lower()


def test_only_a_receipt_can_say_delivered(doctor, patient, provider):
    configured_for_sms(doctor, patient)
    reminder = schedule(doctor, patient["id"]).json()
    db = connect()
    assert service.record_receipt(db, reminder["id"], True)["state"] == "delivered"
    db.commit()
    db.close()


def test_failed_delivery_creates_a_staff_task(doctor, patient, provider):
    provider.fail = True
    configured_for_sms(doctor, patient)
    reminder = schedule(doctor, patient["id"]).json()
    assert reminder["state"] == "staff_followup_required"
    assert "failed" in states(reminder)
    assert doctor.get(f"/api/patients/{patient['id']}/tasks").json()[0]["link_type"] == "reminder"


def test_a_failure_receipt_creates_a_staff_task(doctor, patient, provider):
    configured_for_sms(doctor, patient)
    reminder = schedule(doctor, patient["id"]).json()
    db = connect()
    assert service.record_receipt(db, reminder["id"], False)["state"] == "staff_followup_required"
    db.commit()
    db.close()


def test_messaging_switch_alone_sends_nothing(doctor, patient, monkeypatch):
    monkeypatch.setattr(settings, "enable_external_messaging", True)
    doctor.patch(f"/api/patients/{patient['id']}", json={"phone": "+91 90000 00000"})
    grant(doctor, patient["id"], "external_messaging")
    doctor.put(f"/api/patients/{patient['id']}/communication-preferences", json={"sms": True})
    assert schedule(doctor, patient["id"]).json()["state"] == "staff_followup_required"


def test_a_registered_adapter_is_unused_while_messaging_is_off(doctor, patient):
    fake = Provider()
    adapters.register("sms", fake)
    try:
        doctor.patch(f"/api/patients/{patient['id']}", json={"phone": "+91 90000 00000"})
        grant(doctor, patient["id"], "external_messaging")
        doctor.put(f"/api/patients/{patient['id']}/communication-preferences", json={"sms": True})
        schedule(doctor, patient["id"])
    finally:
        adapters.clear()
    assert fake.sent == []


def test_revoked_consent_stops_sms_at_once(doctor, patient, provider):
    configured_for_sms(doctor, patient)
    consent = doctor.get(f"/api/patients/{patient['id']}/consents").json()["consents"][0]
    doctor.post(f"/api/patients/{patient['id']}/consents/{consent['id']}/revoke")
    reminder = schedule(doctor, patient["id"]).json()
    assert provider.sent == []
    assert reminder["state"] == "staff_followup_required"


def test_staff_record_the_outcome(doctor, patient):
    reminder = schedule(doctor, patient["id"]).json()
    url = f"/api/reminders/{reminder['id']}/outcome"
    assert doctor.post(url, json={"outcome": "no_answer"}).json()["state"] == "staff_followup_required"
    done = doctor.post(url, json={"outcome": "reached"}).json()
    assert done["state"] == "acknowledged"
    assert done["contact_attempts"] == 2
    assert doctor.get(f"/api/patients/{patient['id']}/tasks").json()[0]["status"] == "done"


def test_follow_up_list_is_limited_to_my_patients(doctor, person, patient):
    schedule(doctor, patient["id"])
    assert len(doctor.get("/api/reminders/follow-up").json()) == 1
    assert person("doctor").get("/api/reminders/follow-up").json() == []


def test_patients_cannot_see_the_follow_up_list(person):
    assert person("patient").get("/api/reminders/follow-up").status_code == 403
    assert person("patient").get("/api/reminders/call-list").status_code == 403


def test_pending_staff_cannot_see_the_list(person):
    assert person("doctor", verified=False).get("/api/reminders/call-list").status_code == 403


def test_the_list_can_be_filtered_to_one_module(doctor, patient):
    schedule(doctor, patient["id"], module="lablens")
    schedule(doctor, patient["id"], module="general")
    assert len(doctor.get("/api/reminders/follow-up", params={"module": "lablens"}).json()) == 1


def test_a_printed_list_holds_no_clinical_detail(doctor, patient):
    schedule(doctor, patient["id"], title="Haemoglobin 7.1 follow-up", module="lablens")
    body = doctor.get("/api/reminders/call-list").json()
    text = str(body).lower()
    assert "haemoglobin" not in text
    assert "lablens" not in text
    assert body["entries"][0]["name"] == "Ravi K."
    assert body["entries"][0]["reason"] == "Clinic follow-up"


def test_printing_a_list_is_audited(doctor, patient):
    schedule(doctor, patient["id"])
    doctor.get("/api/reminders/call-list")
    db = connect()
    assert db.execute("SELECT 1 FROM audit_events WHERE action = 'reminder.call_list.printed'").fetchone()
    db.close()


def test_every_step_is_audited(doctor, patient):
    reminder = schedule(doctor, patient["id"]).json()
    doctor.post(f"/api/reminders/{reminder['id']}/outcome", json={"outcome": "reached"})
    db = connect()
    actions = {row[0] for row in db.execute("SELECT action FROM audit_events WHERE resource_id = ?", (reminder["id"],))}
    db.close()
    assert {"reminder.staff_followup_required", "reminder.outcome_recorded", "reminder.acknowledged"} <= actions


def test_lablens_reminders_claim_neither_escalation_nor_delivery(doctor, patient):
    reminder = schedule(doctor, patient["id"], module="lablens", priority="high").json()
    assert "not an emergency" in reminder["notice"]
    assert reminder["state"] != "delivered"
    assert "delivered" not in states(reminder)
    assert reminder["priority"] == "high"


def test_only_the_care_team_can_schedule(doctor, person):
    account, record = linked_patient(doctor, person)
    assert schedule(account, record["id"]).status_code == 403
    assert schedule(person("doctor"), record["id"]).status_code == 404


def test_a_task_from_another_patient_cannot_be_linked(doctor, patient):
    other = doctor.post("/api/patients", json={"first_name": "Sita", "last_name": "Devi"}).json()
    task = doctor.post(f"/api/patients/{other['id']}/tasks", json={"title": "x"}).json()
    assert schedule(doctor, patient["id"], task_id=task["id"]).status_code == 422


def test_strangers_cannot_touch_a_reminder(doctor, person, patient):
    reminder = schedule(doctor, patient["id"]).json()
    stranger = person("doctor")
    assert stranger.post(f"/api/reminders/{reminder['id']}/outcome", json={"outcome": "reached"}).status_code == 404
    assert stranger.post(f"/api/reminders/{reminder['id']}/cancel").status_code == 404
    assert person("patient").post(f"/api/reminders/{reminder['id']}/acknowledge").status_code == 404


def test_cancelling_stops_the_reminder_and_its_task(doctor, patient):
    reminder = schedule(doctor, patient["id"]).json()
    assert doctor.post(f"/api/reminders/{reminder['id']}/cancel").json()["state"] == "cancelled"
    assert doctor.get(f"/api/patients/{patient['id']}/tasks").json()[0]["status"] == "done"
    assert doctor.get("/api/reminders/follow-up").json() == []


def test_a_cancelled_reminder_is_not_sent(doctor, person):
    account, record = linked_patient(doctor, person)
    reminder = schedule(doctor, record["id"], hours=5).json()
    doctor.post(f"/api/reminders/{reminder['id']}/cancel")
    db = connect()
    db.execute("UPDATE reminders SET scheduled_for = ?", (moment(-1),))
    db.commit()
    db.close()
    sweep()
    assert reminder_row(reminder["id"])["state"] == "cancelled"
    assert not account.get("/api/notifications").json()


def test_the_sweep_does_not_send_twice(doctor, person):
    account, record = linked_patient(doctor, person)
    schedule(doctor, record["id"])
    sweep()
    sweep()
    assert len(account.get("/api/notifications").json()) == 1
