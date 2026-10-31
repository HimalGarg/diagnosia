"""CareFlow: the event contract, the outbox, delivery, access control and the Meko and YSQL adapters.

No real services here. `FakeDatabase` stands in for MongoDB, and the live checks are in
backend/tests/live/test_live_integrations.py.
"""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend import llm
from backend.careflow import contract, delivery, demo as careflow_demo, events, live, stores
from backend.careflow.contract import CareEvent, ContractError
from backend.config import settings
from backend.db import connect
from backend.integrations import execution, meko, yugabyte
from backend.tests.conftest import grant


def valid_event(**changes):
    fields = {
        "event_id": "evt_abc123def456", "event_type": "lab.review_required", "module": "LabLens", "workflow_stage": "during_visit",
        "source_operation": "lablens.extract", "workflow_ref": "case_abc123",
        "tenant_ref": "tenant_abc123", "correlation_id": "corr_abc123def", "occurred_at": datetime.now(timezone.utc),
        "status": "review_required", "actor_type": "system", "metadata": {},
    }
    return {**fields, **changes}


class Cursor(list):
    def sort(self, keys):
        ordered = self
        for key, direction in reversed(keys):
            ordered = sorted(ordered, key=lambda item: item[key], reverse=direction < 0)
        return Cursor(ordered)

    def limit(self, count):
        return Cursor(self[:count])


class DuplicateKeyError(Exception):
    pass


class FakeCollection:
    def __init__(self):
        self.documents = {}

    def insert_one(self, document):
        if document["_id"] in self.documents:
            raise DuplicateKeyError()
        self.documents[document["_id"]] = document

    def replace_one(self, query, document, upsert=False):
        self.documents[query["_id"]] = document

    def _matching(self, query):
        def fits(document):
            for key, want in query.items():
                if isinstance(want, dict) and "$in" in want:
                    if document.get(key) not in want["$in"]:
                        return False
                elif not isinstance(want, dict) and document.get(key) != want:
                    return False
            return True

        return [document for document in self.documents.values() if fits(document)]

    def find(self, query):
        return Cursor(self._matching(query))

    def count_documents(self, query):
        return len(self._matching(query))

    def create_index(self, *_args, **_options):
        pass

    def find_one(self, query):
        found = self._matching(query)
        return found[0] if found else None

    def delete_one(self, query):
        found = self._matching(query)
        for document in found[:1]:
            del self.documents[document["_id"]]
        return type("Deleted", (), {"deleted_count": len(found[:1])})()


class FakeDatabase:
    def __init__(self):
        self.collections = {name: FakeCollection() for name in ("care_events", "timeline_projections", "integration_delivery")}
        self.down = False

    def __getitem__(self, name):
        return self.collections[name]

    def command(self, *_args, **_options):
        if self.down:
            raise ConnectionError("synthetic outage")

    def list_collection_names(self):
        return list(self.collections)


@pytest.fixture
def mongo(monkeypatch):
    database = FakeDatabase()
    store = stores.MongoEventStore(database, "mongodb_atlas")
    monkeypatch.setattr(settings, "mongodb_enabled", True)
    monkeypatch.setattr(settings, "mongodb_uri", "mongodb://synthetic.invalid/")
    monkeypatch.setattr(stores, "mongo_store", lambda: store)
    return store


def outbox(*columns):
    db = connect()
    found = db.execute("SELECT event, module, event_type, status FROM careflow_outbox ORDER BY occurred_at, event_id").fetchall()
    db.close()
    return found


def event_types():
    return [row["event_type"] for row in outbox()]


def reconcile(client, patient):
    client.post(f"/api/addons/medguard/patients/{patient['id']}/medications", json={"written_name": "Dolo 650", "source": "prescription", "frequency": "SOS"})
    return client.post("/api/addons/medguard/reconcile", json={"patient_id": patient["id"]})


def run_delivery():
    db = connect()
    try:
        return delivery.deliver_channel(db, "mongodb", lambda event: stores.mongo_store().put(event))
    finally:
        db.close()


# The contract


def test_a_valid_event_is_accepted():
    assert CareEvent(**valid_event()).schema_version == contract.SCHEMA_VERSION


@pytest.mark.parametrize(
    "metadata",
    [
        {"patient_name": "Synthetic Name"},
        {"phone": "9999999999"},
        {"notes": "free text is never allowed"},
        {"review_required": "yes"},
        {"item_count": -1},
        {"item_count": True},
        {"model_provider": "someone-else"},
        {"model_name": "has spaces in it"},
        {"error_category": "Connection refused to db.internal:5432"},
    ],
)
def test_metadata_outside_the_allowlist_is_refused(metadata):
    with pytest.raises(ValueError):
        CareEvent(**valid_event(metadata=metadata))


@pytest.mark.parametrize(
    "field, value",
    [("workflow_stage", "after_visit"), ("workflow_stage", "somewhere"), ("source_operation", "Ravi Kumar's visit"), ("source_ref", "not-a-request-id")],
)
def test_the_source_fields_are_checked_too(field, value):
    with pytest.raises(ValueError):
        CareEvent(**valid_event(**{field: value}))


