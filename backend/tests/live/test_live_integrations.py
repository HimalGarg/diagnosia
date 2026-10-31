"""Real MongoDB Atlas, YugabyteDB and Meko, with synthetic data. Off unless asked for.

    DIAGNOSIA_LIVE_INTEGRATIONS=1 .venv/bin/python -m pytest -m live backend/tests/live/test_live_integrations.py

Each test proves the service is what it claims to be before doing anything, so a local stand-in can
never pass here. A service with no credentials is reported as BLOCKED_MISSING_CREDENTIALS. If it's
switched on in the settings its credentials are expected, so missing ones fail rather than skip.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest

from backend.careflow import delivery, stores
from backend.config import settings
from backend.integrations import execution, meko, yugabyte

pytestmark = pytest.mark.live
ENABLED = os.environ.get("DIAGNOSIA_LIVE_INTEGRATIONS") == "1"


def need(enabled: bool, *values):
    if not ENABLED:
        pytest.skip("Set DIAGNOSIA_LIVE_INTEGRATIONS=1")
    missing = [name for name, value in values if not value]
    if missing and enabled:
        pytest.fail(f"Switched on, but BLOCKED_MISSING_CREDENTIALS: {', '.join(missing)}")
    if missing:
        pytest.skip(f"BLOCKED_MISSING_CREDENTIALS: {', '.join(missing)}")


def event(workflow_ref, event_id=None, occurred=None, **changes):
    return {
        "event_id": event_id or f"evt_{uuid.uuid4().hex}", "schema_version": "1.1", "event_type": "lab.review_required", "module": "LabLens",
        "workflow_stage": "during_visit", "source_system": "diagnosia", "source_operation": "live.test", "source_ref": None,
        "workflow_ref": workflow_ref, "tenant_ref": "tenant_live", "correlation_id": f"corr_{uuid.uuid4().hex}", "causation_id": None,
        "occurred_at": occurred or datetime.now(timezone.utc).isoformat(), "status": "review_required", "actor_type": "system", "target_module": None,
        "metadata": {"review_required": True}, "data_classification": "synthetic_demo", **changes,
    }


@pytest.fixture
def atlas(monkeypatch):
    """A disposable database on the real cluster. Dropped afterwards, so nothing real is touched."""
    need(settings.mongodb_enabled, ("MONGODB_ATLAS_URI", settings.mongodb_uri))
    from pymongo import MongoClient

    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=8000, tz_aware=True, w="majority")
    client.admin.command("ping")
    hosts = [host for host, _port in client.nodes]
    assert stores.deployment_name(hosts) == "mongodb_atlas", "This server is not MongoDB Atlas, so this can't count as a live result"
    name = f"diagnosia_live_{uuid.uuid4().hex[:8]}"
    store = stores.MongoEventStore(client[name], "mongodb_atlas")
    store.bootstrap()
    yield store
    client.drop_database(name)


def test_atlas_passes_every_health_step(atlas):
    assert all(passed for _label, passed, _detail in atlas.verify())


def test_atlas_stores_a_repeated_event_once(atlas):
    item = event("case_live_one")
    assert atlas.put(item) is True
    assert atlas.put(item) is False
    assert len(atlas.timeline("case_live_one")) == 1


def test_atlas_really_refuses_a_bad_document(atlas):
    from pymongo.errors import WriteError

    with pytest.raises(WriteError):
        atlas.events.insert_one({"_id": "evt_bad", "workflow_ref": "Ravi Kumar"})


def test_atlas_projection_survives_events_arriving_out_of_order(atlas):
    atlas.put(event("case_live_two", occurred="2026-10-31T11:00:00+00:00", event_type="lab.report_confirmed", status="completed"))
    atlas.put(event("case_live_two", occurred="2026-10-31T10:00:00+00:00"))
    found = atlas.projections.find_one({"_id": "case_live_two"})
    assert found["event_count"] == 2 and found["latest_activity"] == "lab.report_confirmed"


def test_atlas_change_stream_sees_a_new_event_and_can_resume(atlas):
    with atlas.events.watch([{"$match": {"operationType": "insert"}}], max_await_time_ms=1000) as stream:
        atlas.put(event("case_live_stream"))
        change = None
        for _attempt in range(10):
            change = stream.try_next()
            if change:
                break
        token = stream.resume_token
    assert change["fullDocument"]["workflow_ref"] == "case_live_stream"
    atlas.put(event("case_live_after"))
    with atlas.events.watch([{"$match": {"operationType": "insert"}}], resume_after=token, max_await_time_ms=1000) as stream:
        resumed = None
        for _attempt in range(10):
            resumed = stream.try_next()
            if resumed:
                break
    assert resumed["fullDocument"]["workflow_ref"] == "case_live_after"


@pytest.fixture
def ysql(monkeypatch):
    need(settings.yugabyte_enabled, ("YUGABYTEDB_YSQL_DSN", settings.yugabyte_dsn or os.environ.get("YUGABYTEDB_YSQL_DSN")))
    monkeypatch.setattr(settings, "yugabyte_enabled", True)
    monkeypatch.setattr(settings, "yugabyte_dsn", settings.yugabyte_dsn or os.environ["YUGABYTEDB_YSQL_DSN"])
    yugabyte.reset()
    store = yugabyte.store()
    assert store.identity()["is_yugabytedb"], "This server is not YugabyteDB, so this can't count as a live result"
    yield store
    yugabyte.reset()


def test_yugabytedb_passes_every_health_step(ysql):
    assert all(passed for _label, passed, _detail in ysql.verify())


def test_yugabytedb_migrations_repeat_safely(ysql):
    assert ysql.migrate() == []


def test_yugabytedb_keeps_a_handoff_once_however_often_it_is_retried(ysql):
    reference = f"case_live_{uuid.uuid4().hex[:12]}"
    handoff = delivery.handoff_envelope(event(reference, target_module="ReferralLoop"))
    ysql.save_handoff(handoff)
    ysql.save_handoff(handoff, "context_shared", "meko:synthetic")
    found = ysql.handoffs(reference)
    assert len(found) == 1 and found[0]["handoff_state"] == "context_shared"


def run_record(tool, status="succeeded"):
    run_id = f"run_{uuid.uuid4().hex}"
    return {
        "run_id": run_id, "workflow_ref": None, "module": "LabLens", "tool": tool, "status": status, "provider": "gemini", "model": "gemini-3.5-flash",
        "fallback_used": 1, "fallback_provider": "gemini", "fallback_model": "gemini-3.5-flash-lite", "error_category": None, "trace_ref": None,
        "attempt_count": 2, "started_at": "2026-10-31T10:00:00+00:00", "finished_at": "2026-10-31T10:00:02+00:00", "duration_ms": 2000,
        "attempts": [
            {"attempt_id": f"{run_id}:1", "attempt_number": 1, "provider": "gemini", "model": "gemini-3.5-flash", "status": "failed", "error_category": "AIRateLimited", "started_at": "2026-10-31T10:00:00+00:00", "finished_at": "2026-10-31T10:00:01+00:00", "duration_ms": 1000},
            {"attempt_id": f"{run_id}:2", "attempt_number": 2, "provider": "gemini", "model": "gemini-3.5-flash-lite", "status": "succeeded", "error_category": None, "started_at": "2026-10-31T10:00:01+00:00", "finished_at": "2026-10-31T10:00:02+00:00", "duration_ms": 1000},
        ],
    }


def test_yugabytedb_keeps_a_run_with_its_attempts_once(ysql):
    tool = f"live_{uuid.uuid4().hex[:10]}"
    run = run_record(tool)
    ysql.save_runs([run])
    ysql.save_runs([run])
    assert [row for row in ysql.run_summary() if row["tool"] == tool] == [{"tool": tool, "runs": 1, "failed": 0, "needed_fallback": 1}]


def test_yugabytedb_updates_a_run_when_it_finishes(ysql):
    tool = f"live_{uuid.uuid4().hex[:10]}"
    run = run_record(tool, "running")
    run["attempts"] = []
    ysql.save_runs([run])
    run["status"] = "interrupted"
    ysql.save_runs([run])
    with ysql.pool.connection() as connection:
        assert connection.execute("SELECT status FROM integration_agent_runs WHERE run_id = %s", (run["run_id"],)).fetchone()[0] == "interrupted"


def test_yugabytedb_abandons_a_failed_transaction_whole(ysql):
    tool = f"live_{uuid.uuid4().hex[:10]}"
    run = run_record(tool)
    run["attempts"][1]["status"] = "not-a-valid-status"
    with pytest.raises(Exception):
        ysql.save_runs([run])
    with ysql.pool.connection() as connection:
        assert connection.execute("SELECT count(*) FROM integration_agent_runs WHERE run_id = %s", (run["run_id"],)).fetchone()[0] == 0


def test_yugabytedb_operational_figures_answer(ysql):
    assert ysql.operations()["total_runs"] >= 0


@pytest.fixture
def real_meko():
    need(settings.meko_enabled, ("MEKO_API_KEY", settings.meko_api_key), ("MEKO_DATAPACK_ID", settings.meko_datapack_id))
    return meko.MekoClient(settings.meko_url, settings.meko_api_key, settings.meko_datapack_id)


def test_meko_authenticates_and_offers_the_tools_we_use(real_meko):
    assert {"knowledgebase_search", "knowledgebase_upload_document", "knowledgebase_document_status", "memory_add", "memory_search", "datapack_describe"} <= set(real_meko.tools())


def test_meko_describes_the_configured_datapack(real_meko):
    assert real_meko.describe_datapack()


def test_meko_knowledge_search_answers(real_meko):
    found = real_meko.knowledge("Diagnosia handoff rules")
    assert found.available is True


def test_meko_memory_round_trip(real_meko):
    marker = f"diagnosia-live-check-{uuid.uuid4().hex[:8]}"
    real_meko.remember(f"Synthetic live check {marker}", {"kind": "live_check", "provenance": "live test"})
    assert real_meko.search_memory(marker) is not None


def test_meko_refuses_a_wrong_key():
    need(settings.meko_enabled, ("MEKO_API_KEY", settings.meko_api_key), ("MEKO_DATAPACK_ID", settings.meko_datapack_id))
    client = meko.MekoClient(settings.meko_url, "mko_tkn_deliberately_wrong", settings.meko_datapack_id)
    with pytest.raises(meko.MekoUnavailable):
        client.tools()
