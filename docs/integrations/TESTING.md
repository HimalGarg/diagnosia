# Testing the integrations

Results must say what they ran against. A mock, a local stand-in and a real service prove different things.

| Level | Command | Against | Proves |
|---|---|---|---|
| Unit and mock | `.venv/bin/python -m pytest` | Fakes only | Logic. Never a connection |
| Local stand-ins | `LOCAL_MONGODB_URI=... LOCAL_POSTGRES_DSN=... .venv/bin/python -m pytest -m local_stack backend/tests/local_stack` | A local MongoDB and PostgreSQL | The real drivers and queries work. **Never a live result** |
| Live | `DIAGNOSIA_LIVE_INTEGRATIONS=1 .venv/bin/python -m pytest -m live backend/tests/live/test_live_integrations.py` | Real Atlas, YugabyteDB, Meko | The real service works |
| End to end | `python -m backend.tests.local_stack.end_to_end` | Whatever is configured | The journey, outages and recovery |
| Browser | `node backend/tests/browser/run_careflow.mjs <puppeteer-core> <port>` | A running server | The screen |

## Live tests can't be fooled

Each one checks what it's connected to first. The Atlas tests fail on any server outside `mongodb.net`, and the YugabyteDB tests fail unless the server identifies itself as YugabyteDB.

- No credentials: the test skips, with `BLOCKED_MISSING_CREDENTIALS` and the variable's name.
- Service switched on but credentials missing: the test fails, because they were expected.

## End-to-end run

```bash
DIAGNOSIA_DB=/tmp/e2e/e2e.db DIAGNOSIA_DOCUMENTS=/tmp/e2e/docs .venv/bin/python -m backend.tests.local_stack.end_to_end \
    --real-ai --mongo-container NAME --yugabyte-container NAME
```

It refuses a database without `e2e` in its path. With container names it stops those servers for real. Without them, the outage is simulated, which is the only safe way to do it against a cloud service.

## Browser

Run the demo, start a server, and pass `--expect-stream` when change streams are on, and `--mongo-container NAME` to test an outage. It checks signing in, events, filters, handoffs, labels, status, live updates, fallback and error states. Skin tests need `backend/requirements-skin.txt`.
