"""Status, coverage, knowledge and handoffs. Mocks only, so none of this proves a real connection.

Real services are in backend/tests/live/, and local stand-ins in backend/tests/local_stack/.
"""
import asyncio
import inspect
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.routing import APIRoute

from backend import settings_store
from backend.app import app
from backend.careflow import contract, delivery, demo as careflow_demo, live, stores
from backend.config import Settings, settings
from backend.db import connect
from backend.integrations import execution, knowledge, meko, status, yugabyte
from backend.tests.conftest import grant, verified_organization
from backend.tests.test_careflow import FakeMcp, add_handoff, client_for, event_types, mongo, outbox, record_event  # noqa: F401

TOOL_PACKAGES = ("diagnofast", "skin", "prescription", "clinscribe", "lablens", "dischargecare", "referralloop")
EXPLICIT = {"start_interview", "answer", "read", "explain", "analyze", "create_intake", "draft_note", "approve_note", "confirm_report"}


def write_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.endpoint.__module__.split(".")[1] in TOOL_PACKAGES and route.methods - {"GET", "HEAD", "OPTIONS"}:
            yield route


def actions_in(source):
    names = re.findall(r'"((?:clinscribe|lablens|dischargecare|referralloop|diagnofast)\.[a-z_.]+)"', source)
    return [name for name in names if name in contract.AUDIT_EVENTS]


# Every tool's real operations


def test_every_write_route_records_an_event_or_says_why_not():
    uncovered = []
    for route in write_routes():
        name = route.endpoint.__name__
        source = inspect.getsource(route.endpoint)
        status_based = "_change(" in source or "referralloop.status" in source or "clinscribe.session.{" in source
        if not (actions_in(source) or name in EXPLICIT or name in contract.UNTRACKED_ROUTES or status_based):
            uncovered.append(route.path)
    assert uncovered == []


def test_every_status_a_referral_or_session_can_take_is_mapped():
    from typing import get_args

    from backend.clinscribe import routes as clinscribe
    from backend.referralloop import routes as referrals

    for value in get_args(referrals.Status):
        assert f"referralloop.status.{value}" in contract.AUDIT_EVENTS
    for value in get_args(clinscribe.StateRequest.model_fields["state"].annotation):
        assert f"clinscribe.session.{value}" in contract.AUDIT_EVENTS


def test_all_seven_tools_are_in_the_contract():
    assert len(contract.MODULES) == 7
    assert set(contract.WORKFLOW_STAGES) == contract.MODULES


def test_an_untracked_route_gives_a_real_reason():
    assert all(len(reason) > 15 for reason in contract.UNTRACKED_ROUTES.values())


REQUIRED = {"event_id", "schema_version", "module", "event_type", "occurred_at", "workflow_ref", "status", "metadata", "source_operation", "workflow_stage", "source_system"}


@pytest.fixture
def wide_journey(client):
    careflow_demo.run()


def test_each_event_has_every_required_field(wide_journey):
    for row in outbox():
        event = json.loads(row["event"])
        assert REQUIRED <= set(event)
        assert event["schema_version"] == contract.SCHEMA_VERSION
        assert event["source_system"] == "diagnosia"


def test_events_from_audit_name_the_real_operation(wide_journey):
    found = {json.loads(row["event"])["event_type"]: json.loads(row["event"])["source_operation"] for row in outbox()}
    assert found["lab.report_confirmed"] == "lablens.report.confirmed"
    assert found["discharge.episode_created"] == "dischargecare.episode.create"


def test_a_transcription_failure_is_recorded_but_a_good_chunk_is_not(doctor, patient, monkeypatch):
    from backend import speech

    grant(doctor, patient["id"], "ai_scribe")
    encounter = doctor.post(f"/api/patients/{patient['id']}/encounters", json={}).json()
    session = doctor.post(f"/api/addons/clinscribe/encounters/{encounter['id']}/sessions", json={}).json()
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/state", json={"state": "recording"})
    monkeypatch.setattr(speech, "transcribe", lambda *args: "Synthetic line")
    files = {"audio": ("a.webm", b"synthetic", "audio/webm")}
    assert doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/transcribe", files=files).status_code == 201
    assert "note_session.transcription_failed" not in event_types()

    def down(*_args):
        raise speech.SpeechUnavailable("synthetic")

    monkeypatch.setattr(speech, "transcribe", down)
    assert doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/transcribe", files=files).status_code == 503
    assert "note_session.transcription_failed" in event_types()


