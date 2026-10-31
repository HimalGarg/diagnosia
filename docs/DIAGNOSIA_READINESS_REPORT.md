# Diagnosia readiness report

> **Update, 1 November 2026:** MedGuard, ChronicCare, MaternaCare and AMR Guard were removed from Diagnosia after this was written. It is a record of 31 October 2026 and still describes them. The current tools are in the README.

Dated 31 October 2026, updated the same day after moving to Groq, building local setup, and merging the DiagnoFast, Prescription and Skin work into one product. Technical implementation, clinical validation and legal approval are
reported separately, because passing tests says nothing about the other two.

## 1. Executive summary

**Decision: NO-GO for real patients.** The technical safeguards are much stronger, but no
clinical, legal, vendor or independent security approval exists, and most readiness checks
fail. The application refuses to start if asked to accept real patient data in this state.

**A synthetic-data pilot is reasonable.** `setup-local` builds a working demo with one command, and
the tools work end to end on it, in one workspace. AI runs on Groq with Google Gemini as a second provider. Both were tested live with synthetic data.

What changed: staff can no longer self-activate, unreviewed medication rules can't raise clinical
alerts, unreviewed LOINC codes stay out of exports, documents are encrypted and fail closed, a
reminder that can't be confirmed becomes staff work, and the system says what it didn't check.

## 2. Original blockers and status

| Blocker | Technical work | What remains |
|---|---|---|
| Anyone could register as a doctor | `IMPLEMENTED_AND_TESTED` | `BLOCKED`: no real organization or administrator exists yet |
| MedGuard unreviewed rules | `IMPLEMENTED_AND_TESTED` | `PENDING_CLINICAL_REVIEW`: pharmacist, 0 of 21 approved |
| LOINC mappings | `IMPLEMENTED_AND_TESTED` | `PENDING_CLINICAL_REVIEW`: 0 of 58 approved |
| LOINC licence notice | `PENDING_TECHNICAL_WORK` | `PENDING_LEGAL_REVIEW`: the exact text couldn't be read |
| AMR Guard clinical boundary | `IMPLEMENTED_AND_TESTED` | `PENDING_CLINICAL_REVIEW`: method and vocabulary |
| Lab report encryption | `IMPLEMENTED_AND_TESTED` | `PENDING_TECHNICAL_WORK`: database and disk encryption, key custody |
| Reminders without a smartphone | `IMPLEMENTED_AND_TESTED` for in-app and staff follow-up | `PENDING_VENDOR_APPROVAL`: SMS, voice |
| Pilot controls and readiness check | `IMPLEMENTED_AND_TESTED` | None for the mechanism |
| CDSCO | Not a software task | `PENDING_LEGAL_REVIEW` |
| DPDP | `IMPLEMENTED_NOT_FULLY_TESTED`: consent and access exist, erasure and export don't | `PENDING_LEGAL_REVIEW` |
| AI vendors, Groq and Gemini | `IMPLEMENTED_AND_TESTED`: mocks, plus live runs with synthetic data on both, including a real fallback | `PENDING_VENDOR_APPROVAL` |
| Tools not usable end to end | `IMPLEMENTED_AND_TESTED`: setup, Settings, tool switches, demo journey | None for the mechanism |
| DiagnoFast, Prescription and Skin merged in | `IMPLEMENTED_AND_TESTED`: a Hindi interview and a prescription read and explained through the app, on real Groq and Gemini | Hindi speech quality, and accuracy on real prescriptions |
| Patient recordings stored unencrypted | `IMPLEMENTED_AND_TESTED`: encrypted, in backups and key rotation | Retention period |
| Separate site, tool pages and workspace | `IMPLEMENTED_AND_TESTED`: one catalogue, one workspace, one sign-in, checked in Chrome at three widths | Testing with real staff and patients |
| ABDM | Not built | `PENDING_TECHNICAL_WORK` |
| Microphone | `IMPLEMENTED_NOT_FULLY_TESTED`: Chrome with a fake device | Real devices and browsers |
| Indian lab reports | `IMPLEMENTED_NOT_FULLY_TESTED`: synthetic layouts | `PENDING_CLINICAL_REVIEW`: real de-identified reports |
| Database migration and recovery | `IMPLEMENTED_AND_TESTED` | Restore drill on real infrastructure |

