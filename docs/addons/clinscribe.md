# ClinScribe

**Status:** pilot. Audio capture tested in Chrome with a fake device. Live Groq untested, and no vendor agreement exists. Last audio chunk now kept on pause or stop.

Turns a consultation into a draft note that a clinician checks and signs.
Code: `backend/clinscribe/`, `web/js/care/clinscribe.js`. Tests: `test_clinscribe.py`.

## Workflow

1. A clinician opens a session on an in-progress visit. `ai_scribe` consent is required.
2. The session starts paused. Recording begins only on a click, with a visible state.
3. The browser sends 25-second audio chunks to Groq's Whisper, or lines can be typed if audio fails.
4. After stopping, the model chosen in Settings drafts statements in a SOAP or history-and-physical template.
5. The verifier checks each statement against the transcript and flags problems.
6. The clinician edits and signs. Open flags must be acknowledged. A signed note is final.
7. The transcript is deleted on signing unless `transcript_retention` was agreed.

States: session `paused`, `recording`, `stopped`. Note `draft`, `approved`, `discarded`.

## Data

`scribe_sessions`, `transcript_segments`, `clinical_notes`, and
`clinical_note_versions`, which keeps every version, AI and clinician. Each note
records the model, prompt version and verifier version.

## API

Under `/api/addons/clinscribe`: `POST encounters/{id}/sessions`,
`POST sessions/{id}/state`, `POST sessions/{id}/transcribe`, `POST sessions/{id}/segments`,
`PATCH segments/{id}`, `POST sessions/{id}/draft-note`, `GET`/`PATCH notes/{id}`,
`POST notes/{id}/approve`, `POST notes/{id}/discard`, `GET notes/{id}/fhir`,
`DELETE sessions/{id}/transcript`.

## Safety

- Only the clinician who held the consultation can sign. Patients never see a draft.
- The verifier flags missing numbers, changed units, lost negation, swapped side,
  and unsupported text. AI summaries always need confirming.
- Hindi transcripts can't be word-matched, so lines are marked for manual comparison,
  while numbers are still checked.

## Limits

Speakers are labelled by hand, there are no per-segment timestamps or confidence
scores, and there is no PDF export. Long silences still send chunks. The verifier was
tested on synthetic sentences only.
