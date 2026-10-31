# YugabyteDB setup

This is **your own** YugabyteDB, separate from the one inside Meko. Never ask Meko for database
credentials, and never point this at Meko's storage.

1. Provision a cluster, and a user who owns one database, with TLS on. Don't use an admin user at run time.
2. Install the driver: `pip install -r backend/requirements-integrations.txt`
3. Add to `.env`:

   ```dotenv
   ENABLE_YUGABYTEDB_INTEGRATION=1
   YUGABYTEDB_YSQL_DSN=
   ```

4. Create the tables: `python -m backend.operator_cli integrations-migrate`. It's safe to repeat.

## How it's checked

`integrations-status` asks the server what it is: `version()` must carry a `-YB-` build, and `yb_servers()` must answer. A plain PostgreSQL server reports as `FAILED`, however well it works. `yugabyte-health` then checks permissions, migrations, a write and read, and that a rolled-back transaction leaves nothing.

## Tables

| Table | One row per |
|---|---|
| `integration_agent_runs` | AI call: workflow, tool, provider, model, fallback, status, timing, error category and trace reference |
| `integration_task_attempts` | Provider and model tried in that call, with its timing and safe error category |
| `integration_handoffs` | Handoff between tools, unique on its source event, with its state and context reference |
| `integration_schema_migrations` | Migration, with its version, status and time |

There are no patient, user, consent or event tables, by design. No prompt, answer or reasoning is stored.

## AI run records

A run is written to a small local SQLite file (`<database>-execution.db`) when it begins, and updated when it ends. It's a separate file because a request holds the main database's write lock while the AI works, so writing there would wait on itself. The worker copies new and changed runs here within 5 seconds. With YugabyteDB off, they stay in the local file.

A process that stops mid-run leaves a `running` record, and the next start marks it `interrupted`.

## Operational figures

`GET /api/careflow/operations`, for administrators of a verified organization, gives total, successful, failed and interrupted runs, average time, fallback and retry counts, and handoffs. The queries are fixed, and no endpoint accepts SQL.

## If it's down

Handoffs wait in the outbox, and AI run records wait in the local file. Nothing clinical is affected, because only the delivery worker connects to it.

## Try it locally

```bash
docker compose -f docker-compose.integrations.yml up -d yugabytedb
# YUGABYTEDB_YSQL_DSN=postgresql://yugabyte:yugabyte@127.0.0.1:5433/yugabyte
```
