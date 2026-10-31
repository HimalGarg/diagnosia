# External integration validation

> **Update, 1 November 2026:** MedGuard, ChronicCare, MaternaCare and AMR Guard were removed from Diagnosia after this was written. It is a record of 31 October 2026 and still describes them. The current tools are in the README.

Everything here happened on 31 October 2026, with synthetic data only. Real services and local stand-ins are kept apart, and a stand-in result is never a live result.

| Service | What was tested against | Real? | Result |
|---|---|---|---|
| YugabyteDB | YugabyteDB 2026.1.2.0, single node, in Docker | **Yes, a real YugabyteDB server**, though not a cloud cluster | 7 of 7 live tests passed |
| MongoDB Atlas | Nothing | **No.** `BLOCKED_MISSING_CREDENTIALS: MONGODB_ATLAS_URI` | 5 live tests not run |
| Meko | Nothing with a key. Its server returned 401 to an unauthenticated handshake | **No.** `BLOCKED_MISSING_CREDENTIALS: MEKO_API_KEY, MEKO_DATAPACK_ID` | 5 live tests not run |
| MongoDB 8.0 | A local replica set in Docker | **Stand-in, not Atlas** | 13 local tests passed |
| PostgreSQL 16 | A local server in Docker | **Stand-in, not YugabyteDB** | 3 local tests passed |
| Gemini and Groq | The real services | **Yes** | Used in the end-to-end run |

## YugabyteDB

- **Command:** `integrations-validate`, with `ENABLE_YUGABYTEDB_INTEGRATION` and `YUGABYTEDB_YSQL_DSN` set
- **Connection:** succeeded, over YSQL on port 5433. TLS wasn't used, because this is a local server
- **Authentication:** the local default user. That server doesn't enforce passwords, so a wrong password was tested on PostgreSQL instead
- **Identity:** `version()` returned a `-YB-2026.1.2.0` build, and `yb_servers()` returned 1 node
- **Database:** `diagnosia_app` for the local app, and throwaway databases for tests
- **Migration:** `001_execution.sql` applied, and repeating it changed nothing. 4 of 4 tables present
- **Write and read:** a row stored, read and removed. An abandoned transaction left nothing behind
- **Idempotency:** a repeated run stayed one row with its two attempts, and a repeated handoff stayed one row
- **Failed transaction:** a run with one invalid attempt left neither the run nor its attempts
- **Restart:** with the server stopped and started, 5 runs and 2 handoffs were still there
- **During an outage:** a clinical write returned 201, and the records caught up afterwards
- **Interrupted run:** an unfinished run from an earlier process was marked `interrupted` and reached YugabyteDB

## MongoDB Atlas

**Not connected.** To run: set `ENABLE_MONGODB_ATLAS_INTEGRATION=true` and `MONGODB_ATLAS_URI`, then run `integrations-validate`. The Atlas tests fail on any server outside `mongodb.net`, so a stand-in can't pass them.

## MongoDB 8.0 stand-in

This proves the real driver, queries, indexes, validator and Change Stream code. It doesn't prove Atlas.

- **Command:** `pytest -m local_stack backend/tests/local_stack`
- **Setup:** collections, 4 event indexes and an active validator. Repeating setup changed nothing
- **Write and read:** a synthetic event written, read back and removed
- **Validator:** a malformed document refused with code 121
- **Duplicates:** a repeated event stored once
- **Projection:** correct with events arriving out of order
- **Change stream:** a new event reached the feed. With the watcher stopped, a missed event was picked up on restart from the saved position
- **Bad position:** this found a real bug. MongoDB refuses a corrupt token with code 50811, which wasn't handled, so the watcher would have retried forever. It now starts fresh
- **Bad addresses:** a malformed URI and an unreachable server both reported `FAILED`, with no credential in the output
- **Labelling:** reported as `mongodb_local`, and `BLOCKED` in status, and the screen says "not Atlas"

## Meko

**Not connected.** To run: set `ENABLE_MEKO_INTEGRATION=true`, `MEKO_API_KEY` and `MEKO_DATAPACK_ID`, then run `integrations-validate`, `meko-ingest` and `meko-seed-memory`.

Checked by hand against the real server: `https://mcp.mekodata.ai/mcp` returns 401 without a valid Bearer key. Everything else about Meko has run against a mock only.

## End to end

- **Command:** `python -m backend.tests.local_stack.end_to_end --real-ai --mongo-container ... --yugabyte-container ...`
- **Time:** 18:20 UTC
- **AI:** real Groq for the interview, report and note, and real Gemini for the scanned lab report, after its larger model fell back
- **Against:** real YugabyteDB, **local MongoDB**, no Meko

| Step | Result |
|---|---|
| Journey | 32 events across DiagnoFast, LabLens, MedGuard, ClinScribe and ReferralLoop |
| Delivered | 32 to MongoDB, 2 handoffs to YugabyteDB |
| YugabyteDB | 5 runs: 4 succeeded, 1 interrupted, 1 used a fallback |
| MongoDB stopped for real | Clinical writes returned 201, events waited in the outbox, and CareFlow labelled the fallback |
| After restarting | The 3 waiting events arrived |
| Replaying all 35 on purpose | 35 in the outbox and 35 stored, so no duplicates |
| YugabyteDB stopped for real | Clinical writes returned 201 |
| After restarting | 5 runs and 2 handoffs still there |
| Clinical text in the event store | None found |

An earlier pass reported 76 stored against 35. That was 41 events left in the database by a previous run, not duplicates, because the clean-up hadn't worked. The result above is from a brand-new database.

## Browser

Chrome, against a real server, with real YugabyteDB and **local MongoDB**.

| Check | With change streams | Polling only |
|---|---|---|
| All checks | 19 of 19 | 17 of 17 |
| New event appeared, with no reload | 2.1 seconds | 9.2 seconds |

Covered: signing in, events, tool and status filters, handoffs, review indicators, the waiting count, integration status, AI run figures, the source label, labelled fallback with MongoDB stopped, an error state, and phone width.
