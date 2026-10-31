CREATE TABLE IF NOT EXISTS lab_reports (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES clinical_documents (id),
    encounter_id TEXT REFERENCES encounters (id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'needs_review', 'needs_manual_entry', 'confirmed', 'failed')),
    failure_reason TEXT NOT NULL DEFAULT '',
    laboratory TEXT NOT NULL DEFAULT '',
    name_on_report TEXT NOT NULL DEFAULT '',
    identity_check TEXT NOT NULL DEFAULT 'unknown' CHECK (identity_check IN ('match', 'mismatch', 'unknown')),
    identity_acknowledged_by INTEGER,
    collected_at TEXT,
    extraction_method TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    uploaded_by INTEGER NOT NULL,
    confirmed_by INTEGER,
    confirmed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lab_results (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES lab_reports (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    original_name TEXT NOT NULL,
    original_line TEXT NOT NULL DEFAULT '',
    loinc_code TEXT,
    standard_name TEXT,
    value_text TEXT NOT NULL,
    value_number REAL,
    unit TEXT NOT NULL DEFAULT '',
    reference_text TEXT NOT NULL DEFAULT '',
    reference_low REAL,
    reference_high REAL,
    reported_flag TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'review')),
    issues TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'accepted', 'corrected', 'rejected')),
    original_value TEXT,
    reviewed_by INTEGER,
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS lab_results_report ON lab_results (report_id, position);

-- A code here is a proposal until a laboratory professional approves it.
CREATE TABLE IF NOT EXISTS loinc_mappings (
    id TEXT PRIMARY KEY,
    local_label TEXT NOT NULL UNIQUE,
    proposed_code TEXT NOT NULL,
    source TEXT NOT NULL,
    source_version TEXT,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected')),
    proposed_by INTEGER,
    reviewer_id INTEGER,
    reviewer_name TEXT,
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS loinc_mapping_events (
    id TEXT PRIMARY KEY,
    mapping_id TEXT NOT NULL REFERENCES loinc_mappings (id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    from_code TEXT NOT NULL,
    to_code TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    actor_name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS loinc_mapping_events_no_update BEFORE UPDATE ON loinc_mapping_events
BEGIN SELECT RAISE(ABORT, 'mapping history cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS loinc_mapping_events_no_delete BEFORE DELETE ON loinc_mapping_events
BEGIN SELECT RAISE(ABORT, 'mapping history cannot be deleted'); END;
