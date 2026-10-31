CREATE TABLE IF NOT EXISTS scribe_sessions (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    encounter_id TEXT NOT NULL REFERENCES encounters (id) ON DELETE CASCADE,
    clinician_id INTEGER NOT NULL REFERENCES users (id),
    consent_id TEXT NOT NULL REFERENCES consents (id),
    state TEXT NOT NULL DEFAULT 'paused' CHECK (state IN ('recording', 'paused', 'stopped')),
    language TEXT NOT NULL DEFAULT 'en-IN',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    transcript_deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES scribe_sessions (id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    speaker TEXT NOT NULL DEFAULT 'unknown' CHECK (speaker IN ('clinician', 'patient', 'other', 'unknown')),
    text TEXT NOT NULL,
    language TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('speech', 'typed')),
    recorded_at TEXT NOT NULL,
    corrected INTEGER NOT NULL DEFAULT 0,
    UNIQUE (session_id, sequence)
);

CREATE TABLE IF NOT EXISTS clinical_notes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES scribe_sessions (id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    encounter_id TEXT NOT NULL REFERENCES encounters (id) ON DELETE CASCADE,
    clinician_id INTEGER NOT NULL REFERENCES users (id),
    template TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'discarded')),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    verifier_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_at TEXT
);

-- Each save is a new row, so what the AI wrote and what the clinician changed both survive.
CREATE TABLE IF NOT EXISTS clinical_note_versions (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL REFERENCES clinical_notes (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    author_type TEXT NOT NULL CHECK (author_type IN ('ai', 'clinician')),
    author_id INTEGER,
    author_name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (note_id, version)
);
