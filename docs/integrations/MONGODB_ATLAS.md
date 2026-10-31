# MongoDB Atlas setup

Atlas stores the CareFlow timeline. Everything in it can be rebuilt from the local outbox.

1. Create a cluster, and a database user limited to `readWrite` on `diagnosia_atlas`.
2. Allow your server's address under Network Access.
3. Install the driver: `pip install -r backend/requirements-integrations.txt`
4. Add to `.env`, never to code:

   ```dotenv
   ENABLE_MONGODB_ATLAS_INTEGRATION=1
   MONGODB_ATLAS_URI=
   ```

5. Restart. On first use, the app creates the collections, indexes and validator. Events already in
   the outbox are then sent, in order. Check the state in CareFlow, under Integration status.

For instant timeline updates, add `ENABLE_MONGODB_CHANGE_STREAMS=1`. Atlas clusters support this. Without
it, screens refresh every 15 seconds.

## How it's told apart from a local server

The app looks at the servers it actually connected to. Only hosts under `mongodb.net` count as Atlas, and a setting's name counts for nothing. Anything else shows on screen and in status as a local test server. There's no default address, so a bad URI fails rather than falling back to localhost.

`mongodb-health` checks the connection, the database, collections, indexes and validator, then writes a synthetic event, reads it back and removes it.

## What's created

| Collection | Holds |
|---|---|
| `care_events` | One document per event, with `_id` as the stable event ID, and a schema validator that rejects malformed documents |
| `timeline_projections` | One document per workflow: ordered event summaries, last activity, display-only activity status, pending review count and modules. Marked as an activity view only |
| `integration_delivery` | Delivery state for each event and destination: a view for operators. SQLite decides what's sent next |

Indexes: `(workflow_ref, occurred_at)`, `(tenant_ref, occurred_at)`, `(module, occurred_at)`, `(status, occurred_at)`, `(tenant_ref, last_activity_at)` on projections, and `(state, next_attempt_at)` and `(destination, state)` on delivery. `workflow_ref` is the pseudonymous case reference. `_id` already gives the unique event ID.

`ai_drafts` isn't created. Nothing in the app needs it, and draft text stays in the Diagnosia database.

## Delivery

An event is marked delivered only after MongoDB confirms it, with write concern `majority`. Failures retry with growing waits and jitter, and a document the validator refuses is set aside at once.

## Change Streams

With `ENABLE_MONGODB_CHANGE_STREAMS=true`, a background thread watches `MONGODB_CHANGE_STREAM_COLLECTION`. Its position is saved in SQLite after each change, so a restart carries on from the same place. A lost connection reconnects with growing waits. A position the server refuses is forgotten, and the stream starts fresh. An event seen twice is announced once, and shutdown closes the stream. Screens hear through server-sent events, and the 15-second refresh keeps working either way. That refresh is a fallback, not a change stream, and the status panel says which is running.

## If Atlas is down

Events wait in the outbox and retry. CareFlow shows the local copy, and says so. Nothing clinical is
affected. Run `python -m backend.operator_cli careflow-replay --channel mongodb` for events that ran
out of attempts.

## Retention

Nothing is deleted automatically, because that's a product decision about audit evidence, and hasn't
been made. Only synthetic events should be here.

## Try it locally

```bash
docker compose -f docker-compose.integrations.yml up -d mongodb
# ENABLE_MONGODB_ATLAS_INTEGRATION=1
# MONGODB_ATLAS_URI=mongodb://127.0.0.1:27017/?directConnection=true
```
