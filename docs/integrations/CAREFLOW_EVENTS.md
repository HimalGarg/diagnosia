# CareFlow events

The contract is `backend/careflow/contract.py`, and the code is the authority. Tables here are generated from it. Current schema version: `1.1`. Events written as `1.0` are upgraded when read, without changing what they say.

## Shape of an event

| Field | Meaning |
|---|---|
| `event_id` | Generated, and stable. It's also the MongoDB `_id`, which is what prevents duplicates |
| `schema_version` | The contract version |
| `event_type`, `module` | From the lists below. A module can only record its own |
| `workflow_stage` | `before_visit`, `during_visit` or `after_visit`. Old events from removed tools may say `programme` |
| `source_system`, `source_operation` | Always `diagnosia`, and the audit action or operation that produced this event |
| `source_ref` | The request ID, for tracing back to the audit log |
| `workflow_ref` | A generated `case_...` reference. The map to a patient stays in SQLite |
| `tenant_ref` | A one-way hash of the organization, or `tenant_individual` |
| `correlation_id` | Generated, the same across one workflow |
| `causation_id` | On a handoff, the event before it |
| `occurred_at` | UTC |
| `status` | `started`, `completed`, `review_required` or `failed` |
| `actor_type` | `clinician`, `patient` or `system`. Never a person's identity |
| `target_module` | Set only on a real handoff |
| `metadata` | Allowlisted keys only |
| `data_classification` | Always `synthetic_demo` |

**Metadata keys:** `review_required`, `urgent_stop`, `item_count`, `alert_count`, `question_count`, `duration_ms`, `model_provider`, `model_name`, `extraction_method`, `language`, `error_category`.

Events have no free-text fields. An unknown field, or a metadata value outside its rule, is refused and logged, and the tool carries on.

## What happens if delivery fails

The same for every tool. The event stays in the SQLite outbox, which committed with the change itself. The destination is retried with growing waits and jitter, up to 8 attempts over about an hour. Then the delivery is marked dead, stays visible in Integration status, and can be replayed with `operator_cli careflow-replay`. A document MongoDB's validator refuses is set aside at once, so it can't hold up the events behind it. Repeats are ignored, so a replay can't create a duplicate.

## Events by tool

### DiagnoFast

- **Operations:** Create a link, start an interview, answer, finish, write the report.
- **Stage:** `before_visit`
- **Trigger:** `backend/diagnofast/routes.py`
- **Human review:** Yes: the doctor reads the report.
- **Events:** `intake.completed`, `intake.failed`, `intake.link_created`, `intake.review_required`, `intake.started`, `intake.transcription.completed`

### Skin Cancer Detection

- **Operations:** Analyze an image.
- **Stage:** `during_visit`
- **Trigger:** `backend/skin/routes.py`
- **Human review:** Yes: every result.
- **Events:** `skin_analysis.completed`, `skin_analysis.failed`, `skin_analysis.review_required`, `skin_analysis.started`

### Prescription Analyzer

- **Operations:** Read photos and explain.
- **Stage:** `after_visit`
- **Trigger:** `backend/prescription/routes.py`
- **Human review:** Yes: the patient confirms each medicine.
- **Events:** `prescription.confirmation_required`, `prescription.explained`, `prescription.extraction.completed`, `prescription.processing.failed`, `prescription.processing.started`

### ClinScribe

- **Operations:** Sessions, recording state, transcript correction and deletion, drafts, sign-off.
- **Stage:** `during_visit`
- **Trigger:** Audit hook, and `backend/clinscribe/routes.py`
- **Human review:** Yes: a clinician signs.
- **Events:** `note_draft.approved`, `note_draft.created`, `note_draft.discarded`, `note_draft.failed`, `note_draft.updated`, `note_session.paused`, `note_session.recording`, `note_session.started`, `note_session.stopped`, `note_session.transcript_corrected`, `note_session.transcript_deleted`, `note_session.transcription_failed`



### LabLens

- **Operations:** Receive a report, extract, review results, add results, confirm.
- **Stage:** `during_visit`
- **Trigger:** Audit hook, and `backend/lablens/extract.py`
- **Human review:** Yes: a person confirms results.
- **Events:** `lab.extraction.completed`, `lab.extraction.failed`, `lab.extraction.retried`, `lab.extraction.started`, `lab.report_confirmed`, `lab.report_received`, `lab.result_added`, `lab.result_reviewed`, `lab.review_required`

### DischargeCare

- **Operations:** Episodes, checklist items, approval, closure.
- **Stage:** `after_visit`
- **Trigger:** Audit hook
- **Human review:** Yes: a clinician approves the plan.
- **Events:** `discharge.approved`, `discharge.checklist.updated`, `discharge.completed`, `discharge.episode_created`, `discharge.episode_updated`



### ReferralLoop

- **Operations:** The whole referral, from creation to closure.
- **Stage:** `after_visit`
- **Trigger:** Audit hook
- **Human review:** Yes: the referrer reviews the outcome.
- **Events:** `referral.accepted`, `referral.acknowledged`, `referral.appointment_offered`, `referral.attended`, `referral.completed`, `referral.contact_attempted`, `referral.created`, `referral.declined`, `referral.needs_information`, `referral.no_show`, `referral.outcome_returned`, `referral.priority_changed`, `referral.redirected`, `referral.rescheduled`, `referral.reviewed`, `referral.scheduled`, `referral.sent`

## Handoffs

No tool produces a handoff today. The earlier ones were LabLens to ChronicCare and Prescription Analyzer to MedGuard, and both tools have been removed. The support stays in the code for a future tool: an event may carry a `target_module`, which sends it on to YugabyteDB and Meko as well as MongoDB. Tests exercise it with synthetic events. A receiving tool always re-reads the source record.

**LabLens to ClinScribe isn't one.** `backend/clinscribe/drafting.py` builds a note from the consultation transcript alone, and doesn't read lab results. Making it read them would change what an AI puts in a clinical note, which is a change to clinical logic that needs clinical review first.

## Write routes that record no event

A test fails if a write route is neither mapped nor listed here.

| Route | Why |
|---|---|
| `add_typed_segment` | A line of transcript. Too small a step to show, and its content can't be recorded |
| `approve` | LOINC mapping governance, kept in the audit log |
| `correct` | LOINC mapping governance, kept in the audit log |
| `read_aloud` | Reads text aloud and stores nothing |
| `reject` | LOINC mapping governance, kept in the audit log |
| `transcribe_chunk` | Runs every 25 seconds while recording. Only a failure is recorded |

## Adding an event

1. Add the name to `EVENT_TYPES`.
2. If the step already writes an audit entry, add it to `AUDIT_EVENTS`. Otherwise call `events.emit` in the same transaction as the change, or `events.emit_separately` if there's no change to share.
3. Record success only after the work has succeeded. Generating something never counts as approval.
