# Troubleshooting

Start with `.venv/bin/python -m backend.operator_cli integrations-status`.

| You see | It means | Do this |
|---|---|---|
| `BLOCKED_MISSING_CREDENTIALS` | The switch is on, and a variable is empty | Fill in the variable it names |
| `MONGODB_ATLAS: BLOCKED`, local test server | The URI points somewhere that isn't Atlas | Use your `mongodb+srv://...mongodb.net` address |
| `MONGODB_ATLAS: FAILED`, `ServerSelectionTimeoutError` | The cluster can't be reached | Add this machine's address under Atlas Network Access |
| `MONGODB_ATLAS: FAILED`, `OperationFailure` | Usually a wrong user or password | Check the database user in Atlas |
| `MONGODB_ATLAS: FAILED`, `InvalidURI` or `ConfigurationError` | The URI is malformed | Copy it again from Atlas. Encode special characters in the password |
| `YUGABYTEDB: FAILED`, not YugabyteDB | A plain PostgreSQL server answered | Check the DSN |
| `YUGABYTEDB: FAILED`, `PoolTimeout` or `OperationalError` | Unreachable, or refused | Check the host, port, password and TLS settings. Aeon needs `sslmode=verify-full` and its certificate |
| `MEKO: FAILED`, `MekoRejected` | Wrong key, or no access to that datapack | Create a key under Settings > API Keys. Keep the `mko_tkn_` prefix |
| `MEKO: FAILED`, `MekoUnsupported` | The server's tools changed | Run `meko-health`, and update `backend/integrations/meko.py` |
| Install message | The drivers are missing | `uv pip install -r backend/requirements-integrations.txt` |

## Events aren't reaching a destination

CareFlow's Integration status shows waiting, delivered and failed counts for each destination. Waiting events retry by themselves. Failed ones need this, after you've fixed the cause:

```bash
.venv/bin/python -m backend.operator_cli careflow-replay --channel mongodb
```

Replaying can't create duplicates.

## CareFlow says it's using the local copy

Atlas isn't answering, or isn't switched on. The screen still works. Events catch up by themselves when Atlas returns.

## Live updates are off

Change streams need `ENABLE_MONGODB_CHANGE_STREAMS=true`. Without them the page refreshes every 15 seconds, and the status panel says so.

## An AI run shows as interrupted

The process stopped during that call. It's recorded at the next start. Nothing else needs doing.

## Skin downloads

`python -m backend.skin.training.download_isic` resumes by itself, and checks the finished file against S3's ETag. Don't delete `ISIC_2019_Training_Input.zip` or its `.progress.json` while it's running.