def test_an_older_event_is_upgraded_without_changing_what_it_says():
    old = {"schema_version": "1.0", "event_id": "evt_abc123def456", "module": "LabLens", "event_type": "lab.review_required", "status": "review_required"}
    new = contract.upgrade(old)
    assert new["schema_version"] == contract.SCHEMA_VERSION
    assert new["workflow_stage"] == "during_visit" and new["source_operation"] == "lab.review_required"
    assert new["event_type"] == old["event_type"] and new["status"] == old["status"]
    assert contract.upgrade(new) == new


def test_too_many_metadata_fields_are_refused():
    with pytest.raises(ContractError):
        contract.check_metadata({key: True for key in [f"k{n}" for n in range(contract.MAX_METADATA_KEYS + 1)]})


@pytest.mark.parametrize("field, value", [("patient_name", "Synthetic"), ("email", "a@example.com"), ("notes", "text"), ("result", "12.3")])
def test_unknown_fields_are_refused(field, value):
    with pytest.raises(ValueError):
        CareEvent(**valid_event(**{field: value}))


@pytest.mark.parametrize("reference", ["ravi.kumar@example.com", "9999999999", "Ravi Kumar", "MRN-1234", "case_Ravi Kumar"])
def test_a_workflow_reference_must_be_generated(reference):
    with pytest.raises(ValueError):
        CareEvent(**valid_event(workflow_ref=reference))


def test_a_module_can_only_record_its_own_events():
    with pytest.raises(ValueError):
        CareEvent(**valid_event(module="MedGuard"))


def test_a_handoff_goes_to_a_different_known_module():
    with pytest.raises(ValueError):
        CareEvent(**valid_event(target_module="LabLens"))
    with pytest.raises(ValueError):
        CareEvent(**valid_event(target_module="Ninth Tool"))


def test_every_audit_mapping_is_a_real_event_type():
    for mapping in contract.AUDIT_EVENTS.values():
        assert mapping.event_type in contract.EVENT_TYPES[mapping.module]
        assert mapping.failed is None or mapping.failed in contract.EVENT_TYPES[mapping.module]


def test_no_clinical_decision_event_exists():
    words = " ".join(name for names in contract.EVENT_TYPES.values() for name in names)
    for banned in ("diagnosis", "triage", "treatment", "dosage", "prescribed"):
        assert banned not in words


# The outbox


def test_an_event_commits_with_the_change_it_describes(doctor, patient):
    assert reconcile(doctor, patient).status_code == 201
    assert "safety_check.completed" in event_types()


def test_a_failed_request_leaves_no_success_event(doctor, patient, monkeypatch):
    grant(doctor, patient["id"], "ai_scribe")
    encounter = doctor.post(f"/api/patients/{patient['id']}/encounters", json={}).json()
    session = doctor.post(f"/api/addons/clinscribe/encounters/{encounter['id']}/sessions", json={}).json()
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/segments", json={"text": "Synthetic line"})
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/state", json={"state": "stopped"})

    def down(*_args, **_options):
        raise llm.AIUnavailable("synthetic outage")

    monkeypatch.setattr(llm, "ask", down)
    assert doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/draft-note", json={}).status_code == 503
    found = event_types()
    assert "note_draft.created" not in found
    assert "note_draft.failed" in found
    failed = next(json.loads(row["event"]) for row in outbox() if row["event_type"] == "note_draft.failed")
    assert failed["metadata"] == {"error_category": "ai_unavailable"}


def test_a_refused_route_records_nothing(person, doctor, patient):
    stranger = person("doctor")
    assert stranger.post("/api/addons/medguard/reconcile", json={"patient_id": patient["id"]}).status_code == 404
    assert "safety_check.completed" not in event_types()


def test_a_refused_event_never_stops_the_tool(doctor, patient, monkeypatch):
    def broken(**_fields):
        raise ContractError("synthetic")

    monkeypatch.setattr(events, "CareEvent", broken)
    assert reconcile(doctor, patient).status_code == 201
    assert event_types() == []


def test_with_real_patient_data_allowed_nothing_is_recorded_or_sent(doctor, patient, monkeypatch):
    monkeypatch.setattr(settings, "allow_real_patient_data", True)
    assert events.active() is False
    reconcile(doctor, patient)
    assert event_types() == []
    assert delivery.deliver_once() == {}


def test_a_workflow_reference_says_nothing_about_the_patient(doctor, patient):
    reconcile(doctor, patient)
    event = json.loads(outbox()[0]["event"])
    assert patient["id"] not in json.dumps(event)
    assert patient["last_name"].lower() not in event["workflow_ref"]
    assert contract.WORKFLOW_REF.match(event["workflow_ref"])


# The whole journey


@pytest.fixture
def journey(client):
    return careflow_demo.run()


def stored_text():
    db = connect()
    text = "\n".join(row["event"] for row in db.execute("SELECT event FROM careflow_outbox"))
    db.close()
    return text