# Settings


@pytest.mark.parametrize("value, expected", [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("0", False), ("false", False), ("", False)])
def test_a_switch_reads_both_ways_of_writing_it(monkeypatch, value, expected):
    monkeypatch.setenv("ENABLE_MEKO_INTEGRATION", value)
    assert Settings().meko_enabled is expected


def test_the_database_name_has_a_safe_default_and_can_be_set(monkeypatch):
    monkeypatch.delenv("MONGODB_DATABASE_NAME", raising=False)
    monkeypatch.delenv("MONGODB_DATABASE", raising=False)
    assert Settings().mongodb_database == "diagnosia_atlas"
    monkeypatch.setenv("MONGODB_DATABASE_NAME", "diagnosia_other")
    assert Settings().mongodb_database == "diagnosia_other"


@pytest.mark.parametrize("name, attribute", [("MONGODB_ATLAS_URI", "mongodb_uri"), ("YUGABYTEDB_YSQL_DSN", "yugabyte_dsn"), ("MEKO_API_KEY", "meko_api_key")])
def test_a_placeholder_never_counts_as_a_credential(monkeypatch, name, attribute):
    monkeypatch.setenv(name, "your-value-here")
    assert getattr(Settings(), attribute) == ""


def test_there_is_no_default_address_to_fall_back_to(monkeypatch):
    monkeypatch.delenv("MONGODB_ATLAS_URI", raising=False)
    monkeypatch.delenv("YUGABYTEDB_YSQL_DSN", raising=False)
    fresh = Settings()
    assert fresh.mongodb_uri == "" and fresh.yugabyte_dsn == ""


# What the status says


def enable(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)


def test_a_service_switched_on_in_a_developers_env_is_off_during_tests():
    assert not (settings.mongodb_enabled or settings.yugabyte_enabled or settings.meko_enabled)


def test_a_switched_off_service_says_disabled(monkeypatch):
    enable(monkeypatch, mongodb_enabled=False, yugabyte_enabled=False, meko_enabled=False)
    assert {item.state for item in status.overview()} == {"DISABLED"}


def test_a_switch_without_credentials_is_blocked_by_name(monkeypatch):
    enable(monkeypatch, mongodb_enabled=True, mongodb_uri="", yugabyte_enabled=True, yugabyte_dsn="", meko_enabled=True, meko_api_key="", meko_datapack_id="")
    found = {item.name: item for item in status.overview()}
    assert all(item.state == "BLOCKED" and item.detail.startswith("BLOCKED_MISSING_CREDENTIALS") for item in found.values())
    assert "MONGODB_ATLAS_URI" in found["MONGODB_ATLAS"].detail
    assert "YUGABYTEDB_YSQL_DSN" in found["YUGABYTEDB"].detail
    assert "MEKO_API_KEY" in found["MEKO"].detail and "MEKO_DATAPACK_ID" in found["MEKO"].detail


@pytest.mark.parametrize(
    "hosts, expected",
    [
        (["cluster0-shard-00-00.abcde.mongodb.net"], "mongodb_atlas"),
        (["a.abcde.mongodb.net", "b.abcde.mongodb.net"], "mongodb_atlas"),
        (["localhost"], "mongodb_local"),
        (["127.0.0.1"], "mongodb_local"),
        (["a.abcde.mongodb.net", "127.0.0.1"], "mongodb_local"),
        (["mongodb.net.attacker.example"], "mongodb_local"),
        ([], "mongodb_local"),
    ],
)
def test_atlas_is_decided_by_the_servers_connected_to(hosts, expected):
    assert stores.deployment_name(hosts) == expected


class FakeStore:
    def __init__(self, name):
        self.name = name

    def ping(self):
        pass


def test_a_local_mongodb_is_never_reported_as_atlas(monkeypatch):
    enable(monkeypatch, mongodb_enabled=True, mongodb_uri="mongodb://127.0.0.1:27017/")
    monkeypatch.setattr(stores, "mongo_store", lambda: FakeStore("mongodb_local"))
    found = status.mongodb()
    assert found.state == "BLOCKED" and "not Atlas" in found.detail


def test_atlas_is_only_connected_after_it_answers(monkeypatch):
    enable(monkeypatch, mongodb_enabled=True, mongodb_uri="mongodb+srv://synthetic.mongodb.net/")
    monkeypatch.setattr(stores, "mongo_store", lambda: FakeStore("mongodb_atlas"))
    assert status.mongodb().state == "CONNECTED"

    def down():
        raise ConnectionError("mongodb+srv://user:synthetic-secret@cluster.invalid")

    monkeypatch.setattr(stores, "mongo_store", down)
    found = status.mongodb()
    assert found.state == "FAILED" and "synthetic-secret" not in found.detail


