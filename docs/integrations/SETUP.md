# Integration setup

MongoDB Atlas, YugabyteDB and Meko are all optional. With none of them, Diagnosia runs as normal, and CareFlow reads a local copy and says so.

## What you need

| Service | Where to get it | `.env` variables |
|---|---|---|
| MongoDB Atlas | A cluster at [cloud.mongodb.com](https://cloud.mongodb.com). Change streams work on the free tier, with limits on filtering by database name that this doesn't use | `ENABLE_MONGODB_ATLAS_INTEGRATION=true`, `MONGODB_ATLAS_URI` |
| YugabyteDB | Your own cluster, YugabyteDB Aeon, or the Docker image | `ENABLE_YUGABYTEDB_INTEGRATION=true`, `YUGABYTEDB_YSQL_DSN` |
| Meko | An API key and a datapack at [cloud.mekodata.ai](https://cloud.mekodata.ai) | `ENABLE_MEKO_INTEGRATION=true`, `MEKO_API_KEY`, `MEKO_DATAPACK_ID` |
| Live updates | Any Atlas cluster | `ENABLE_MONGODB_CHANGE_STREAMS=true` |

Put these in `.env`, which git ignores. Never put them in code, a document or a chat message.

## Install

```bash
uv pip install -r backend/requirements.txt -r backend/requirements-integrations.txt
```

## Check, then start

```bash
.venv/bin/python -m backend.operator_cli integrations-status
.venv/bin/python -m backend.operator_cli integrations-migrate     # creates the YugabyteDB tables
.venv/bin/uvicorn backend.app:app --reload --port 8001
```

The app sends waiting events by itself, every 5 seconds. There's no separate worker to run.

`integrations-status` tries each service now:

| State | Meaning |
|---|---|
| `CONNECTED` | It answered just now, and it's really that service |
| `DISABLED` | Its switch is off |
| `BLOCKED` | Credentials are missing, or it's a local stand-in rather than the real service |
| `FAILED` | It was tried, and it failed. The kind of error is shown, never its text |

## Health and live tests

```bash
.venv/bin/python -m backend.operator_cli mongodb-health
.venv/bin/python -m backend.operator_cli yugabyte-health
.venv/bin/python -m backend.operator_cli meko-health
.venv/bin/python -m backend.operator_cli integrations-validate    # health, then live tests for what's connected
```

## Load the Meko knowledge base

```bash
.venv/bin/python -m backend.operator_cli meko-ingest --dry-run
.venv/bin/python -m backend.operator_cli meko-ingest
.venv/bin/python -m backend.operator_cli meko-seed-memory
```

## Run the demo

```bash
.venv/bin/python -m backend.operator_cli careflow-demo             # fixed answers, no AI cost
.venv/bin/python -m backend.operator_cli careflow-demo --real-ai   # real Gemini and Groq
```

Sign in as `careflow.demo.doctor@example.com` with `Synthetic-demo-only-1`, then open CareFlow.

## Local development

`docker compose -f docker-compose.integrations.yml up -d` starts a MongoDB replica set and a real YugabyteDB. A local MongoDB is never reported as Atlas.

```dotenv
YUGABYTEDB_YSQL_DSN=postgresql://yugabyte:yugabyte@127.0.0.1:5433/yugabyte
```

## Rotating keys

The Groq and Gemini keys used so far were pasted into a chat, so treat them as exposed. **They have not been rotated.** To rotate:

1. Create new keys at [console.groq.com](https://console.groq.com) and [aistudio.google.com](https://aistudio.google.com/apikey).
2. Replace `GROQ_API_KEY` and `GEMINI_API_KEY` in `.env`.
3. Restart the server, because `.env` is read at start-up.
4. Revoke the old keys in each provider's console.
5. Run `DIAGNOSIA_LIVE_TESTS=1 .venv/bin/python -m pytest -m live backend/tests/live/test_live_services.py`.

The app reads these only from the environment or from Settings, where they're stored encrypted.