def test_the_journey_passes_through_the_tools_in_order(journey):
    found = event_types()
    order = ["intake.link_created", "intake.started", "intake.completed", "lab.report_received", "lab.extraction.completed", "lab.report_confirmed", "safety_check.completed", "note_draft.created", "referral.created", "referral.acknowledged"]
    positions = [found.index(name) for name in order]
    assert positions == sorted(positions)


def test_the_journey_uses_a_fixed_readable_reference_and_is_repeatable(journey):
    first = len(outbox())
    assert careflow_demo.run()["created"] is False
    assert len(outbox()) == first
    assert all(json.loads(row["event"])["workflow_ref"] != "" for row in outbox())
    db = connect()
    assert db.execute("SELECT COUNT(*) FROM careflow_outbox WHERE workflow_ref = ?", (careflow_demo.CASE,)).fetchone()[0] > 8
    db.close()


def test_stored_events_hold_no_clinical_content(journey):
    text = stored_text()
    for forbidden in ("Demo Person", "Penicillin", "Glycomet", "Haemoglobin", "Synthetic answer", "metformin", "@example.com"):
        assert forbidden not in text


def test_real_handoffs_are_marked_and_point_to_their_cause(journey):
    handoffs = [json.loads(row["event"]) for row in outbox() if json.loads(row["event"]).get("target_module")]
    assert {(event["module"], event["target_module"]) for event in handoffs} == {("LabLens", "ChronicCare")}
    assert all(event["causation_id"] for event in handoffs)


def test_the_ai_stub_is_named_so_the_demo_can_never_pass_as_real(journey):
    draft = next(json.loads(row["event"]) for row in outbox() if row["event_type"] == "note_draft.created")
    assert draft["metadata"]["model_provider"] == "demo_stub"


@pytest.mark.parametrize(
    "helper, wanted",
    [
        ("_chronic", {"care_plan.created", "care_plan.approved", "reading.recorded"}),
        ("_discharge", {"discharge.episode_created", "discharge.checklist.updated", "discharge.approved"}),
        ("_maternal", {"maternal.episode_started", "maternal.due_date_confirmed", "maternal.visit_planned"}),
    ],
)
def test_the_other_record_tools_report_their_real_steps(doctor, patient, helper, wanted):
    from backend import demo

    for purpose in demo.CONSENTS:
        grant(doctor, patient["id"], purpose)
    getattr(demo, helper)(doctor, patient["id"])
    assert wanted <= set(event_types())


def test_amr_guard_reports_against_its_organization(person):
    from backend import demo
    from backend.tests.conftest import verified_organization

    admin = person("doctor")
    organization = verified_organization(admin)
    demo._amr(admin, organization)
    assert "amr_analysis.import_completed" in event_types()
    timeline = admin.get("/api/careflow/events").json()
    assert any(event["module"] == "AMR Guard" for event in timeline["events"])


def test_the_prescription_analyzer_reports_without_any_content(doctor, monkeypatch):
    from backend.tests.test_prescription import photo_bytes, read_result

    monkeypatch.setattr(llm, "ask", lambda *args, **options: read_result())
    response = doctor.post("/api/prescription/read", files=[("images", ("rx.jpg", photo_bytes(), "image/jpeg"))], data={"consent": "true"})
    assert response.status_code == 200
    assert {"prescription.processing.started", "prescription.extraction.completed", "prescription.confirmation_required"} <= set(event_types())
    assert "Amoxicillin" not in stored_text()


def test_a_failed_prescription_read_says_so(doctor, monkeypatch):
    from backend.tests.test_prescription import photo_bytes

    def down(*_args, **_options):
        raise llm.AIRateLimited("synthetic")

    monkeypatch.setattr(llm, "ask", down)
    assert doctor.post("/api/prescription/read", files=[("images", ("rx.jpg", photo_bytes(), "image/jpeg"))], data={"consent": "true"}).status_code == 503
    found = event_types()
    assert "prescription.processing.failed" in found and "prescription.extraction.completed" not in found