class FakeConnection:
    def __init__(self, version):
        self.version = version

    def __enter__(self):
        return self

    def __exit__(self, *_details):
        return False

    def execute(self, statement, _parameters=None):
        version = self.version

        class Result:
            def fetchone(self):
                return (version,) if "version()" in statement else (1,)

        return Result()


class FakePool:
    def __init__(self, version):
        self.version = version

    def connection(self):
        return FakeConnection(self.version)


def test_postgresql_is_never_reported_as_yugabytedb(monkeypatch):
    enable(monkeypatch, yugabyte_enabled=True, yugabyte_dsn="postgresql://synthetic")
    monkeypatch.setattr(yugabyte, "store", lambda: yugabyte.YugabyteStore(FakePool("PostgreSQL 16.4 on aarch64-unknown-linux-gnu")))
    found = status.yugabytedb()
    assert found.state == "FAILED" and "not YugabyteDB" in found.detail


def test_yugabytedb_is_recognised_by_what_the_server_says(monkeypatch):
    enable(monkeypatch, yugabyte_enabled=True, yugabyte_dsn="postgresql://synthetic")
    monkeypatch.setattr(yugabyte, "store", lambda: yugabyte.YugabyteStore(FakePool("PostgreSQL 15.12-YB-2026.1.2.0-b0 on aarch64")))
    found = status.yugabytedb()
    assert found.state == "CONNECTED" and "2026.1.2.0" in found.detail


def test_a_wrong_meko_key_is_failed_not_connected(monkeypatch):
    enable(monkeypatch, meko_enabled=True, meko_api_key="synthetic", meko_datapack_id="datapack-synthetic")
    monkeypatch.setattr(meko, "client", lambda: client_for(FakeMcp(failure=401)))
    found = status.mekoservice()
    assert found.state == "FAILED" and found.detail == "MekoRejected"


def test_the_status_command_names_each_service(capsys, monkeypatch, client):
    from backend import operator_cli

    enable(monkeypatch, mongodb_enabled=False, yugabyte_enabled=False, meko_enabled=False)
    assert operator_cli.integrations_status(None) == 0
    printed = capsys.readouterr().out
    assert "MONGODB_ATLAS: DISABLED" in printed and "YUGABYTEDB: DISABLED" in printed and "MEKO: DISABLED" in printed


# The projection


def event(kind, status_value="completed", module="LabLens", minute=0):
    return {
        "event_id": f"evt_{kind}_{minute}", "event_type": kind, "status": status_value, "module": module, "tenant_ref": "tenant_abc",
        "occurred_at": f"2026-10-31T10:{minute:02d}:00+00:00", "target_module": None,
    }


def test_waiting_for_review_counts_only_what_is_unsettled():
    events = [event("lab.review_required", "review_required", minute=1), event("lab.review_required", "review_required", minute=2), event("lab.report_confirmed", minute=3)]
    assert stores.waiting_for_review(events) == 1


def test_a_settling_event_with_nothing_waiting_counts_for_nothing():
    assert stores.waiting_for_review([event("lab.report_confirmed")]) == 0


def test_the_projection_says_it_is_only_a_view():
    found = stores.project("case_abc123", [event("lab.review_required", "review_required")])
    assert "Activity view only" in found["projection_note"]
    assert found["activity_status"] == "waiting_for_review" and found["pending_review_count"] == 1
    assert found["modules"] == ["LabLens"] and found["events"][0]["event_type"] == "lab.review_required"
    assert found["projected_at"].tzinfo is not None


def test_a_failed_last_event_shows_as_failed():
    assert stores.project("case_abc123", [event("lab.extraction.failed", "failed")])["activity_status"] == "failed"


# Delivery


class Validation(Exception):
    code = 121


def waiting_events(doctor, patient):
    record_event(doctor, patient)
    record_event(doctor, patient)


def test_a_document_the_schema_refuses_is_set_aside_without_blocking_the_rest(doctor, patient):
    waiting_events(doctor, patient)
    db = connect()
    sent = []
    calls = {"count": 0}

    def send(item):
        calls["count"] += 1
        if calls["count"] == 1:
            raise Validation("synthetic")
        sent.append(item)

    outcome = delivery.deliver_channel(db, "mongodb", send)
    assert outcome["dead"] == 1 and outcome["delivered"] == len(sent) and sent
    db.close()


