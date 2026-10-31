CREATE TABLE IF NOT EXISTS discharge_episodes (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    encounter_id TEXT REFERENCES encounters (id) ON DELETE SET NULL,
    admitted_on TEXT,
    discharge_date TEXT NOT NULL,
    destination TEXT NOT NULL,
    responsible_clinician_id INTEGER NOT NULL REFERENCES users (id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'closed')),
    problems TEXT NOT NULL DEFAULT '',
    procedures TEXT NOT NULL DEFAULT '',
    emergency_contact TEXT NOT NULL DEFAULT '',
    reconciliation_id TEXT REFERENCES medication_reconciliations (id) ON DELETE SET NULL,
    flagged INTEGER NOT NULL DEFAULT 0,
    approved_by INTEGER,
    approved_at TEXT,
    closed_by INTEGER,
    closed_at TEXT,
    close_exception TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS discharge_patient ON discharge_episodes (patient_id, status);

CREATE TABLE IF NOT EXISTS discharge_instructions (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES discharge_episodes (id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    text TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en-IN',
    author_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- What is holding a discharge up, as on a ward huddle board.
CREATE TABLE IF NOT EXISTS discharge_barriers (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES discharge_episodes (id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolved_by INTEGER,
    resolved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discharge_pending_results (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES discharge_episodes (id) ON DELETE CASCADE,
    test_name TEXT NOT NULL,
    expected_by TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'final')),
    result_note TEXT NOT NULL DEFAULT '',
    finalised_by INTEGER,
    finalised_at TEXT
);