## 3. Files and modules changed

**New:** `backend/migrations.py`, `backend/safety.py`, `backend/operator_cli.py`,
`backend/records/staff.py`, `backend/records/vault.py`, `backend/medguard/rules.py`,
`backend/lablens/mappings.py`, `backend/reminders/`, `web/js/care/governance.js`,
`web/js/care/reminders.js`, `web/privacy.html`, `pytest.ini`, and nine test modules with
`backend/tests/lab_validation/`, `live/` and `browser/`.

**Changed:** `backend/auth.py`, `app.py`, `db.py`, `config.py`, `llm.py`, `speech.py`,
`backend/records/` (access, care, documents, fhir, routes, schema), `backend/medguard/` (engine,
references, routes, schema), `backend/lablens/` (extract, parser, routes, schema),
`backend/referralloop/routes.py`, `backend/maternacare/routes.py`, `backend/amrguard/routes.py`,
the workspace pages and scripts, `web/login.html`, `web/terms_of_service.html`, and test fixtures.

`backend/tests/test_skin.py`, `test_auth.py`, `test_medguard.py`, `test_lablens.py`,
`test_referralloop.py` and `test_amrguard.py` changed where staff must now be verified, or rules
approved. No assertion was removed. The FHIR test now checks both unapproved and approved codes.

**Merged from Himal's commit 892cd94:** DiagnoFast, the medicine list check, several-photo reading,
the skin classifier and metrics, his tests and his pages. His Gemini `llm.py`, Sarvam `speech.py`,
`sarvam_first_call.py` and the `google-genai` and `sarvamai` packages were left out, because Groq is
the chosen provider. His three working pages were rebuilt as workspace views and now redirect there.

**`backend/prescription/shorthand.py`:** your `PATTERN` line is kept exactly. Himal's additions around
it (`MARK`, `COLUMNS` and the rest) were merged in, and both sets of tests pass.

Nothing was committed or pushed. Git's `HEAD` is still `75697cc`, so this all shows as uncommitted
work on top of it.

## 4. Security findings

Full list in `SECURITY_REVIEW.md`. Fixed: self-activating doctors, self-made administrator
organizations, patient text in logs, cacheable API answers, plaintext temp uploads, no login
throttle, long staff sessions, unenforced maternal consent revocation.

Open: no staff multi-factor authentication, no password change or reset, plaintext database,
audit log not shipped off-host, and no independent penetration test.

## 5. Clinical safety findings

- MedGuard performs **no interaction check today**, because nothing is approved. It says so.
  This is the correct state, and it reduces function compared with the last release.
- List comparisons for shared ingredients and allergies stay on, using an unreviewed drug list.
- A real bug was found in LabLens: two-column reports silently lost the right column. It's fixed,
  and those lines now always go to a person.
- The last audio chunk used to be lost on pause or stop. Fixed.
- Pressing Stop while the browser was still opening the microphone left it running. Fixed, and
  tested by forcing that timing.
- Whisper can invent text from silence, so segments it marks as non-speech are dropped.
- Reminders don't replace clinical escalation, and maternal escalation rules need
  maternal-health professionals.
- DiagnoFast's urgent stop works in the browser, but the list of urgent symptoms comes from the AI's
  judgement and nobody clinical has reviewed it. It needs the clinical safety lead.
- Silence that Whisper drops is now asked again, rather than saved as an empty answer.
- Reading prescriptions and lab photos now prefers Gemini. On **synthetic** photos it beat Groq's vision
  model (see `backend/tests/ai_eval/README.md`), but those photos are easier than real handwriting, and
  no real prescription or report was available. **Real accuracy is still unknown.** The best Gemini
  model often hits its free limit, so the smaller one does much of the work.