def test_nothing_is_marked_delivered_until_the_destination_confirms(doctor, patient):
    waiting_events(doctor, patient)
    db = connect()

    def refuse(_event):
        raise ConnectionError("synthetic")

    delivery.deliver_channel(db, "mongodb", refuse)
    assert delivery.backlog(db)["mongodb"]["delivered"] == 0
    db.close()


def test_delivery_state_reaches_mongodb_as_a_view(doctor, patient, mongo):  # noqa: F811
    waiting_events(doctor, patient)
    db = connect()
    while delivery.deliver_channel(db, "mongodb", lambda item: mongo.put(item) and None)["delivered"]:
        pass
    delivery._mirror_states(db)
    db.close()
    stored = list(mongo.deliveries.documents.values())
    assert stored and all(item["state"] == "delivered" and item["destination"] == "mongodb" for item in stored)
    assert all(":mongodb" in key for key in mongo.deliveries.documents)


def test_a_restart_loses_nothing_because_the_outbox_is_on_disk(doctor, patient):
    waiting_events(doctor, patient)
    db = connect()
    pending = delivery.backlog(db)["mongodb"]["pending"]
    db.close()
    fresh = connect()
    assert pending > 0 and delivery.backlog(fresh)["mongodb"]["pending"] == pending
    fresh.close()


# Change streams


def test_an_event_seen_twice_is_announced_once():
    feed = live.Feed()
    assert feed.publish("case_abc123", "evt_one") is True
    assert feed.publish("case_abc123", "evt_one") is False
    assert feed.version == 1


def test_a_stream_position_survives_a_restart(client):
    live.save_token({"_data": "synthetic-token"})
    assert live.load_token() == {"_data": "synthetic-token"}
    live.save_token(None)
    assert live.load_token() is None


def test_a_saved_position_is_used_on_the_next_start(client, monkeypatch):
    from backend.tests.test_careflow import watch_with

    live.save_token({"_data": "saved-before-restart"})
    events_seen, _errors = watch_with([[]])
    assert events_seen.resumed_from == [{"_data": "saved-before-restart"}]


def test_a_stale_position_is_forgotten(client, monkeypatch):
    from backend.tests.test_careflow import change, watch_with

    class Stale(Exception):
        code = 260

    monkeypatch.setattr(live, "FIRST_WAIT_SECONDS", 0)
    live.save_token({"_data": "too-old"})
    events_seen, _errors = watch_with([[Stale()], [change()]])
    assert events_seen.resumed_from[0] == {"_data": "too-old"}
    assert events_seen.resumed_from[1] is None


def test_a_position_the_server_refuses_is_forgotten_whatever_its_reason(client, monkeypatch):
    from backend.tests.test_careflow import watch_with

    class Refused(Exception):
        code = 50811  # what a real server said about a corrupt token

    class OpensBadly:
        def __init__(self, inner):
            self.inner = inner
            self.first = True

        def __getitem__(self, _name):
            return self

        def watch(self, *args, **options):
            if self.first:
                self.first = False
                raise Refused()
            return self.inner.watch(*args, **options)

    monkeypatch.setattr(live, "FIRST_WAIT_SECONDS", 0)
    live.save_token({"_data": "corrupt"})
    stop = threading.Event()
    from backend.tests.test_careflow import FakeEvents

    inner = FakeEvents([[]], stop)
    store = type("Store", (), {"database": OpensBadly(inner)})()
    live.watch(stop, store)
    assert inner.resumed_from == [None] and live.load_token() is None


def test_a_network_failure_keeps_the_position(client, monkeypatch):
    from backend.tests.test_careflow import FakeEvents

    monkeypatch.setattr(live, "FIRST_WAIT_SECONDS", 0)
    live.save_token({"_data": "still-good"})
    stop = threading.Event()
    calls = []

    class Drops:
        def __getitem__(self, _name):
            return self

        def watch(self, *_args, resume_after=None, **_options):
            calls.append(resume_after)
            if len(calls) == 1:
                raise ConnectionError("synthetic")
            stop.set()
            return FakeEvents([[]], stop).watch(None, resume_after)

    live.watch(stop, type("Store", (), {"database": Drops()})())
    assert calls == [{"_data": "still-good"}, {"_data": "still-good"}]


