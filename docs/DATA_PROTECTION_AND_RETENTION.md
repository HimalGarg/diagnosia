# Data protection and retention

This describes what the code does. Retention periods are a policy decision that has not
been made. Replaces `DATA_RETENTION_AND_CONSENT.md`.

## Consent purposes

Each is checked by code when it matters, and revoking takes effect at once. Every grant
keeps who recorded it, how, when, and the policy version (`2026-10-draft`). Consents are
revoked, never deleted.

| Purpose | Checked by | Without it |
|---|---|---|
| `ai_scribe` | ClinScribe, at session start, recording, each audio chunk, and drafting | Won't start or continue |
| `transcript_retention` | ClinScribe, on signing | Transcript is deleted on signing |
| `ai_processing` | LabLens, before AI reads a scan | The report waits for manual entry |
| `referral_sharing` | ReferralLoop, on sending and on every receiver access | Can't send, and the receiver loses access |
| `external_messaging` | Reminders, before any SMS or voice use | That channel isn't used |
| `patient_pack` | Patient packs, on sending and on every brief or question | Nothing is sent, and revoking deletes the datapack |
| `pack_sharing` | Patient packs, on every open by a referred-to doctor | The doctor sees only the referral, and loses the pack at once |

Prescription photos keep their per-upload consent, and a DiagnoFast patient ticks a consent box before the interview starts, because they have no account or record to hold it. Consent recorded by a clinician is kept
distinct from consent given in the app.

## What is stored

| Data | Where | Encrypted by the application? | Kept for |
|---|---|---|---|
| Lab reports and documents | `settings.documents_dir`, under random names | **Yes**, AES-256-GCM | Not set |
| Database, including names, results and notes | SQLite file | **No.** Needs disk encryption | Not set |
| Original filenames | Database | No | Not set |
| Consultation audio | Not stored, sent to Groq | n/a | None on our side |
| DiagnoFast recordings | `settings.documents_dir/intake-audio` | **Yes**, AES-256-GCM | Not set |
| DiagnoFast answers, transcripts and reports | Database | No | Not set |
| Transcripts | Database | No | Until signing, unless retention agreed |
| Prescription and skin photos | Not stored | n/a | None |
| Medicines a patient saved from a prescription | Database | No | Not set |
| Patient packs: finished records as text | Meko, in one datapack per patient | Not stated by Meko | Until consent is revoked. Meko doesn't state whether deletion reaches its backups |
| Audit events | Database | No | Not set |
| CareFlow outbox and reference map | Database | No | Not set |
| Pre-migration copies | Next to the database, owner-only | No | Remove after checking |
| Groq or Gemini API key, if saved in Settings | Database | **Yes**, with the document key ring | Until replaced |

## Document encryption

`backend/records/vault.py`. Each file gets its own AES-256-GCM key, wrapped by a master key
from a key ring. The authenticated data ties a file to its patient and document, so a file
moved to another record won't open. A SHA-256 check confirms content on every read.

- **Keys** live in the file named by `DIAGNOSIA_DOCUMENT_KEY_FILE`. It is refused if it is
  in the repository or beside the database or documents, or readable by anyone but its owner.
- **No keys means no storage.** Uploads return 503 in every environment, with no fallback.
  There is no separate development mode.
- **Files on disk**, including DiagnoFast recordings, are owner-only, written atomically, and hold no patient information in
  their names. Uploads aren't written to temporary files by this application.

### Commands

```bash
python -m backend.operator_cli generate-key --path ~/.diagnosia/document-keys.json
python -m backend.operator_cli add-key --key-id k2          # new active key
python -m backend.operator_cli rewrap-documents             # move files to it
python -m backend.operator_cli retire-key --key-id k1       # refused while in use
python -m backend.operator_cli verify-documents             # check every file
python -m backend.operator_cli backup --to /secure/path
python -m backend.operator_cli restore --source ... --database ... --documents ...
```

### Key recovery

**Losing the key ring means losing every document.** There is no recovery. Keep two offline
copies in separate places, held by named people, never with the backups. A real deployment
should move master keys into a managed key service. Backups need the key ring that was
current when they were made, so keep retired keys for as long as those backups exist.

### Backups

`backup` encrypts the database and copies the already-encrypted documents, with a manifest of
hashes. It never includes keys. `restore` checks every hash, and writes only to new paths.
Tested with synthetic data. Automated scheduling and off-site storage are not set up.

### Migrating existing files

```bash
python -m backend.operator_cli migrate-documents --dry-run
python -m backend.operator_cli migrate-documents
python -m backend.operator_cli verify-documents
python -m backend.operator_cli delete-migrated-originals --retention-approval "REF"
```

Migration checks each original's hash, writes the encrypted copy, decrypts it to prove it,
and only then updates the record. Originals stay until a named approval is given, and each
copy is re-proved before deletion. Failures are listed and leave that document unchanged.

## What leaves our servers

| Sent to | What | When |
|---|---|---|
| Groq, in the United States | Consultation audio, transcript text, DiagnoFast answers and recordings, prescription photos, images of unreadable lab reports, English text read aloud | With consent, or on request |
| Google Gemini, in the United States or elsewhere | The same text and images, but never audio, only when Groq fails or Settings say providers take turns, and only if a Gemini key is set | When a Gemini key exists |
| MongoDB Atlas, YugabyteDB and Meko, only if switched on | Activity events, technical AI timings and handoff notes. No names, results, notes or images | Synthetic data only. CareFlow stops recording if real patient data is allowed |
| Meko, only if packs are switched on, hosted by Yugabyte | Finished records as text, including results and notes. No names, contact details, images or files | Synthetic data only, with `patient_pack` consent. Packs stop if real patient data is allowed. See `integrations/PATIENT_PACKS.md` |

Whether Groq may receive real patient data is covered in `CLINICAL_GOVERNANCE.md`. Today
the answer is no.

## Website wording

- `web/privacy.html` is new, because the terms linked to a privacy policy that didn't exist.
  It is a factual draft, marked for legal review.
- `web/terms_of_service.html` now links to it. **For legal review:** it calls the tools
  "educational", which conflicts with clinical use, and promises a privacy policy.

## Required before real use

1. Set retention periods for each data type.
2. Build whole-record export and erasure.
3. Encrypt the disk and backups, and record the `infrastructure_encryption` signoff.
4. Schedule backups, and test restoring them.
5. Write a breach-response procedure.
6. Have counsel review the privacy notice and terms.