- Hindi speech quality is untested. A four-photo prescription is read in two requests, so a
  bracket spanning photos 3 and 4 could be missed. The patient still confirms every medicine.
- The homepage's review image was removed. It praised features that don't exist, such as a BMI
  calculator and posture analysis.

## 6. Privacy and regulatory findings

Assessments for qualified reviewers, in `CLINICAL_GOVERNANCE.md`.

- **CDSCO:** the final guidance's examples closely match the **existing Skin Cancer Detection**
  tool, and ChronicCare, MedGuard and DiagnoFast are candidates. Nothing is determined.
- **DPDP:** sources report main obligations around May 2027, but a proposal to shorten this to
  about 13 November 2026 could not be confirmed either way. **Counsel should check this now.**
  The Gazette itself was not read.
- **Gemini:** a second vendor for the same content, whenever its key is set. Terms weren't checked. A free key may let Google use content. See `CLINICAL_GOVERNANCE.md` C2.
- **Groq:** data goes to the United States, there is no signed agreement, zero retention must be
  switched on in Groq's console, and its training position wasn't stated on the page read. The
  vision model and English voice are preview. **Do not send real data.**
- **LOINC:** the required notice isn't in the product, because the licence page couldn't be read.
- **ABDM:** exports aren't compliant. DiagnoFast's ABHA records are simulated and labelled demo data.
- **Licences:** PanDerm and ISIC 2019 are non-commercial. The medicine list's data permission is
  unconfirmed. Details are in `LICENCE_AND_DEPENDENCY_AUDIT.md`.
- **Terms:** they call the tools "educational", which conflicts with clinical use.

### Real database on this machine

- The dev server on port 8001 runs with `--reload`, so it **applied migrations 1 to 5 to
  `backend/storage/diagnosia.db`** during this work. They add columns and tables, and rebuild one
  table. No rows were lost, but this happened without a separate copy being taken.
- `setup-local` was then run here, at the owner's request for a working platform. It added two
  lines to `.env`, created `~/.diagnosia/`, and added demo accounts, organizations and synthetic
  patients to that database. It approved nothing clinical.
- That database holds 1 patient account and 1 PNG uploaded on 31 October at 04:41 IST,
  **stored unencrypted**, state `plaintext_legacy`. It was not opened, moved or changed.
- The old PNG is still plaintext. Run `migrate-documents` to encrypt it.
- The same dev server **also applied migration 6** during the merge, adding one column and one index
  to the new `intakes` table. That table holds no rows. A copy is at
  `backend/storage/diagnosia.db.pre-migration-20261031T101224Z.bak`.

## 7. Test results

| Suite | Result |
|---|---|
| Standard automated suite | **1,389 passed**, 47 deselected because they run separately |
| `test_skin.py`, in a separate environment with torch | **21 passed** |
| Live public services and AI | RxNorm and openFDA passed. Groq and Gemini: see section 8 |
| CareFlow | Real YugabyteDB: 7 of 7. Local MongoDB and PostgreSQL stand-ins: 16 passed. Chrome: 19 of 19. Not Atlas or Meko |
| Microphone in Chrome | 30 of 30 |
| Public site, at 1280, 768 and 390 pixels | No horizontal overflow, FAQ opens, no page errors |
| Every tool opened in the workspace with a patient open | Patient kept across all of them, no page errors |
| DiagnoFast in Chrome, against a stand-in Groq server | Link from the chart, Hindi browser voice, typed answers, report in the chart, urgent patient first |
| Prescription in Chrome, against the stand-in | Confirm, explain, unconfirmed medicine withheld, Hindi browser voice |
| Skin in Chrome | Only the "not installed" message. **The model has no weights here, so no image was analysed** |

The earlier failure, `test_temperature_scaling_fixes_overconfidence`, no longer fails after Himal's
changes to the training code and that test.

