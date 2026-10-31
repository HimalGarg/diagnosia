# Production deployment checklist

A list of requirements, not a record of completion. Every item is **not done** unless the
readiness report says otherwise. Run `python -m backend.operator_cli readiness` for current status.

## Before any deployment, including a synthetic pilot

- [ ] Create a key ring outside the repository, and set `DIAGNOSIA_DOCUMENT_KEY_FILE`
- [ ] Store two offline copies of the key ring, held by named people
- [ ] Set `DIAGNOSIA_ENV` and `DIAGNOSIA_HTTPS=1`
- [ ] Confirm `ALLOW_REAL_PATIENT_DATA`, and `ENABLE_EXTERNAL_MESSAGING` are unset
- [ ] Bootstrap the first organization with `bootstrap-organization`
- [ ] Confirm the pilot banner shows

## Infrastructure

- [ ] Full-disk encryption on database, document and backup storage
- [ ] TLS with a current configuration
- [ ] Scheduled `backup`, stored off-site, encrypted
- [ ] Restore drill completed on a separate host
- [ ] Audit log shipped off the host
- [ ] Log retention set, including server access logs
- [ ] Restricted shell access, because `operator_cli` is powerful
- [ ] Monitoring and alerts for failures and failed reminder sweeps
- [ ] One application process, or a move away from SQLite. Sweeps run in-process

## Migrating an existing installation

- [ ] Copy the database, and test the upgrade on the copy first
- [ ] Expect existing staff to need verifying again
- [ ] `migrate-documents --dry-run`, then `migrate-documents`, then `verify-documents`
- [ ] Delete originals only under a named retention approval

## Security

- [ ] Independent penetration test
- [ ] Dependency vulnerability scan in CI
- [ ] Multi-factor authentication for staff
- [ ] Password change and reset
- [ ] Idle session timeout

## Clinical, legal and vendor

All 10 signoffs in `CLINICAL_GOVERNANCE.md`, each backed by a real document. Record one with:

```bash
python -m backend.operator_cli signoff --item <item> --signed-by "<name>" --role "<role>" \
    --document-reference "<reference>" --operator "<your name>"
```

- [ ] Every LOINC mapping reviewed against a named release
- [ ] LOINC notice copied into notices and terms
- [ ] Privacy notice and terms reviewed by counsel
- [ ] Retention periods set, and export and erasure built
- [ ] CDSCO classification decided, including the existing Skin Cancer Detection tool
- [ ] Skin model replaced before any paid use, and the medicine list's data permission confirmed
- [ ] Real ABHA access approved, or the DiagnoFast demo records stay labelled
- [ ] Data agreement signed with Groq, and zero data retention switched on in Groq's console
- [ ] Data terms with Google reviewed if a Gemini key stays set, on a plan that doesn't train on content. Otherwise remove the key
- [ ] Incident and breach procedures with named owners

## Only then

- [ ] `readiness` passes every check
- [ ] Set `ALLOW_REAL_PATIENT_DATA=1`. The application refuses to start if any check fails