def test_the_skin_tool_reports_no_finding(doctor):
    from backend.app import app
    from backend.skin import routes as skin
    from backend.skin.classifier import Analysis, Condition
    from backend.tests.test_prescription import photo_bytes

    class Fake:
        def analyze(self, _photo):
            return Analysis(priority="refer", conditions=[Condition("MEL", "Melanoma", "cancer", 0.91)], heatmap=None, concern_score=0.91)

    app.dependency_overrides[skin.classifier_dependency] = lambda: Fake()
    try:
        response = doctor.post("/api/skin/analyze", files={"image": ("a.jpg", photo_bytes(), "image/jpeg")}, data={"image_type": "dermoscopic"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert {"skin_analysis.started", "skin_analysis.completed", "skin_analysis.review_required"} <= set(event_types())
    assert "Melanoma" not in stored_text() and "refer" not in stored_text().replace("referral", "")


# Delivery


def test_delivery_stores_each_event_once_even_when_sent_twice(journey, mongo):
    run_delivery()
    count = len(mongo.events.documents)
    db = connect()
    db.execute("UPDATE careflow_deliveries SET status = 'pending', next_attempt_at = '2000-01-01T00:00:00'")
    db.commit()
    db.close()
    while run_delivery()["delivered"]:
        pass
    assert len(mongo.events.documents) == count
    assert count == len(outbox())


def test_a_projection_is_right_whatever_order_events_arrive_in(journey, mongo):
    rows = outbox()
    for row in reversed(rows):
        mongo.put(json.loads(row["event"]))
    projection = mongo.projections.documents[careflow_demo.CASE]
    last = json.loads(rows[-1]["event"])
    assert projection["event_count"] == len(rows)
    assert projection["latest_activity"] == last["event_type"]
    assert "Activity view only" in projection["projection_note"]
    occurred = [event["occurred_at"] for event in mongo.timeline(careflow_demo.CASE)]
    assert occurred == sorted(occurred)


def test_an_outage_keeps_events_waiting_then_recovers_without_duplicates(journey, mongo):
    sent = {"count": 0}

    def flaky(event):
        sent["count"] += 1
        if sent["count"] <= 2:
            raise ConnectionError("synthetic outage")
        mongo.put(event)

    db = connect()
    assert delivery.deliver_channel(db, "mongodb", flaky)["retrying"] == 1
    assert delivery.backlog(db)["mongodb"]["delivered"] == 0
    row = db.execute("SELECT attempts, last_error, next_attempt_at FROM careflow_deliveries WHERE attempts > 0").fetchone()
    assert row["attempts"] == 1 and row["last_error"] == "ConnectionError"
    assert row["next_attempt_at"] > datetime.now(timezone.utc).isoformat()
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    while delivery.deliver_channel(db, "mongodb", flaky, now=later)["delivered"] or delivery.backlog(db)["mongodb"]["pending"]:
        later += timedelta(hours=2)
    assert len(mongo.events.documents) == len(outbox())
    db.close()


def test_a_backoff_is_bounded():
    assert settings.event_backoff_seconds <= delivery.retry_delay(1) <= settings.event_backoff_seconds * (1 + delivery.JITTER)
    assert delivery.MAX_BACKOFF_SECONDS <= delivery.retry_delay(30) <= delivery.MAX_BACKOFF_SECONDS * (1 + delivery.JITTER)


def test_retries_are_spread_out_by_jitter():
    assert len({delivery.retry_delay(3) for _ in range(20)}) > 1


def test_an_event_that_keeps_failing_is_marked_dead_and_can_be_replayed(doctor, patient, mongo, monkeypatch):
    reconcile(doctor, patient)
    monkeypatch.setattr(settings, "event_max_attempts", 2)
    db = connect()

    def down(_event):
        raise ConnectionError("synthetic outage")

    now = datetime.now(timezone.utc)
    delivery.deliver_channel(db, "mongodb", down, now=now)
    assert delivery.deliver_channel(db, "mongodb", down, now=now + timedelta(hours=1))["dead"] == 1
    assert delivery.backlog(db)["mongodb"]["dead"] == 1
    assert delivery.replay_dead(db, "mongodb") == 1
    db.commit()
    db.close()
    assert run_delivery()["delivered"] >= 1
    assert len(mongo.events.documents) == len(outbox())


def test_a_failed_delivery_never_keeps_the_error_text(doctor, patient, mongo):
    reconcile(doctor, patient)
    db = connect()

    def down(_event):
        raise ConnectionError("mongodb+srv://user:password@cluster.invalid")

    delivery.deliver_channel(db, "mongodb", down)
    stored = db.execute("SELECT last_error FROM careflow_deliveries WHERE last_error IS NOT NULL").fetchone()
    db.close()
    assert stored["last_error"] == "ConnectionError"


def test_one_destination_failing_does_not_hold_up_another(journey, monkeypatch):
    db = connect()
    sent = []

    def down(_event):
        raise ConnectionError("synthetic")

    delivery.deliver_channel(db, "mongodb", down)
    delivery.deliver_channel(db, "yugabyte", sent.append)
    db.close()
    assert sent, "handoffs still go to YugabyteDB while MongoDB is down"


# Reading, and who may read


def test_a_timeline_is_ordered_and_shows_only_safe_fields(journey, client):
    response = client.post("/api/auth/login", json={"email": careflow_demo.DOCTOR, "password": careflow_demo.PASSWORD})
    assert response.status_code == 200
    body = client.get(f"/api/careflow/workflows/{careflow_demo.CASE}/timeline").json()
    assert body["source"] == "local_outbox" and body["synthetic_demo"] is True
    assert set(body["events"][0]) == {"event_id", "workflow_ref", "module", "event_type", "status", "occurred_at", "actor_type", "target_module", "review_required"}
    assert body["label"] == "Demo Person"
    times = [event["occurred_at"] for event in body["events"]]
    assert times == sorted(times)


def test_reading_a_timeline_is_audited(journey, client):
    client.post("/api/auth/login", json={"email": careflow_demo.DOCTOR, "password": careflow_demo.PASSWORD})
    client.get(f"/api/careflow/workflows/{careflow_demo.CASE}/timeline")
    db = connect()
    assert db.execute("SELECT 1 FROM audit_events WHERE action = 'careflow.timeline.read'").fetchone()
    db.close()


def test_filters_work_on_both_stores(journey, client, mongo):
    client.post("/api/auth/login", json={"email": careflow_demo.DOCTOR, "password": careflow_demo.PASSWORD})
    while run_delivery()["delivered"]:
        pass
    remote = client.get("/api/careflow/events?module=LabLens&status=review_required").json()
    assert remote["source"] == "mongodb_atlas"
    assert remote["events"] and all(event["module"] == "LabLens" and event["status"] == "review_required" for event in remote["events"])


def test_counts_come_from_stored_events(journey, client):
    client.post("/api/auth/login", json={"email": careflow_demo.DOCTOR, "password": careflow_demo.PASSWORD})
    counts = client.get("/api/careflow/summary").json()["counts"]
    assert counts["total"] == len(outbox())


def test_someone_else_cannot_read_a_timeline(journey, person):
    stranger = person("doctor")
    assert stranger.get(f"/api/careflow/workflows/{careflow_demo.CASE}/timeline").status_code == 404
    assert stranger.get("/api/careflow/events").json()["events"] == []
    assert stranger.get("/api/careflow/summary").json()["counts"]["total"] == 0
    assert stranger.get("/api/careflow/workflows").json()["workflows"] == []


def test_one_organizations_events_never_reach_another_even_in_atlas(journey, person, client, mongo):
    while run_delivery()["delivered"]:
        pass
    other = person("doctor")
    response = other.get("/api/careflow/events")
    assert response.json()["source"] == "mongodb_atlas"
    assert response.json()["events"] == []


def test_a_patient_sees_their_own_timeline_and_no_one_elses(person, doctor, patient):
    reconcile(doctor, patient)
    code = doctor.post(f"/api/patients/{patient['id']}/share-codes", json={"purpose": "claim"}).json()["code"]
    owner, stranger = person("patient"), person("patient")
    owner.post("/api/patients/join", json={"code": code})
    assert owner.get("/api/careflow/events").json()["events"]
    assert stranger.get("/api/careflow/events").json()["events"] == []


def test_unverified_staff_see_nothing(journey, person, doctor, patient):
    reconcile(doctor, patient)
    code = doctor.post(f"/api/patients/{patient['id']}/share-codes", json={"purpose": "clinician"}).json()["code"]
    pending = person("doctor", verified=False)
    pending.post("/api/patients/join", json={"code": code})
    assert pending.get("/api/careflow/events").json()["events"] == []
    assert pending.get("/api/careflow/health").status_code == 403


def test_revoking_access_stops_the_timeline_at_once(person, doctor, patient):
    reconcile(doctor, patient)
    code = doctor.post(f"/api/patients/{patient['id']}/share-codes", json={"purpose": "clinician"}).json()["code"]
    colleague = person("doctor")
    colleague.post("/api/patients/join", json={"code": code})
    workflow = colleague.get("/api/careflow/workflows").json()["workflows"][0]["workflow_ref"]
    assert colleague.get(f"/api/careflow/workflows/{workflow}/timeline").status_code == 200
    db = connect()
    db.execute("UPDATE care_team SET revoked_at = 'now' WHERE user_id = (SELECT id FROM users WHERE email = ?)", (colleague.email,))
    db.commit()
    db.close()
    assert colleague.get(f"/api/careflow/workflows/{workflow}/timeline").status_code == 404


def test_a_bad_reference_is_refused_before_any_lookup(doctor):
    assert doctor.get("/api/careflow/workflows/not%20a%20reference/timeline").status_code == 404


def test_an_atlas_outage_falls_back_and_says_so(journey, client, mongo):
    client.post("/api/auth/login", json={"email": careflow_demo.DOCTOR, "password": careflow_demo.PASSWORD})
    mongo.database.down = True
    body = client.get(f"/api/careflow/workflows/{careflow_demo.CASE}/timeline").json()
    assert body["source"] == "local_outbox" and body["degraded"] is True
    assert body["events"]


def test_health_holds_no_secrets(person, monkeypatch):
    staff_member = person("doctor")
    monkeypatch.setattr(settings, "mongodb_enabled", True)
    monkeypatch.setattr(settings, "mongodb_uri", "mongodb+srv://user:synthetic-secret@cluster.invalid/")
    monkeypatch.setattr(settings, "yugabyte_enabled", True)
    monkeypatch.setattr(settings, "yugabyte_dsn", "")
    monkeypatch.setattr(settings, "meko_enabled", False)

    def fails():
        raise ConnectionError("mongodb+srv://user:synthetic-secret@cluster.invalid/")

    monkeypatch.setattr(stores, "mongo_store", fails)
    response = staff_member.get("/api/careflow/health")
    assert "synthetic-secret" not in response.text
    states = {item["name"]: item["state"] for item in response.json()["integrations"]}
    assert states == {"MONGODB_ATLAS": "FAILED", "YUGABYTEDB": "BLOCKED", "MEKO": "DISABLED"}


def test_nothing_needs_an_integration_to_start(client):
    assert client.get("/care.html").status_code == 200
    assert delivery.deliver_once() == {}


def test_a_new_tool_screen_writes_no_html_and_holds_no_secrets(client):
    source = client.get("/js/care/careflow.js").text
    assert "innerHTML" not in source
    for secret in ("MONGODB_ATLAS_URI", "MEKO_API_KEY", "YUGABYTEDB_YSQL_DSN", "mongodb+srv"):
        assert secret not in source


# Agent runs


class Reply:
    @staticmethod
    def ok(content='{"text": "hello"}'):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


def test_a_fallback_shows_up_as_two_attempts_with_no_content(monkeypatch):
    from pydantic import BaseModel

    class Answer(BaseModel):
        text: str

    monkeypatch.setattr(settings, "groq_api_key", "groq-test-key")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test-key")
    replies = iter([httpx.Response(503), Reply.ok()])
    monkeypatch.setattr(httpx, "post", lambda *args, **options: next(replies))
    llm.ask("Be careful.", "SYNTHETIC-SECRET-PROMPT", Answer, tool="clinscribe_note")
    runs = execution.pending_sync(10)
    assert len(runs) == 1 and runs[0]["status"] == "succeeded"
    assert [(item["status"], item["error_category"]) for item in runs[0]["attempts"]] == [("failed", "AIUnavailable"), ("succeeded", None)]
    assert runs[0]["provider"] == "groq" and runs[0]["fallback_provider"] == "gemini" and runs[0]["fallback_used"] == 1
    assert runs[0]["duration_ms"] is not None and runs[0]["module"] == "ClinScribe"
    assert "SYNTHETIC-SECRET-PROMPT" not in str(runs)


def test_a_run_is_on_disk_before_the_ai_answers(monkeypatch):
    from pydantic import BaseModel

    class Answer(BaseModel):
        text: str

    monkeypatch.setattr(settings, "groq_api_key", "groq-test-key")
    seen = {}

    def answer(*_args, **_options):
        db = execution.connect()
        seen["status"] = db.execute("SELECT status FROM agent_runs").fetchone()["status"]
        db.close()
        return Reply.ok()

    monkeypatch.setattr(httpx, "post", answer)
    llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")
    assert seen["status"] == "running"


def test_a_run_is_recorded_while_the_request_holds_the_write_lock(doctor, patient, monkeypatch):
    """The reason these records have their own file: a draft holds the main write lock during the AI call."""
    from backend.clinscribe import drafting

    grant(doctor, patient["id"], "ai_scribe")
    encounter = doctor.post(f"/api/patients/{patient['id']}/encounters", json={}).json()
    session = doctor.post(f"/api/addons/clinscribe/encounters/{encounter['id']}/sessions", json={}).json()
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/segments", json={"text": "Synthetic line"})
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/state", json={"state": "stopped"})

    def down(*_args, **_options):
        raise httpx.ConnectError("synthetic")

    monkeypatch.setattr(settings, "groq_api_key", "groq-test-key")
    monkeypatch.setattr(httpx, "post", down)
    assert doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/draft-note", json={}).status_code == 503
    runs = execution.pending_sync(10)
    assert [run["status"] for run in runs] == ["failed"]
    assert runs[0]["workflow_ref"].startswith("case_")
    assert drafting  # the route under test


def test_a_crash_leaves_a_run_that_is_later_marked_interrupted():
    db = execution.connect()
    db.execute(
        "INSERT INTO agent_runs (run_id, module, tool, status, started_at, boot_id, updated_at) VALUES ('run_old', 'LabLens', 'lablens_read', 'running', '2026-01-01', 'an-earlier-process', '2026-01-01')"
    )
    db.commit()
    db.close()
    assert execution.recover_interrupted() == 1
    runs = execution.pending_sync(10)
    assert runs[0]["status"] == "interrupted" and runs[0]["finished_at"]
    assert execution.summary()["interrupted_runs"] == 1


def test_a_run_from_this_process_is_left_alone():
    run = execution.AgentRun("lablens_read")
    assert execution.recover_interrupted() == 0
    run.finish("failed")


def test_run_records_survive_a_restart_because_they_are_on_disk():
    run = execution.AgentRun("lablens_read")
    run.tried("gemini", "gemini-3.5-flash", "succeeded")
    run.finish("succeeded")
    assert execution.path().exists()
    assert execution.summary()["total_runs"] == 1


def test_a_record_that_cannot_be_written_never_stops_the_ai(monkeypatch):
    def locked():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(execution, "connect", locked)
    run = execution.AgentRun("lablens_read")
    run.tried("gemini", "gemini-3.5-flash", "succeeded")
    run.finish("succeeded")
    assert execution.pending_sync(10) == []


def test_a_failed_send_keeps_runs_for_next_time(monkeypatch):
    run = execution.AgentRun("clinscribe_note")
    run.finish("succeeded")

    class Down:
        def save_runs(self, _runs):
            raise ConnectionError("synthetic")

    monkeypatch.setattr(yugabyte, "store", lambda: Down())
    with pytest.raises(ConnectionError):
        delivery._send_runs()
    assert execution.waiting() == 1


def test_sent_runs_are_not_sent_again_until_they_change(monkeypatch):
    run = execution.AgentRun("clinscribe_note")
    sent = []

    class Store:
        def save_runs(self, runs):
            sent.extend(item["status"] for item in runs)

    monkeypatch.setattr(yugabyte, "store", lambda: Store())
    delivery._send_runs()
    delivery._send_runs()
    assert sent == ["running"]
    run.finish("succeeded")
    delivery._send_runs()
    assert sent == ["running", "succeeded"]
    assert execution.waiting() == 0


def test_the_migration_is_split_into_statements_without_comments():
    statements = yugabyte._statements(next(yugabyte.MIGRATIONS.glob("*.sql")).read_text())
    assert len(statements) == 6
    assert all("--" not in statement for statement in statements)


def test_ysql_creates_no_clinical_or_identity_tables():
    script = next(yugabyte.MIGRATIONS.glob("*.sql")).read_text().lower()
    for word in ("patient", "consent", "users", "first_name", "email", "dob"):
        assert word not in script.replace("-- no clinical records, identities, consent or event history belong here.", "")


def test_a_handoff_carries_only_fixed_text_and_references(journey):
    handoff = next(delivery.handoff_envelope(json.loads(row["event"])) for row in outbox() if json.loads(row["event"]).get("target_module"))
    assert handoff["human_review_required"] is True
    assert handoff["source_module"] == "LabLens" and handoff["target_module"] == "ChronicCare"
    assert handoff["source_event_ids"][0].startswith("evt_")
    assert set(handoff) == {"schema_version", "handoff_id", "correlation_id", "source_module", "target_module", "workflow_ref", "created_at", "purpose", "summary", "artifact_refs", "source_event_ids", "human_review_required"}


# Meko, against a stand-in for its documented MCP server


class FakeMcp:
    TOOLS = [
        {"name": "knowledgebase_search", "inputSchema": {"properties": {"query": {}, "conversation_id": {}, "datapack_id": {}, "agent_id": {}, "limit": {}}}},
        {"name": "memory_add", "inputSchema": {"properties": {"text": {}, "conversation_id": {}, "agent_id": {}, "run_id": {}, "metadata": {}, "messages": {}, "datapack_id": {}}}},
    ]

    def __init__(self, failure=None, event_stream=False):
        self.calls = []
        self.failure = failure
        self.event_stream = event_stream

    def __call__(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json})
        if "id" not in json:
            return httpx.Response(202)
        if self.failure:
            if isinstance(self.failure, Exception):
                raise self.failure
            return httpx.Response(self.failure)
        method = json["method"]
        if method == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": json["id"], "result": {"protocolVersion": "2025-06-18"}}, headers={"mcp-session-id": "synthetic-session"})
        if method == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": json["id"], "result": {"tools": self.TOOLS}})
        result = {"content": [{"type": "text", "text": '{"results": [{"text": "Synthetic guidance"}]}'}]}
        body = {"jsonrpc": "2.0", "id": json["id"], "result": result}
        if self.event_stream:
            import json as codec

            return httpx.Response(200, text=f"event: message\ndata: {codec.dumps(body)}\n\n", headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=body)


def client_for(server):
    return meko.MekoClient("https://mcp.example.invalid/mcp", "synthetic-key", "datapack-synthetic", post=server)


def test_meko_uses_the_bearer_key_and_the_configured_datapack():
    server = FakeMcp()
    found = client_for(server).knowledge("LabLens: review")
    assert found.available is True and found.items == [{"text": "Synthetic guidance"}]
    call = server.calls[-1]
    assert call["headers"]["Authorization"] == "Bearer synthetic-key"
    assert call["headers"]["Mcp-Session-Id"] == "synthetic-session"
    assert call["body"]["params"]["name"] == "knowledgebase_search"
    assert call["body"]["params"]["arguments"]["datapack_id"] == "datapack-synthetic"


def test_meko_reads_a_streamed_reply_too():
    assert client_for(FakeMcp(event_stream=True)).knowledge("x y").items == [{"text": "Synthetic guidance"}]


def test_meko_refuses_a_tool_the_server_does_not_offer():
    with pytest.raises(meko.MekoUnsupported):
        client_for(FakeMcp()).call("made_up_tool")


def test_meko_refuses_a_retired_argument_before_sending():
    server = FakeMcp()
    client = client_for(server)
    with pytest.raises(meko.MekoUnsupported):
        client.call("knowledgebase_search", query="x", scope="read")
    assert all(call["body"]["method"] != "tools/call" for call in server.calls)


@pytest.mark.parametrize("failure", [401, 403, 500, 429, httpx.ConnectTimeout("slow")])
def test_a_meko_failure_is_unavailable_never_an_empty_answer(failure):
    found = client_for(FakeMcp(failure=failure)).knowledge("x y")
    assert found.available is False and found.items == [] and found.reason


def test_meko_retries_are_bounded():
    server = FakeMcp(failure=httpx.ConnectTimeout("slow"))
    client_for(server).knowledge("x y")
    assert len(server.calls) == meko.MAX_ATTEMPTS


def test_a_bad_meko_key_is_not_retried():
    server = FakeMcp(failure=401)
    client_for(server).knowledge("x y")
    assert len(server.calls) == 1


def test_a_handoff_saved_to_meko_holds_no_clinical_content(journey):
    server = FakeMcp()
    row = next(row for row in outbox() if json.loads(row["event"]).get("target_module"))
    client_for(server).save_handoff(delivery.handoff_envelope(json.loads(row["event"])))
    arguments = server.calls[-1]["body"]["params"]["arguments"]
    assert server.calls[-1]["body"]["params"]["name"] == "memory_add"
    assert "LabLens to ChronicCare" in arguments["text"]
    for forbidden in ("Demo Person", "Haemoglobin", "HbA1c"):
        assert forbidden not in json.dumps(arguments)


def test_a_meko_outage_leaves_the_handoff_queued(journey, monkeypatch):
    class Down:
        def save_handoff(self, _handoff):
            raise meko.MekoUnavailable("synthetic")

    monkeypatch.setattr(meko, "client", lambda: Down())
    outcome = delivery.deliver_once()
    assert outcome["meko"]["retrying"] == 1
    db = connect()
    assert delivery.backlog(db)["meko"]["pending"] >= 1
    db.close()


def test_the_context_route_never_lets_a_request_choose_a_datapack(person, monkeypatch):
    server = FakeMcp()
    staff_member = person("doctor")
    monkeypatch.setattr(meko, "client", lambda: client_for(server))
    response = staff_member.get("/api/careflow/context?module=LabLens&topic=review&datapack_id=someone-elses")
    assert response.json()["available"] is True
    assert server.calls[-1]["body"]["params"]["arguments"]["datapack_id"] == "datapack-synthetic"


def test_the_context_route_is_for_staff_and_says_when_disconnected(person):
    assert person("patient").get("/api/careflow/context?module=LabLens&topic=review").status_code == 403
    body = person("doctor").get("/api/careflow/context?module=LabLens&topic=review").json()
    assert body["available"] is False and body["items"] == []




# Live updates


class FakeStream:
    def __init__(self, changes, stop):
        self.changes = list(changes)
        self.stop = stop
        self.resume_token = {"_data": "synthetic-token"}
        self.alive = True

    def __enter__(self):
        return self

    def __exit__(self, *_details):
        return False

    def try_next(self):
        if not self.changes:
            self.stop.set()
            return None
        change = self.changes.pop(0)
        if isinstance(change, Exception):
            raise change
        return change


class FakeEvents:
    def __init__(self, batches, stop):
        self.batches = batches
        self.stop = stop
        self.resumed_from = []

    def __getitem__(self, _name):
        return self

    def watch(self, _pipeline, resume_after=None, max_await_time_ms=None):
        self.resumed_from.append(resume_after)
        return FakeStream(self.batches.pop(0), self.stop)


def watch_with(batches):
    import threading

    stop = threading.Event()
    events_seen = FakeEvents(batches, stop)
    store = type("Store", (), {"database": events_seen, "events": events_seen})()
    errors = []

    def note(error):
        errors.append(error)
        stop.clear()

    live.watch(stop, store, on_error=note)
    return store.events, errors


def change(reference="case_abc123", event_id=None):
    return {"fullDocument": {"_id": event_id or f"evt_{time.monotonic_ns()}", "workflow_ref": reference}}


def test_a_change_reaches_the_feed(client):
    before = live.feed.version
    watch_with([[change()]])
    version, changed = live.feed.since(before)
    assert version == before + 1 and changed == {"case_abc123"}


def test_a_lost_stream_resumes_from_its_last_token(client, monkeypatch):
    monkeypatch.setattr(live, "FIRST_WAIT_SECONDS", 0)
    events_seen, errors = watch_with([[change(), ConnectionError("synthetic")], []])
    assert len(errors) == 1
    assert events_seen.resumed_from == [None, {"_data": "synthetic-token"}]


def test_a_stale_token_starts_afresh(client, monkeypatch):
    class Stale(Exception):
        code = 286

    monkeypatch.setattr(live, "FIRST_WAIT_SECONDS", 0)
    events_seen, _errors = watch_with([[change(), Stale()], []])
    assert events_seen.resumed_from == [None, None]


async def collect(allowed, source, checks):
    return [item async for item in live.updates(allowed, source, checks=checks)]


def test_a_screen_only_hears_about_workflows_it_may_see():
    import asyncio

    source = live.Feed()

    async def run():
        collector = asyncio.create_task(collect({"case_mine"}, source, 2))
        await asyncio.sleep(0)
        source.publish("case_someone_else")
        return await collector

    assert asyncio.run(run()) == []


def test_the_message_never_names_a_workflow():
    import asyncio

    source = live.Feed()

    async def run():
        collector = asyncio.create_task(collect({"case_mine"}, source, 2))
        await asyncio.sleep(0)
        source.publish("case_mine")
        return await collector

    assert asyncio.run(run()) == ['data: {"changed": true}\n\n']


def test_the_stream_is_off_unless_asked_for(doctor):
    assert doctor.get("/api/careflow/stream").status_code == 404


def test_nothing_is_watched_unless_switched_on():
    assert live.start() is None
