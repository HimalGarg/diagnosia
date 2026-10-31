CREATE TABLE IF NOT EXISTS condition_profiles (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    condition_name TEXT NOT NULL,
    onset_date TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved')),
    managing_clinician_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS care_plans (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    condition_id TEXT REFERENCES condition_profiles (id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
    review_date TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    approved_by INTEGER,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS care_plan_goals (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES care_plans (id) ON DELETE CASCADE,
    description TEXT NOT NULL
);

-- Thresholds exist only here, set by a clinician for one patient, with what to do next.
CREATE TABLE IF NOT EXISTS care_plan_measures (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES care_plans (id) ON DELETE CASCADE,
    measure TEXT NOT NULL,
    target_text TEXT NOT NULL DEFAULT '',
    cadence_days INTEGER,
    alert_low REAL,
    alert_high REAL,
    escalation_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS adherence_events (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    medicine_name TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('taken', 'missed', 'held')),
    effective_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL,
    recorded_by INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS adherence_patient ON adherence_events (patient_id, effective_at);
