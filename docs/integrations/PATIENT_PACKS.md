# Patient packs

A patient pack is one searchable view of a patient's finished records, across visits and tools. A doctor reads it as an AI brief with sources, and can ask it questions. A doctor who accepts a referral can open it too, which is how a patient's history reaches a new doctor.

**Made-up patients only.** Packs are off by default, and they stop completely when `ALLOW_REAL_PATIENT_DATA` is on. See "Before real patients".

## How it works

SQLite stays the record, and the only thing that decides access. Each patient has one Meko datapack, used as a search index.

1. A tool finishes something, and queues a reference to it in the same transaction. No text is queued.
2. The worker runs with CareFlow's delivery loop. It checks consent, reads the record, turns it into a document, scans it, and uploads it.
3. A doctor opens a pack. SQLite checks access, Meko finds what's relevant, and the records are **read again from SQLite** before they're shown or given to the AI.

So text that's stale in Meko can't reach a doctor, and text in one record can't point at another patient's. Original files stay in the encrypted vault. A referral copies nothing: access is worked out on each request, so ending it works at once.

Nothing flows back from Meko into the record.

## What goes in

| Source | When | Goes in | Stays out |
|---|---|---|---|
| ClinScribe | Note signed | The signed note | Drafts, and all transcripts |
| DiagnoFast | Report written, and the link has a patient record | The report, labelled as AI-written and unreviewed | Intakes with no record, answers, transcript and audio |
| LabLens | Report confirmed | Accepted and corrected results | Pending and rejected results |
| ReferralLoop | Outcome recorded | Summary and recommendations | |
| DischargeCare | Plan approved | Problems, procedures and instructions | Drafts, and the emergency contact |
| Documents, X-rays, doctors' prescriptions | Uploaded | Type, date and the typed description | The file and its filename |
| Prescription Analyzer | The patient presses "Save to my record" | Only the medicines they ticked, labelled as not checked by a clinician | Photos and unconfirmed medicines |

Documents carry no name, date of birth, phone number or ABHA number. Each item is scanned with the `FORBIDDEN` patterns in `integrations/knowledge.py`, and a match is held for a person, not cleaned. The scan finds patterns only, so a name inside free text can still get through.

Meko memory and conversations are never used. Memory rewrites text with an LLM and can drop details, and clinical text has to stay as written.

## Who can open a pack

| Who | Sees | Until |
|---|---|---|
| A verified doctor on the care team | Everything, including the brief and questions | They leave the care team |
| The doctor who accepted a referral | The same, plus original files | The referral closes, or the patient revokes `pack_sharing` |
| The patient | The list of what's in it, with no AI reading | Always |

Other staff at the receiving service can't open it, and neither can caregivers. Every open is audited. Without `pack_sharing`, a referral works exactly as it did: the receiver sees the referral and its chosen files.

## Consent

| Purpose | Allows | When revoked |
|---|---|---|
| `patient_pack` | Storing a copy with Meko, and sending records to our AI services when a doctor asks | Access ends at once, and the worker deletes the datapack |
| `pack_sharing` | A doctor who accepts a referral opening the whole pack | Access ends at once |

In demo mode both are recorded automatically for every patient, so every patient with a finished record uses a datapack slot, up to `MEKO_PACK_LIMIT`. Consent is checked when reading and again when sending. References are kept locally without consent, so earlier records follow if consent is given later.

## The brief and questions

- Allergies and saved medicines come straight from SQLite, with no AI and no search.
- The brief reads every finished record, newest first, up to about 24,000 characters, and says if older ones were left out. It doesn't use search, because search can miss things.
- Questions use Meko's search, then the records from SQLite.
- Each statement cites a record, and the doctor can open it. A statement with no real source, or with a number no record holds, is removed, and the doctor is told how many were. Anything else that doesn't line up is marked to check.
- Meko being down reads as "can't be reached", never as nothing found.

The AI never diagnoses, and never suggests a cause, test, treatment, medicine or dose. Record text is treated as data, so instructions inside a record are ignored. DiagnoFast's patient interview never reads a pack.

## Setup

```dotenv
MEKO_API_KEY=
ENABLE_MEKO_PATIENT_PACKS=1
# MEKO_PACK_LIMIT=6
```

Then record `patient_pack` consent on a patient. `python -m backend.operator_cli pack-rebuild --patient ID` queues a patient's records again.

## Limits

- **Datapacks.** Free allows 10 and Standard 50. Pro has no limit, at a custom price. **Deleting one doesn't give the slot back**, so `MEKO_PACK_LIMIT` counts every one ever created and defaults to 6. Past the limit, items are held, and the app carries on.
- **Rate.** Meko allows 60 requests a minute, so the worker sends one record every 5 seconds. A patient with 10 records takes about a minute to sync. A brief says how many are still syncing.
- **Images.** Meko can't search them, so an X-ray with no description says only that it exists. No AI reads X-rays.
- **Corrections.** Meko's status reply gives no document ID, so the old copy is found by searching for its tag before it's deleted.

## Before real patients

Don't switch this on for real patients until all of these are done:

- **An agreement with Meko.** Its public pages say nothing on encryption at rest, data region, training use, outside AI providers, or compliance such as DPDP or SOC 2. Its FAQ says "We don't anonymize or strip names automatically." Its connector privacy policy hasn't been read yet.
- **Deletion.** Meko doesn't state whether deleting reaches its backups.
- **Identifiers in free text.** The scan can't catch a name.
- **Cost.** A Pro quote, because one datapack per patient passes Standard's limit at 50 patients.
- The Groq and Gemini agreements in `docs/CLINICAL_GOVERNANCE.md`.

## Testing

`backend/tests/test_patient_pack.py` needs no keys. `FakeMcp` uses Meko's real tool schemas, saved in `backend/tests/meko_tools.json`.

```bash
DIAGNOSIA_LIVE_INTEGRATIONS=1 .venv/bin/python -m pytest -m live backend/tests/live/test_live_patient_pack.py
```

The live test checks those saved schemas against the server, then runs one made-up patient from a signed note to erasure on real Meko and real AI. It uses up one datapack slot each run.
