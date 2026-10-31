CREATE TABLE IF NOT EXISTS amr_import_batches (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    total_rows INTEGER NOT NULL,
    accepted_rows INTEGER NOT NULL,
    rejected_rows INTEGER NOT NULL,
    errors TEXT NOT NULL DEFAULT '[]',
    imported_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- patient_ref is the hospital's own reference. It never appears in aggregate output.
CREATE TABLE IF NOT EXISTS microbiology_results (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL REFERENCES amr_import_batches (id) ON DELETE CASCADE,
    patient_ref TEXT NOT NULL,
    isolate_ref TEXT NOT NULL,
    specimen TEXT NOT NULL,
    organism TEXT NOT NULL,
    antibiotic TEXT NOT NULL,
    mic TEXT NOT NULL DEFAULT '',
    zone_mm TEXT NOT NULL DEFAULT '',
    interpretation TEXT NOT NULL CHECK (interpretation IN ('S', 'I', 'R')),
    standard TEXT NOT NULL,
    standard_version TEXT NOT NULL,
    collected_on TEXT NOT NULL,
    laboratory TEXT NOT NULL,
    UNIQUE (organization_id, isolate_ref, antibiotic)
);
CREATE INDEX IF NOT EXISTS microbiology_period ON microbiology_results (organization_id, collected_on);

CREATE TABLE IF NOT EXISTS antibiogram_runs (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    min_isolates INTEGER NOT NULL,
    method TEXT NOT NULL,
    output TEXT NOT NULL,
    data_quality TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS culture_reviews (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    isolate_ref TEXT NOT NULL,
    patient_ref TEXT NOT NULL,
    organism TEXT NOT NULL,
    specimen TEXT NOT NULL,
    collected_on TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'positive' CHECK (state IN ('positive', 'reviewed', 'acknowledged', 'actioned')),
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    UNIQUE (organization_id, isolate_ref)
);

-- Written by people. Nothing in AMR Guard generates a recommendation.
CREATE TABLE IF NOT EXISTS stewardship_reviews (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    culture_review_id TEXT REFERENCES culture_reviews (id) ON DELETE SET NULL,
    patient_ref TEXT NOT NULL,
    intervention TEXT NOT NULL CHECK (intervention IN
        ('review_requested', 'de_escalation_considered', 'duration_review', 'guideline_discussion', 'other')),
    finding TEXT NOT NULL,
    recommendation TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    disposition TEXT NOT NULL DEFAULT 'open' CHECK (disposition IN ('open', 'accepted', 'declined', 'no_action')),
    follow_up TEXT NOT NULL DEFAULT '',
    reviewer_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS amr_guidelines (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    issuer TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    clinical_owner TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    added_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
