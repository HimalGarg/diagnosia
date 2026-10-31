# Production readiness audit

> **Update, 1 November 2026:** MedGuard, ChronicCare, MaternaCare and AMR Guard were removed from Diagnosia after this was written. It is a record of 31 October 2026 and still describes them. The current tools are in the README.

Baseline taken 31 October 2026, before hardening work began: 391 automated tests
passing, `test_skin.py` not runnable because `torch` was absent, `git status` showing
this work uncommitted on `main`, and the uncommitted `shorthand.py` change left alone.

Findings came from reading the code. Status of each fix is reported in
`DIAGNOSIA_READINESS_REPORT.md`, not here.

Severity: **S0** can directly harm a patient or expose records. **S1** weakens a
safeguard. **S2** is hygiene or an assurance gap.

## Identity and access

| ID | Severity | Finding | Files | Fix | Depends on |
|---|---|---|---|---|---|
| A1 | S0 | `POST /api/auth/register` lets anyone become an active `doctor` | `backend/auth.py` | Account states, staff begin `pending_verification` | Human verification |
| A2 | S0 | Any doctor can create an organization and become its admin | `backend/records/routes.py` | Organizations start unverified. Only verified ones can verify staff | Operator bootstrap |
| A3 | S1 | `practitioners.verified` exists but nothing enforces it, and profession is self-declared | `backend/records/schema.sql` | Record profession as verified by an administrator | |
| A4 | S1 | No login throttle | `backend/auth.py` | Limit failed logins per email | |
| A5 | S1 | Staff sessions last 14 days | `backend/auth.py` | Shorter staff sessions | |
| A6 | S1 | Suspension has no meaning: no state exists, and sessions persist | `backend/auth.py` | Suspended accounts lose sessions at once | |
| A7 | S2 | The dashboard's Change Password button does nothing | `web/dashboard.html` | Record as open work | |

## Clinical content

| ID | Severity | Finding | Files | Fix | Depends on |
|---|---|---|---|---|---|
| C1 | S0 | 20 unreviewed interaction rules produce high-severity alerts | `backend/medguard/engine.py`, `knowledge/interactions.json` | Rule table with approval states. Only approved rules alert | Pharmacist |
| C2 | S0 | "No alerts" could read as reassurance | `web/js/care/medguard.js` | Explicit coverage states | |
| C3 | S1 | No evidence source, version or reviewer stored for rules | `backend/medguard/knowledge/` | Provenance fields, append-only history | Pharmacist |
| C4 | S1 | LOINC codes written from memory are exported as real codings | `backend/lablens/analytes.json`, `backend/records/fhir.py` | Mapping review. Export codes only when approved | Lab professional |
| C5 | S1 | RxNorm and openFDA results carry no timestamp or validation, and are cached forever | `backend/medguard/references.py` | Validate, attribute, timestamp, expire | |
| C6 | S1 | AMR Guard's unavailable decision-support status is only a banner | `backend/amrguard/routes.py` | State it in the API and test the boundary | Microbiologist |
| C7 | S1 | Pharmacist, lab and steward roles can't be proven | `backend/records/` | Reviewer checks use administrator-verified profession | A1 |

## Privacy and documents

| ID | Severity | Finding | Files | Fix | Depends on |
|---|---|---|---|---|---|
| P1 | S0 | Reports stored in plaintext | `backend/records/documents.py` | AES-256-GCM envelope encryption, fail closed | Key custody |
| P2 | S1 | Uploads over 1 MB spool to a plaintext temp file | Starlette defaults | Keep uploads in memory, cap size | |
| P3 | S1 | Exception text is logged, and can hold patient text | `backend/app.py` | Log type and request ID only | |
| P4 | S1 | API responses have no `no-store`, so clinical data can sit in a browser cache | `backend/app.py` | Cache headers | |
| P5 | S1 | Terms link to a privacy policy that doesn't exist | `web/terms_of_service.html` | Draft notice, flagged for legal review | Legal |
| P6 | S1 | The SQLite database is plaintext | `backend/storage/` | Disk encryption, encrypted backups | Infrastructure |
| P7 | S2 | Original filenames, which may hold names, are stored in the database | `clinical_documents.filename` | Not on disk. Covered by P6 | |
| P8 | S2 | `referral_sharing` was enforced, but `maternal_care` revocation changed nothing | `backend/maternacare/routes.py` | Enforce on clinical actions | |

## Reliability and integrations

| ID | Severity | Finding | Files | Fix | Depends on |
|---|---|---|---|---|---|
| R1 | S0 | Reminders reach only people with the app | `backend/records/care.py` | Channel-independent reminders, staff follow-up on failure | Vendors for SMS and voice |
| R2 | S1 | A malformed Sarvam reply raises an unhandled error | `backend/speech.py` | Validate and retry within limits | |
| R3 | S1 | Anthropic client uses library default timeouts | `backend/llm.py` | Explicit timeout and retries | |
| R4 | S1 | The last audio chunk is lost on pause or stop, because the state changes before it uploads | `web/js/care/clinscribe.js` | Wait for the final chunk | |
| R5 | S1 | No schema version or migration history | `backend/db.py` | Numbered, atomic migrations | |
| R6 | S1 | No backup or restore tooling | | Encrypted backup and restore | Key custody |
| R7 | S2 | LabLens only tested on two simple synthetic layouts | `backend/tests/` | Synthetic format dataset with field accuracy | Real de-identified reports |
| R8 | S2 | `test_skin.py` never run | | Optional environment | |

## Real database

| ID | Severity | Finding |
|---|---|---|
| D1 | S2 | The dev server on port 8001 runs with `--reload`, so it applied migrations 1 to 5 to `backend/storage/diagnosia.db` during this work. They were additive and removed no rows |
| D2 | S1 | That database holds 1 patient account and 1 PNG uploaded on 31 October at 04:41 IST, stored unencrypted. It was not opened, moved or changed. Run `migrate-documents` once keys exist |

## Regulatory

CDSCO classification, DPDP, vendor agreements, the LOINC notice and ABDM are assessed
in `CLINICAL_GOVERNANCE.md`. None can be settled by software.