def test_waits_grow_after_repeated_failures_and_stop_at_a_limit(client, monkeypatch):
    waits = []
    stop = threading.Event()

    class Down:
        def __getitem__(self, _name):
            return self

        def watch(self, *_args, **_options):
            raise ConnectionError("synthetic")

    store = type("Store", (), {"database": Down()})()
    monkeypatch.setattr(stop, "wait", lambda seconds: waits.append(seconds) or (len(waits) >= 9 and stop.set()))
    live.watch(stop, store)
    assert waits[1] > waits[0] and max(waits) <= live.LONGEST_WAIT_SECONDS * 1.2


def test_the_watcher_stops_cleanly(client):
    stop = threading.Event()

    class Quiet:
        alive = True
        resume_token = None

        def __enter__(self):
            return self

        def __exit__(self, *_details):
            return False

        def try_next(self):
            return None

    class Source:
        def __getitem__(self, _name):
            return self

        def watch(self, *_args, **_options):
            return Quiet()

    store = type("Store", (), {"database": Source()})()
    thread = threading.Thread(target=live.watch, args=(stop, store), daemon=True)
    thread.start()
    watcher = live.Watcher(thread, stop)
    watcher.stop()
    assert not thread.is_alive()


def test_screens_are_told_only_that_something_changed():
    source = live.Feed()

    async def run():
        collector = asyncio.ensure_future(_collect({"case_mine"}, source))
        await asyncio.sleep(0)
        source.publish("case_mine", "evt_one")
        return await collector

    assert asyncio.run(run()) == ['data: {"changed": true}\n\n']


async def _collect(allowed, source):
    return [item async for item in live.updates(allowed, source, checks=2)]


# Meko: the MCP contract


SCHEMA_TOOLS = [
    {"name": "knowledgebase_search", "inputSchema": {"properties": {"query": {"type": "string"}, "limit": {"type": "integer"}, "agent_id": {"type": "string"}, "datapack_id": {"type": "string"}}, "required": ["query"]}},
    {"name": "memory_add", "inputSchema": {"properties": {"text": {"type": "string"}, "agent_id": {}, "run_id": {}, "metadata": {"type": "object"}, "datapack_id": {}}}},
    {"name": "datapack_describe", "inputSchema": {"properties": {"datapack_id": {}, "include_status": {"type": "boolean"}}}},
    {"name": "knowledgebase_upload_document", "inputSchema": {"properties": {"filename": {}, "size": {"type": "integer"}, "content_hash": {}, "content_head_base64": {}, "datapack_id": {}}}},
    {"name": "knowledgebase_document_status", "inputSchema": {"properties": {"document_key": {}, "datapack_id": {}}}},
]


class Server(FakeMcp):
    TOOLS = SCHEMA_TOOLS

    def __init__(self, replies=None, **options):
        super().__init__(**options)
        self.replies = replies or {}

    def __call__(self, url, headers, json, timeout):
        name = json.get("params", {}).get("name") if isinstance(json.get("params"), dict) else None
        if name in self.replies:
            self.calls.append({"url": url, "headers": headers, "body": json})
            reply = self.replies[name]
            reply = reply() if callable(reply) else reply
            text = reply if isinstance(reply, str) else __import__("json").dumps(reply)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": json["id"], "result": {"content": [{"type": "text", "text": text}]}})
        return super().__call__(url, headers, json, timeout)


def test_meko_identifies_itself_as_the_docs_ask():
    server = Server()
    client_for(server).knowledge("a query")
    assert server.calls[0]["headers"]["User-Agent"].startswith("diagnosia")


def test_meko_says_it_is_ready_before_listing_tools():
    server = Server()
    client_for(server).tools()
    assert [call["body"]["method"] for call in server.calls] == ["initialize", "notifications/initialized", "tools/list"]


def test_a_missing_required_argument_is_refused_before_sending():
    server = Server()
    client = client_for(server)
    with pytest.raises(meko.MekoUnsupported):
        client.call("knowledgebase_search", limit=3)
    assert all(call["body"]["method"] != "tools/call" for call in server.calls)


def test_a_wrong_type_is_refused_before_sending():
    with pytest.raises(meko.MekoUnsupported):
        client_for(Server()).call("knowledgebase_search", query="x", limit="five")


def test_a_tool_that_disappears_is_a_clear_failure_not_a_guess():
    class Changed(Server):
        TOOLS = [tool for tool in SCHEMA_TOOLS if tool["name"] != "memory_add"]

    with pytest.raises(meko.MekoUnsupported):
        client_for(Changed()).remember("text", {})


