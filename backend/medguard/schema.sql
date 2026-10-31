CREATE TABLE IF NOT EXISTS medication_reconciliations (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    encounter_id TEXT REFERENCES encounters (id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'finalised')),
    source_snapshot TEXT NOT NULL,
    knowledge_version TEXT NOT NULL,
    summary TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    finalised_by INTEGER,
    finalised_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_reconciliation
    ON medication_reconciliations (patient_id) WHERE status = 'draft';

CREATE TABLE IF NOT EXISTS medication_alerts (
    id TEXT PRIMARY KEY,
    reconciliation_id TEXT NOT NULL REFERENCES medication_reconciliations (id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('high', 'moderate', 'info')),
    title TEXT NOT NULL,
    explanation TEXT NOT NULL,
    evidence TEXT NOT NULL,
    rule_id TEXT,
    knowledge_version TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    disposition TEXT NOT NULL DEFAULT 'pending'
        CHECK (disposition IN ('pending', 'acknowledged', 'action_planned', 'not_applicable')),
    disposition_reason TEXT NOT NULL DEFAULT '',
    reviewed_by INTEGER,
    reviewed_at TEXT,
    UNIQUE (reconciliation_id, fingerprint)
);

-- Public reference lookups, with where and when each came from.
CREATE TABLE IF NOT EXISTS terminology_cache (
    cache_key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- A rule's evidence and review travel with it. Only 'approved' rules can raise a clinical alert.
CREATE TABLE IF NOT EXISTS medication_rules (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK (category IN ('drug_interaction', 'allergy_cross_reaction')),
    side_a TEXT NOT NULL,
    side_b TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('high', 'moderate')),
    summary TEXT NOT NULL,
    basis TEXT NOT NULL,
    evidence_source TEXT,
    evidence_version TEXT,
    evidence_date TEXT,
    rule_set_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending_pharmacist_review', 'approved', 'rejected', 'retired')),
    created_at TEXT NOT NULL,
    created_by_label TEXT NOT NULL,
    submitted_by INTEGER,
    submitted_at TEXT,
    last_reviewed_at TEXT,
    reviewer_id INTEGER,
    reviewer_name TEXT,
    review_note TEXT
);

CREATE TABLE IF NOT EXISTS medication_rule_events (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES medication_rules (id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    actor_name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS medication_rule_events_no_update BEFORE UPDATE ON medication_rule_events
BEGIN SELECT RAISE(ABORT, 'rule history cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS medication_rule_events_no_delete BEFORE DELETE ON medication_rule_events
BEGIN SELECT RAISE(ABORT, 'rule history cannot be deleted'); END;