The optional environment is `backend/requirements.txt` plus `backend/requirements-skin.txt`.

Automated browser checks caught problems that unit tests missed, including a crash in the skin tool
on a machine without torch. Treat new test claims with care: the DiagnoFast and prescription browser
journeys were scripted for this work, and aren't in the repository.

## 8. External integrations actually tested

| Integration | Tested against the real service? |
|---|---|
| RxNorm | **Yes**, on 31 October 2026 |
| openFDA | **Yes**, on 31 October 2026 |
| Groq chat and vision | **Yes**, 31 October 2026, synthetic data: strict output, images, Hindi interview turns |
| Groq speech to text | **Yes**, accepted a synthetic tone. Not tried on real speech |
| Groq English voice | **No.** Returned 400 until the account owner accepts the model's terms in the Groq console. The browser voice covers it meanwhile |
| Gemini chat and images | **Yes**, same date and data, including a full DiagnoFast interview and a prescription read |
| YugabyteDB | **Yes**, a real server in Docker, not a cloud cluster |
| MongoDB Atlas, Meko | **No.** No credentials. See `integrations/FINAL_COMPLETION_REPORT.md` |
| Fallback between them | **Yes.** A wrong Groq key was answered by Gemini, and a real Groq rate limit was too |
| Real microphone | **No.** Chrome's fake device |
| SMS and voice | **No.** Not connected |
| ABDM | **No** |

## 9. Known limitations

- SQLite with one application process, and sweeps run in-process.
- Reminders can't reach anyone by SMS or voice.
- No whole-record export or erasure.
- Lab accuracy on real reports is unknown, and AI reading of scans is unmeasured.
- Firefox, Safari and mobile browsers untested for recording.
- No retention periods set.
- Follow-up tasks go to the person who scheduled the reminder.
- Governance review screens were rendered, but pharmacist and laboratory actions in them were
  tested through the API, not by clicking in a browser.
- `setup-local` is for development only. A real clinic has no in-app setup: its first administrator
  is created from the command line, on purpose.
- DiagnoFast has no rate limit on patient links, and no retention period for recordings.
- The chart links to a DiagnoFast report, but its answers aren't copied into ClinScribe or the
  medicine list. A person carries them across. Skin results and explained prescriptions aren't
  saved to the record at all.
- Each tool has one model. A busy model gets one retry, with no fallback to a second model.
- `terms_of_service.html` hasn't been reviewed for the new tools.
- CareFlow records nothing if real patient data is allowed. MongoDB, YugabyteDB and Meko would each need their own agreement first.

## 10. Remaining human approvals

13 signoffs, all missing, listed in `CLINICAL_GOVERNANCE.md`: clinical safety lead, pharmacist,
laboratory professional, obstetric team, microbiologist, CDSCO determination, DPDP review, Groq
agreement, Gemini agreement, infrastructure encryption, penetration test, incident response, and
LOINC notice.

## 11. Deployment prerequisites

See `PRODUCTION_DEPLOYMENT_CHECKLIST.md`. In short: key ring and custody, disk and backup encryption,
TLS, off-host audit logs, multi-factor authentication, penetration test, every signoff, and every
readiness check passing.

## 12. Final go/no-go assessment

| Question | Assessment |
|---|---|
| Technical safeguards in place? | Largely, with the open items above |
| Clinically validated? | **No** |
| Legally approved? | **No** |
| Vendors approved for patient data? | **No** |
| Independently security tested? | **No** |
| Real patient use | **NO-GO** `BLOCKED` |
| Synthetic-data pilot | **GO**, after key ring and bootstrap, with staff told it is a pilot |

Passing tests doesn't make Diagnosia ready for patients. The next most valuable steps are a
regulatory opinion on Skin Cancer Detection, a pharmacist's review of MedGuard, and a first real run
with a Groq key, including a Hindi interview and a real synthetic prescription photo.
