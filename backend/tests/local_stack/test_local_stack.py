"""The real drivers against local servers in Docker. These are stand-ins, not live results.

A local MongoDB isn't Atlas, and PostgreSQL isn't YugabyteDB. They check that our queries, indexes,
validators and Change Stream code work against a real server, before any cloud service is involved.

    LOCAL_MONGODB_URI='mongodb://127.0.0.1:37017/?directConnection=true' \\
    LOCAL_POSTGRES_DSN='postgresql://postgres:synthetic_only@127.0.0.1:55433/diagnosia_exec' \\
        .venv/bin/python -m pytest -m local_stack backend/tests/local_stack
"""
import os
import threading
import time
import uuid

import pytest

from backend.careflow import delivery, live, stores
from backend.integrations import yugabyte
from backend.tests.live.test_live_integrations import event, run_record

pytestmark = pytest.mark.local_stack


@pytest.fixture
def local_mongo():
    uri = os.environ.get("LOCAL_MONGODB_URI")
    if not uri:
        pytest.skip("LOCAL_MONGODB_URI isn't set")
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    name = f"diagnosia_local_{uuid.uuid4().hex[:8]}"
    store = stores.MongoEventStore(client[name], stores.deployment_name([host for host, _port in (client.admin.command("ping"), client.nodes)[1]]))
    store.bootstrap()
    yield store
    client.drop_database(name)
    client.close()


def test_a_local_server_is_labelled_as_local(local_mongo):
    assert local_mongo.name == "mongodb_local"


def test_every_health_step_passes_on_a_real_server(local_mongo):
    assert all(passed for _label, passed, _detail in local_mongo.verify())


def test_setup_can_run_again_and_updates_the_validator(local_mongo):
    local_mongo.bootstrap()
    local_mongo.bootstrap()
    assert "validator" in local_mongo.events.options()


def test_a_repeat_is_stored_once(local_mongo):
    item = event("case_local_one")
    assert local_mongo.put(item) is True
    assert local_mongo.put(item) is False
    assert len(local_mongo.timeline("case_local_one")) == 1


def test_the_validator_refuses_a_bad_document(local_mongo):
    from pymongo.errors import WriteError

    with pytest.raises(WriteError) as refused:
        local_mongo.events.insert_one({"_id": "evt_bad", "workflow_ref": "Ravi Kumar"})
    assert refused.value.code == delivery.VALIDATION_FAILED


def test_the_projection_is_right_when_events_arrive_late(local_mongo):
    local_mongo.put(event("case_local_two", occurred="2026-10-31T11:00:00+00:00", event_type="lab.report_confirmed", status="completed"))
    local_mongo.put(event("case_local_two", occurred="2026-10-31T10:00:00+00:00"))
    found = local_mongo.projections.find_one({"_id": "case_local_two"})
    assert found["event_count"] == 2 and found["pending_review_count"] == 0
    assert [item["event_type"] for item in found["events"]] == ["lab.review_required", "lab.report_confirmed"]


def test_filters_use_real_queries(local_mongo):
    local_mongo.put(event("case_local_three"))
    local_mongo.put(event("case_local_other"))
    assert len(local_mongo.recent(["case_local_three"], "LabLens", "review_required", "2026-01-01", None)) == 1
    assert local_mongo.summary(["case_local_three"])["total"] == 1


def test_delivery_state_is_kept_once_per_destination(local_mongo):
    state = {"event_id": "evt_abc", "destination": "mongodb", "state": "delivered", "attempts": 1}
    local_mongo.record_delivery(state)
    local_mongo.record_delivery({**state, "attempts": 2})
    assert local_mongo.deliveries.count_documents({}) == 1


@pytest.fixture
def stream_state(client):
    live.status.__init__()
    yield


