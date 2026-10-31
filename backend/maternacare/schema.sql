CREATE TABLE IF NOT EXISTS maternal_episodes (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'antenatal' CHECK (status IN ('antenatal', 'postnatal', 'closed')),
    estimated_due_date TEXT,
    due_date_confirmed_by INTEGER,
    due_date_basis TEXT NOT NULL DEFAULT '',
    gravida INTEGER,
    para INTEGER,
    care_provider_id INTEGER NOT NULL REFERENCES users (id),
    facility TEXT NOT NULL DEFAULT '',
    escalation_contact TEXT NOT NULL,
    concern_response_hours INTEGER NOT NULL,
    consent_id TEXT NOT NULL REFERENCES consents (id),
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_maternal_episode
    ON maternal_episodes (patient_id) WHERE status != 'closed';

-- Every visit is planned by a clinician. No schedule is generated.
CREATE TABLE IF NOT EXISTS maternal_visits (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES maternal_episodes (id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN ('antenatal', 'postnatal')),
    subject TEXT NOT NULL DEFAULT 'mother' CHECK (subject IN ('mother', 'newborn')),
    planned_date TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'attended', 'missed', 'cancelled')),
    notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maternal_risks (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES maternal_episodes (id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved')),
    confirmed_by INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maternal_education (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES maternal_episodes (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en-IN',
    publisher TEXT NOT NULL,
    content_version TEXT NOT NULL,
    approved_by INTEGER NOT NULL,
    approved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS birth_plans (
    episode_id TEXT PRIMARY KEY REFERENCES maternal_episodes (id) ON DELETE CASCADE,
    facility TEXT NOT NULL DEFAULT '',
    transport TEXT NOT NULL DEFAULT '',
    contacts TEXT NOT NULL DEFAULT '',
    referral_arrangements TEXT NOT NULL DEFAULT '',
    updated_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_records (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL UNIQUE REFERENCES maternal_episodes (id) ON DELETE CASCADE,
    delivered_at TEXT NOT NULL,
    facility TEXT NOT NULL,
    outcome TEXT NOT NULL,
    newborn_patient_id TEXT REFERENCES patients (id) ON DELETE SET NULL,
    recorded_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maternal_concerns (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES maternal_episodes (id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    reported_by INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    task_id TEXT REFERENCES tasks (id) ON DELETE SET NULL,
    acknowledged_by INTEGER,
    acknowledged_at TEXT,
    outcome TEXT NOT NULL DEFAULT ''
);