def test_an_expired_session_starts_again():
    server = Server()
    client = client_for(server)
    client.tools()
    original = server.__call__

    def expired(url, headers, json, timeout):
        return httpx.Response(404)

    client._post = expired
    assert client.knowledge("x y").available is False
    client._post = original
    assert client.knowledge("x y").available is True


def test_a_saved_handoff_returns_a_safe_reference():
    server = Server({"memory_add": {"id": "memory-123"}})
    reference = client_for(server).save_handoff(delivery.handoff_envelope({
        "event_id": "evt_abcdef", "correlation_id": "corr_abcdef", "module": "DischargeCare", "target_module": "ReferralLoop",
        "workflow_ref": "case_abc123", "occurred_at": "2026-10-31T10:00:00+00:00", "event_type": "discharge.approved",
    }))
    assert reference == "meko:memory-123"


def test_text_from_a_reply_never_becomes_a_reference():
    server = Server({"memory_add": {"id": "<script>not an identifier</script>"}})
    reference = client_for(server).save_handoff(delivery.handoff_envelope({
        "event_id": "evt_abcdef", "correlation_id": "corr_abcdef", "module": "DischargeCare", "target_module": "ReferralLoop",
        "workflow_ref": "case_abc123", "occurred_at": "2026-10-31T10:00:00+00:00", "event_type": "discharge.approved",
    }))
    assert reference == "meko:handoff_abcdef"


def confirmed_lab(client):
    careflow_demo.run()


def test_a_handoff_looks_up_real_context_before_it_is_noted(client, monkeypatch):
    careflow_demo.run()
    add_handoff(careflow_demo.CASE)
    add_handoff(careflow_demo.CASE)
    server = Server({"memory_add": {"id": "memory-123"}})
    monkeypatch.setattr(meko, "client", lambda: client_for(server))
    assert delivery.deliver_once()["meko"]["delivered"] == 2
    names = [call["body"]["params"]["name"] for call in server.calls if call["body"]["method"] == "tools/call"]
    assert names[:2] == ["knowledgebase_search", "memory_add"]
    assert "DischargeCare to ReferralLoop" in json.dumps(server.calls[-1]["body"])


def test_no_context_is_invented_when_meko_is_down(client, monkeypatch):
    careflow_demo.run()
    add_handoff(careflow_demo.CASE)
    add_handoff(careflow_demo.CASE)
    monkeypatch.setattr(meko, "client", lambda: client_for(Server(failure=500)))
    assert delivery.deliver_once()["meko"]["retrying"] == 1
    db = connect()
    row = db.execute("SELECT result_ref, status FROM careflow_deliveries WHERE channel = 'meko' AND attempts > 0").fetchone()
    db.close()
    assert row["result_ref"] is None and row["status"] == "pending"


def test_a_context_reference_goes_on_to_the_handoff_record(client, monkeypatch):
    careflow_demo.run()
    add_handoff(careflow_demo.CASE)
    add_handoff(careflow_demo.CASE)
    saved = []

    class Store:
        def save_handoff(self, handoff, state="recorded", context_ref=None):
            saved.append((state, context_ref))

        def save_runs(self, _runs):
            pass

    enable(monkeypatch, yugabyte_enabled=True, yugabyte_dsn="postgresql://synthetic")
    monkeypatch.setattr(yugabyte, "store", lambda: Store())
    server = Server({"memory_add": {"id": "memory-123"}})
    monkeypatch.setattr(meko, "client", lambda: client_for(server))
    delivery.deliver_once()
    delivery.deliver_once()
    assert ("recorded", None) in saved and ("context_shared", "meko:memory-123") in saved


# Knowledge


def test_the_knowledge_base_passes_its_own_checks():
    documents = knowledge.corpus()
    assert len(documents) >= 8
    assert {item.fields["category"] for item in documents} == knowledge.CATEGORIES


def document_with(tmp_path, body, **fields):
    header = {"id": "x", "title": "T", "category": "terminology", "version": "1.0", "source": "here", "review_status": "technical_reviewed", "clinical_content": "none", **fields}
    path = tmp_path / "x.md"
    path.write_text("---\n" + "\n".join(f"{key}: {value}" for key, value in header.items() if value is not None) + "\n---\n\n" + body, encoding="utf-8")
    return knowledge.parse(path)


