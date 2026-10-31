# Iteration audit

The state of the repository on 31 October 2026, before this iteration's changes.

## Baseline

| Check | Result |
|---|---|
| Branch and commit | `main`, at `3a06537`, with 36 uncommitted paths kept as they were |
| Main test suite | 1,281 passed, 24 live tests deselected |
| Credentials in `.env` | Groq and Gemini set. MongoDB Atlas, YugabyteDB and Meko all missing |
| Skin training | Stalled at 3.5 of 9.8 GB and 0.69 of 1.4 GB |

## Gaps found

| Area | Finding |
|---|---|
| Event coverage | "All 11 wired" overstated it. 23 write routes had no mapping, and events had no source operation or stage |
| Honest labels | `/health` said "connected" for any MongoDB, and any source was called Atlas |
| YugabyteDB | Never run on a real server. Nothing proved the server's identity |
| AI records | Timing was in memory, written only at the end of a run, and lost on restart |
| Meko | Missed the `User-Agent` the docs ask for, and the `initialized` notification MCP expects. Checked unknown arguments only. Knowledge upload was manual, although a documented tool exists |
| Change streams | No saved position, no clean shutdown, no duplicate protection, and a corrupt position would retry forever |
| Delivery | No jitter. A document the schema refuses blocked its queue until it ran out of attempts |
| Dashboard | No handoff history, and status was hidden behind a toggle |
| README | Described IOMETE, planned features that were never built, a handoff that doesn't exist, and stale providers |

## Vendor documentation checked

- **Meko:** MCP server reference, quick start, datapacks, knowledge bases, memory, and `llms.txt`. Confirmed the endpoint, Bearer key, `User-Agent` header, 28 tools and the retired `scope` argument. Confirmed `knowledgebase_upload_document` as the upload route, with the presigned reply's exact shape left unpublished.
- **YugabyteDB:** `version()` carries a `-YB-` build, and `yb_servers()` exists only on YugabyteDB.
- **MongoDB:** change stream resume tokens, and validators through `collMod`.

## LabLens to ClinScribe

Not a real path. `backend/clinscribe/drafting.py` reads the transcript alone. See [CAREFLOW_EVENTS.md](CAREFLOW_EVENTS.md).
