# Integration audit

> **Update, 1 November 2026:** MedGuard, ChronicCare, MaternaCare and AMR Guard were removed from Diagnosia after this was written. It is a record of 31 October 2026 and still describes them. The current tools are in the README.

The repository as found on 31 October 2026, before adding CareFlow, MongoDB, YugabyteDB and Meko.

## What the repository really is

| The brief assumed | What's actually here | What was done |
|---|---|---|
| A React frontend | Plain JavaScript modules, no build step, in `web/js/care/` | CareFlow is one more module, using the existing helpers |
| An SSE or WebSocket pattern | None exists | One small SSE route, with a 15-second refresh as the fallback |
| A background worker service | One in-process loop, started in `backend/app.py` | A second loop beside it |
| Multi-tenant workspaces | Care teams and organizations. Patients aren't owned by a tenant | Access is decided per patient by the existing gate, and `tenant_ref` is a label |
| Gemini for everything | Groq and Gemini, with reading tools pinned to Gemini | Left alone. Events name the provider that answered |

## Sources of truth

| Data | Owner | New integrations |
|---|---|---|
| Identity, login, roles | SQLite: `users`, `sessions` | Never copied |
| Consent and care teams | SQLite: `consents`, `care_team` | Read on every CareFlow request, never stored elsewhere |
| Clinical records and documents | SQLite and the encrypted document store | Never copied |
| Official workflow status and approvals | SQLite, in each tool's tables | Never written to by an integration |
| The map from reference to patient | SQLite: `careflow_workflows` | Stays here, which is what keeps references pseudonymous |
| Events waiting to be sent | SQLite: `careflow_outbox`, `careflow_deliveries` | The durable source for delivery |
| Cross-tool activity and timelines | MongoDB Atlas: `care_events`, `timeline_projections` | An activity view only |
| AI run and handoff records | Your own YugabyteDB, through YSQL | Technical data only |
| Shared workflow knowledge and handoff notes | A Meko datapack | Supplemental context only |

If a store disagrees with SQLite, SQLite is right. Nothing flows back the other way.

## Module map

All 11 tools exist and are wired. Event names are in [CAREFLOW_EVENTS.md](CAREFLOW_EVENTS.md).

| Tool | Code | Events come from |
|---|---|---|
| DiagnoFast | `backend/diagnofast/routes.py` | Explicit calls, including the patient's link routes |
| Skin Cancer Detection | `backend/skin/routes.py` | Explicit calls |
| Prescription Analyzer | `backend/prescription/routes.py` | Explicit calls, and the audit entry for an import to MedGuard |
| ClinScribe | `backend/clinscribe/routes.py` | Audit entries, and an explicit call after the AI draft |
| MedGuard | `backend/medguard/routes.py` | Audit entries, and an explicit call for waiting alerts |
| ChronicCare | `backend/chroniccare/routes.py` | Audit entries |
| LabLens | `backend/lablens/routes.py`, `extract.py` | Audit entries, and explicit calls in the extraction job |
| DischargeCare | `backend/dischargecare/routes.py` | Audit entries |
| MaternaCare | `backend/maternacare/routes.py` | Audit entries |
| AMR Guard | `backend/amrguard/routes.py` | Audit entries, against the organization |
| ReferralLoop | `backend/referralloop/routes.py` | Audit entries |

## Key finding: the audit call is the right hook

Every meaningful action already writes an audit row inside the request's transaction, and a failed
request rolls that transaction back. Adding the outbox write there gave most tools a true
transactional outbox with almost no change to their code, and a success event can't outlive a
failure. Explicit calls cover what isn't audited, such as failures and the tools with no database write.

## What existed already

No MongoDB, YugabyteDB, Meko, outbox or event code was present. `pymongo` and `psycopg` were new, and
are optional, in `backend/requirements-integrations.txt`. Meko uses `httpx`, already a dependency.