@pytest.mark.parametrize(
    "body",
    ["Call 9876543210 for help", "Write to someone@example.com", "The key is gsk_abcdefghijklmnop", "mongodb+srv://user:pass@cluster", "DOB: 12/03/1980"],
)
def test_a_file_with_personal_details_or_secrets_is_refused(tmp_path, body):
    with pytest.raises(knowledge.KnowledgeRefused):
        knowledge.check(document_with(tmp_path, body))


@pytest.mark.parametrize("fields", [{"clinical_content": "dosing"}, {"review_status": "draft"}, {"category": "made_up"}, {"source": None}])
def test_a_file_without_clean_provenance_is_refused(tmp_path, fields):
    with pytest.raises(knowledge.KnowledgeRefused):
        knowledge.check(document_with(tmp_path, "Plain text", **fields))


def test_a_file_with_no_header_is_refused(tmp_path):
    path = tmp_path / "x.md"
    path.write_text("No provenance", encoding="utf-8")
    with pytest.raises(knowledge.KnowledgeRefused):
        knowledge.parse(path)


def test_the_memory_seed_is_clean():
    entries = json.loads((knowledge.FOLDER / "memory.json").read_text(encoding="utf-8"))
    client = client_for(Server())
    assert knowledge.seed_memory(client, entries) == len(entries)


class Uploads:
    def __init__(self, status_code=204):
        self.calls = []
        self.status_code = status_code

    def __call__(self, url, **options):
        self.calls.append({"url": url, **options})
        return httpx.Response(self.status_code)


def ingest_with(client_fixture, tmp_path, replies, uploads=None):
    uploads = uploads or Uploads()
    db = connect()
    document = document_with(tmp_path, "Plain workflow text")
    server = Server(replies)
    result = knowledge.ingest(client_for(server), db, [document], post=uploads, wait=lambda _seconds: None)
    db.close()
    return result, uploads, server, document


def test_a_file_is_uploaded_then_polled_until_indexed(client, tmp_path):
    states = iter([{"state": "indexing"}, {"state": "completed"}])
    result, uploads, server, document = ingest_with(client, tmp_path, {
        "knowledgebase_upload_document": {"url": "https://upload.example.invalid/x", "key": "doc-key"},
        "knowledgebase_document_status": lambda: next(states),
    })
    assert result == [{"document": "x.md", "state": "completed"}]
    assert uploads.calls[0]["content"] == document.data
    request = next(call for call in server.calls if call["body"].get("params", {}).get("name") == "knowledgebase_upload_document")
    arguments = request["body"]["params"]["arguments"]
    assert arguments["size"] == len(document.data) and arguments["content_hash"] == document.content_hash
    assert arguments["datapack_id"] == "datapack-synthetic"


def test_form_fields_from_the_reply_are_used_when_given(client, tmp_path):
    _result, uploads, _server, _document = ingest_with(client, tmp_path, {
        "knowledgebase_upload_document": {"url": "https://upload.example.invalid/x", "key": "doc-key", "fields": {"policy": "synthetic"}},
        "knowledgebase_document_status": {"state": "completed"},
    })
    assert uploads.calls[0]["data"] == {"policy": "synthetic"} and "files" in uploads.calls[0]


def test_the_meko_key_never_goes_to_an_upload_url(client, tmp_path):
    _result, uploads, _server, _document = ingest_with(client, tmp_path, {
        "knowledgebase_upload_document": {"url": "https://upload.example.invalid/x", "key": "doc-key"},
        "knowledgebase_document_status": {"state": "completed"},
    })
    assert "synthetic-key" not in str(uploads.calls[0])


def test_content_already_there_is_not_sent_again(client, tmp_path):
    result, uploads, _server, _document = ingest_with(client, tmp_path, {"knowledgebase_upload_document": {"already_uploaded": True, "key": "doc-key"}})
    assert result[0]["state"] == "already_uploaded" and uploads.calls == []


def test_a_finished_file_is_skipped_next_time(client, tmp_path):
    replies = {"knowledgebase_upload_document": {"url": "https://upload.example.invalid/x", "key": "doc-key"}, "knowledgebase_document_status": {"state": "completed"}}
    ingest_with(client, tmp_path, replies)
    result, uploads, _server, _document = ingest_with(client, tmp_path, replies)
    assert result[0]["state"] == "unchanged" and uploads.calls == []