def test_a_real_change_stream_reaches_the_feed_and_resumes_after_restart(local_mongo, stream_state):
    stop = threading.Event()
    thread = threading.Thread(target=live.watch, args=(stop, local_mongo), daemon=True)
    thread.start()
    time.sleep(1.5)
    reference = f"case_local_{uuid.uuid4().hex[:10]}"
    before = live.feed.version
    local_mongo.put(event(reference))
    for _attempt in range(60):
        _version, changed = live.feed.since(before)
        if reference in changed:
            break
        time.sleep(0.1)
    assert reference in changed
    live.Watcher(thread, stop).stop()
    assert not thread.is_alive()
    saved = live.load_token()
    assert saved

    # Something happens while the watcher is down, as in a restart.
    missed = f"case_local_{uuid.uuid4().hex[:10]}"
    local_mongo.put(event(missed))
    stop = threading.Event()
    thread = threading.Thread(target=live.watch, args=(stop, local_mongo), daemon=True)
    before = live.feed.version
    thread.start()
    for _attempt in range(60):
        _version, changed = live.feed.since(before)
        if missed in changed:
            break
        time.sleep(0.1)
    live.Watcher(thread, stop).stop()
    assert missed in changed
    assert live.status.resumed is True


def test_a_real_stale_token_recovers_by_starting_fresh(local_mongo, stream_state):
    live.save_token({"_data": "82635019A0000000012B042C0100296E5A1004"})
    stop = threading.Event()
    thread = threading.Thread(target=live.watch, args=(stop, local_mongo), daemon=True)
    thread.start()
    for _attempt in range(80):
        if live.status.connected or live.status.reconnects:
            break
        time.sleep(0.1)
    for _attempt in range(40):
        if live.load_token() is None and live.status.connected:
            break
        time.sleep(0.25)
    live.Watcher(thread, stop).stop()
    assert live.load_token() is None


@pytest.fixture
def postgres(monkeypatch):
    dsn = os.environ.get("LOCAL_POSTGRES_DSN")
    if not dsn:
        pytest.skip("LOCAL_POSTGRES_DSN isn't set")
    from backend.config import settings

    monkeypatch.setattr(settings, "yugabyte_enabled", True)
    monkeypatch.setattr(settings, "yugabyte_dsn", dsn)
    yugabyte.reset()
    yield yugabyte.store()
    yugabyte.reset()


def test_postgresql_says_it_is_not_yugabytedb(postgres):
    assert postgres.identity()["is_yugabytedb"] is False


def test_the_sql_works_on_plain_postgresql_too(postgres):
    tool = f"local_{uuid.uuid4().hex[:10]}"
    postgres.save_runs([run_record(tool)])
    assert [row["runs"] for row in postgres.run_summary() if row["tool"] == tool] == [1]


def test_a_real_wrong_password_is_failed_and_leaks_nothing(monkeypatch, caplog):
    dsn = os.environ.get("LOCAL_POSTGRES_DSN")
    if not dsn:
        pytest.skip("LOCAL_POSTGRES_DSN isn't set")
    from backend.config import settings
    from backend.integrations import status

    monkeypatch.setattr(settings, "yugabyte_enabled", True)
    monkeypatch.setattr(settings, "yugabyte_dsn", dsn.replace("synthetic_only", "wrong-synthetic-secret"))
    yugabyte.reset()
    found = status.yugabytedb()
    assert found.state == "FAILED"
    assert "wrong-synthetic-secret" not in found.detail + caplog.text


def test_a_real_unreachable_server_is_failed(monkeypatch):
    from backend.config import settings
    from backend.integrations import status

    monkeypatch.setattr(settings, "yugabyte_enabled", True)
    monkeypatch.setattr(settings, "yugabyte_dsn", "postgresql://synthetic:synthetic-secret@127.0.0.1:1/none")
    yugabyte.reset()
    found = status.yugabytedb()
    assert found.state == "FAILED" and "synthetic-secret" not in found.detail


@pytest.mark.parametrize("uri", ["not-a-uri", "mongodb://user:synthetic-secret@127.0.0.1:1/?serverSelectionTimeoutMS=500"])
def test_a_bad_mongodb_address_is_failed_and_never_falls_back(monkeypatch, uri):
    from backend.config import settings
    from backend.integrations import status

    monkeypatch.setattr(settings, "mongodb_enabled", True)
    monkeypatch.setattr(settings, "mongodb_uri", uri)
    monkeypatch.setattr(settings, "mongodb_timeout_ms", 500)
    stores.reset_mongo()
    found = status.mongodb()
    stores.reset_mongo()
    assert found.state == "FAILED" and "synthetic-secret" not in found.detail
