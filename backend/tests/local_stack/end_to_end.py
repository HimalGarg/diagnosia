"""The whole synthetic journey across whatever services are configured, with evidence printed.

    python -m backend.tests.local_stack.end_to_end [--real-ai] [--mongo-container NAME] [--yugabyte-container NAME]

It reports what each service really is, so a stand-in can't be mistaken for the real thing. Give a
container name to stop that server for real during the outage step. Without one, the outage is
simulated by making the destination fail, which is the only safe way to test against a cloud service.
Use a throwaway DIAGNOSIA_DB. It refuses to run against one without `e2e` in its path.
"""
import argparse
import json
import subprocess
import sys
import time
from typing import Optional

from fastapi.testclient import TestClient

from backend.app import app
from backend.careflow import delivery, demo, stores
from backend.config import settings
from backend.db import connect, init_db
from backend.integrations import execution, status, yugabyte


def show(label: str, value: object) -> None:
    print(f"{label}: {json.dumps(value, default=str)}", flush=True)


def drain() -> dict:
    total: dict = {}
    for _attempt in range(40):
        result = delivery.deliver_once()
        for channel, outcome in result.items():
            total[channel] = total.get(channel, 0) + outcome["delivered"]
        if not any(outcome["delivered"] for outcome in result.values()):
            break
    return total


def make_due() -> None:
    db = connect()
    db.execute("UPDATE careflow_deliveries SET next_attempt_at = '2000-01-01T00:00:00' WHERE status = 'pending'")
    db.commit()
    db.close()


def docker(*arguments: str) -> None:
    subprocess.run(["docker", *arguments], capture_output=True, check=True)


