-- SQLite stays the record. These two tables only say what has reached the patient's datapack.
CREATE TABLE IF NOT EXISTS patient_packs (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL UNIQUE REFERENCES patients (id) ON DELETE CASCADE,
    datapack_id TEXT,
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'erasing', 'erased')),
    datapacks_used INTEGER NOT NULL DEFAULT 0,  -- Meko never gives a slot back, so erased ones still count
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- A reference to a record, never its text. The worker reads the record when it sends.
CREATE TABLE IF NOT EXISTS pack_items (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT,
    document_key TEXT,
    document_id TEXT,
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'indexing', 'ready', 'failed', 'held')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_type, source_id)
);
CREATE INDEX IF NOT EXISTS pack_items_due ON pack_items (state, next_attempt_at);
CREATE INDEX IF NOT EXISTS pack_items_patient ON pack_items (patient_id, state);

-- Medicines a patient ticked in the Prescription Analyzer and chose to keep. The photo is never stored.
CREATE TABLE IF NOT EXISTS saved_prescriptions (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    medicines TEXT NOT NULL,
    saved_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS saved_prescriptions_patient ON saved_prescriptions (patient_id, created_at);