def test_a_failed_upload_is_reported_and_keeps_going(client, tmp_path):
    result, _uploads, _server, _document = ingest_with(client, tmp_path, {"knowledgebase_upload_document": {"url": "https://upload.example.invalid/x", "key": "doc-key"}}, Uploads(403))
    assert result[0]["state"].startswith("failed")


def test_a_rejected_file_says_what_meko_said(client, tmp_path):
    result, _uploads, _server, _document = ingest_with(client, tmp_path, {
        "knowledgebase_upload_document": {"url": "https://upload.example.invalid/x", "key": "doc-key"},
        "knowledgebase_document_status": {"state": "abandoned"},
    })
    assert result[0]["state"] == "abandoned"


# Handoffs and operations, through the routes


def test_handoff_history_shows_where_each_record_has_got_to(client):
    careflow_demo.run()
    add_handoff(careflow_demo.CASE)
    add_handoff(careflow_demo.CASE)
    client.post("/api/auth/login", json={"email": careflow_demo.DOCTOR, "password": careflow_demo.PASSWORD})
    found = client.get(f"/api/careflow/workflows/{careflow_demo.CASE}/handoffs").json()["handoffs"]
    assert [(item["source_module"], item["target_module"]) for item in found] == [("DischargeCare", "ReferralLoop")] * 2
    assert found[0]["records"]["mongodb"]["state"] == "pending"


def test_someone_else_cannot_read_handoffs(client, person):
    careflow_demo.run()
    add_handoff(careflow_demo.CASE)
    add_handoff(careflow_demo.CASE)
    assert person("doctor").get(f"/api/careflow/workflows/{careflow_demo.CASE}/handoffs").status_code == 404


def test_operational_figures_are_for_administrators(person):
    assert person("doctor").get("/api/careflow/operations").status_code == 403
    assert person("patient").get("/api/careflow/operations").status_code == 403
    admin = person("doctor")
    verified_organization(admin)
    body = admin.get("/api/careflow/operations").json()
    assert body["source"] == "local_records" and body["figures"]["total_runs"] == 0


def test_operational_figures_fall_back_and_say_so(person, monkeypatch):
    admin = person("doctor")
    verified_organization(admin)
    enable(monkeypatch, yugabyte_enabled=True, yugabyte_dsn="postgresql://synthetic")

    def down():
        raise ConnectionError("synthetic")

    monkeypatch.setattr(yugabyte, "store", down)
    body = admin.get("/api/careflow/operations").json()
    assert body["source"] == "local_records" and body["degraded"] is True


def test_a_timeline_reports_what_is_still_waiting(client):
    careflow_demo.run()
    client.post("/api/auth/login", json={"email": careflow_demo.DOCTOR, "password": careflow_demo.PASSWORD})
    body = client.get(f"/api/careflow/workflows/{careflow_demo.CASE}/timeline").json()
    assert body["activity"]["pending_review"] >= 1 and "LabLens" in body["activity"]["modules"]


def test_no_log_line_holds_a_connection_string(client, caplog, monkeypatch):
    careflow_demo.run()
    db = connect()

    def down(_event):
        raise ConnectionError("mongodb+srv://user:synthetic-secret@cluster.invalid")

    delivery.deliver_channel(db, "mongodb", down)
    db.close()
    assert "synthetic-secret" not in caplog.text


def test_the_ai_stub_never_records_a_real_provider(client):
    careflow_demo.run()
    assert execution.summary()["total_runs"] >= 3
    db = execution.connect()
    providers = {row["provider"] for row in db.execute("SELECT provider FROM agent_runs")}
    db.close()
    assert providers == {"demo_stub"}


def test_a_setting_for_reading_tools_is_unchanged_by_all_this():
    assert settings_store.READING_MODELS["lablens_read"] == "gemini-3.5-flash"


def test_a_due_retry_is_not_sent_early(doctor, patient):
    waiting_events(doctor, patient)
    db = connect()
    first = db.execute("SELECT event_id FROM careflow_deliveries WHERE channel = 'mongodb' ORDER BY event_id").fetchone()["event_id"]
    now = datetime.now(timezone.utc)

    def down(_event):
        raise ConnectionError("synthetic")

    delivery.deliver_channel(db, "mongodb", down, now=now)
    failed = db.execute("SELECT event_id FROM careflow_deliveries WHERE attempts = 1").fetchone()["event_id"]
    sent = []
    delivery.deliver_channel(db, "mongodb", lambda item: sent.append(item["event_id"]), now=now + timedelta(seconds=1))
    db.close()
    assert first and failed not in sent