def wait_for(check, seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception:  # still starting
            pass
        time.sleep(2)
    raise SystemExit("A server didn't come back in time")


def outage_of_mongodb(client: TestClient, patient_id: str, container: Optional[str]) -> None:
    store = stores.mongo_store()
    if store is None:
        return
    before = store.events.count_documents({"workflow_ref": demo.CASE})
    if container:
        docker("stop", "-t", "1", container)
    else:
        real_put = stores.MongoEventStore.put
        stores.MongoEventStore.put = lambda _self, _event: (_ for _ in ()).throw(ConnectionError("simulated outage"))
    show("tool during MongoDB outage (HTTP status)", client.post(f"/api/patients/{patient_id}/encounters", json={"reason": "Outage check"}).status_code)
    show("tool event during MongoDB outage", client.post("/api/addons/medguard/reconcile", json={"patient_id": patient_id}).status_code)
    show("delivery during outage", delivery.deliver_once().get("mongodb"))
    db = connect()
    show("outbox kept events during outage", delivery.backlog(db)["mongodb"])
    db.close()
    timeline = client.get(f"/api/careflow/workflows/{demo.CASE}/timeline").json()
    show("timeline during outage", {"source": timeline["source"], "degraded": timeline["degraded"], "events": len(timeline["events"])})
    if container:
        docker("start", container)
        stores.reset_mongo()
        wait_for(lambda: stores.mongo_store() is not None)
    else:
        stores.MongoEventStore.put = real_put
    make_due()
    show("delivered after recovery", drain())
    db = connect()
    db.execute("UPDATE careflow_deliveries SET status = 'pending', next_attempt_at = '2000-01-01T00:00:00' WHERE channel = 'mongodb'")
    db.commit()
    outbox = db.execute("SELECT COUNT(*) FROM careflow_outbox WHERE workflow_ref = ?", (demo.CASE,)).fetchone()[0]
    db.close()
    show("replayed every event on purpose", drain())
    stored = stores.mongo_store().events.count_documents({"workflow_ref": demo.CASE})
    show("no duplicates", {"before_outage": before, "outbox": outbox, "stored": stored, "match": outbox == stored})


def outage_of_yugabyte(client: TestClient, patient_id: str, container: Optional[str]) -> None:
    if yugabyte.store() is None or not container:
        return
    docker("stop", "-t", "2", container)
    yugabyte.reset()
    code = client.post(f"/api/patients/{patient_id}/encounters", json={"reason": "Second outage check"}).status_code
    show("tool during YugabyteDB outage (HTTP status)", code)
    show("worker during YugabyteDB outage still runs", {channel: outcome for channel, outcome in delivery.deliver_once().items()})
    show("AI run records waiting in the local file", execution.waiting())
    docker("start", container)
    wait_for(lambda: yugabyte.store() is not None and yugabyte.store().identity()["is_yugabytedb"], 120)
    drain()
    show("after restart, YugabyteDB still holds", yugabyte.store().operations())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-ai", action="store_true")
    parser.add_argument("--mongo-container")
    parser.add_argument("--yugabyte-container")
    options = parser.parse_args()
    if "e2e" not in str(settings.db_path):
        print("Use a throwaway DIAGNOSIA_DB with e2e in its path.", file=sys.stderr)
        return 1
    init_db()
    for item in status.overview(deep=True):
        show(item.name, {"state": item.state, "detail": item.detail, "checks": item.checks})

    # An AI run that a crash left unfinished, from an earlier process.
    records = execution.connect()
    records.execute(
        "INSERT OR IGNORE INTO agent_runs (run_id, module, tool, status, started_at, boot_id, updated_at) VALUES ('run_crashed_earlier', 'LabLens', 'lablens_read', 'running', '2026-10-31T00:00:00+00:00', 'an-earlier-process', '2026-10-31T00:00:00+00:00')"
    )
    records.commit()
    records.close()
    show("interrupted runs recovered", execution.recover_interrupted())

    started = time.monotonic()
    show("journey", demo.run(real_ai=options.real_ai))
    show("journey seconds", round(time.monotonic() - started, 1))
    show("drained", drain())
    db = connect()
    show("backlog", delivery.backlog(db))
    rows = db.execute("SELECT module, event_type, status FROM careflow_outbox WHERE workflow_ref = ? ORDER BY occurred_at, event_id", (demo.CASE,)).fetchall()
    db.close()
    show("events", [f"{row['module']}: {row['event_type']} [{row['status']}]" for row in rows])

    client = TestClient(app)
    client.post("/api/auth/login", json={"email": demo.DOCTOR, "password": demo.PASSWORD})
    timeline = client.get(f"/api/careflow/workflows/{demo.CASE}/timeline").json()
    show("timeline", {"source": timeline["source"], "degraded": timeline["degraded"], "events": len(timeline["events"]), "activity": timeline["activity"]})
    show("handoffs", client.get(f"/api/careflow/workflows/{demo.CASE}/handoffs").json()["handoffs"])
    store = yugabyte.store()
    if store is not None:
        show("YugabyteDB operations", store.operations())
        show("YugabyteDB runs by tool", store.run_summary())
        show("YugabyteDB handoffs", store.handoffs(demo.CASE))
    show("local AI runs", execution.summary())
    db = execution.connect()
    show("AI providers used", [dict(row) for row in db.execute("SELECT tool, provider, model, status, fallback_used, duration_ms FROM agent_runs WHERE run_id != 'run_crashed_earlier' ORDER BY started_at")])
    db.close()

    patient = next(item for item in client.get("/api/patients").json() if item["first_name"] == demo.SYNTHETIC_NAME["first_name"])
    outage_of_mongodb(client, patient["id"], options.mongo_container)
    outage_of_yugabyte(client, patient["id"], options.yugabyte_container)
    mongo = stores.mongo_store()
    if mongo is not None:
        text = json.dumps(list(mongo.events.find({}, {"_id": 0, "occurred_at": 0, "recorded_at": 0})), default=str)
        show("clinical text found in the event store", [word for word in ("Demo Person", "Penicillin", "Haemoglobin", "Glycomet", "cough", "@example.com") if word in text])
    return 0


if __name__ == "__main__":
    sys.exit(main())
