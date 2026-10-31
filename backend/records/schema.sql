-- The shared patient record every add-on tool builds on.
-- Login accounts (users), patients and practitioners are separate on purpose.

CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users (id) ON DELETE SET NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    date_of_birth TEXT,
    sex TEXT CHECK (sex IN ('female', 'male', 'other', 'unknown')),
    phone TEXT,
    preferred_language TEXT NOT NULL DEFAULT 'en-IN',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patient_identifiers (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    value TEXT NOT NULL,
    issuer TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    UNIQUE (patient_id, type, value)
);

CREATE TABLE IF NOT EXISTS practitioners (
    user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    profession TEXT NOT NULL DEFAULT 'doctor',
    speciality TEXT,
    registration_number TEXT
);

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('clinic', 'hospital', 'laboratory', 'pharmacy')),
    address TEXT,
    phone TEXT,
    specialities TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organization_members (
    organization_id TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('admin', 'staff', 'coordinator')),
    added_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, user_id)
);

CREATE TABLE IF NOT EXISTS care_team (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('clinician', 'caregiver')),
    scope TEXT NOT NULL DEFAULT 'full' CHECK (scope IN ('full', 'read')),
    added_by INTEGER NOT NULL,
    granted_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS care_team_active
    ON care_team (patient_id, user_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS share_codes (
    code_hash TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('clinician', 'caregiver', 'claim')),
    scope TEXT NOT NULL DEFAULT 'full' CHECK (scope IN ('full', 'read')),
    created_by INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL CHECK (method IN ('patient_in_app', 'clinician_recorded')),
    policy_version TEXT NOT NULL,
    recorded_by INTEGER NOT NULL,
    granted_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by INTEGER
);
CREATE INDEX IF NOT EXISTS consents_lookup ON consents (patient_id, purpose);

CREATE TABLE IF NOT EXISTS encounters (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations (id) ON DELETE SET NULL,
    type TEXT NOT NULL CHECK (type IN ('outpatient', 'inpatient', 'emergency', 'telehealth', 'home')),
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('planned', 'in_progress', 'finished', 'cancelled')),
    reason TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    created_by INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS encounters_patient ON encounters (patient_id, started_at);

CREATE TABLE IF NOT EXISTS clinical_documents (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    encounter_id TEXT REFERENCES encounters (id) ON DELETE SET NULL,
    document_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    retention_status TEXT NOT NULL DEFAULT 'retained' CHECK (retention_status IN ('retained', 'deleted')),
    uploaded_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (patient_id, sha256)
);

-- Every clinical value says where it came from and whether a person checked it.
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    encounter_id TEXT REFERENCES encounters (id) ON DELETE SET NULL,
    category TEXT NOT NULL CHECK (category IN ('laboratory')),
    code_system TEXT,
    code TEXT,
    label TEXT NOT NULL,
    value_number REAL,
    value_text TEXT,
    unit TEXT NOT NULL DEFAULT '',
    reference_text TEXT NOT NULL DEFAULT '',
    reference_low REAL,
    reference_high REAL,
    flag TEXT NOT NULL DEFAULT '',
    effective_at TEXT NOT NULL,
    performer TEXT,
    note TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL CHECK (source_type IN
        ('patient_entered', 'clinician_entered', 'imported_document', 'laboratory', 'device', 'ai_extracted')),
    source_reference TEXT,
    extraction_method TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'accepted', 'corrected', 'rejected', 'superseded')),
    reviewed_by INTEGER,
    reviewed_at TEXT,
    original_value TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS observations_trend ON observations (patient_id, code, unit, effective_at);

CREATE TABLE IF NOT EXISTS allergies (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    substance TEXT NOT NULL,
    reaction TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'unknown' CHECK (severity IN ('mild', 'moderate', 'severe', 'unknown')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'refuted')),
    source_type TEXT NOT NULL,
    recorded_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    audience TEXT NOT NULL DEFAULT 'care_team' CHECK (audience IN ('patient', 'care_team')),
    owner_user_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    escalate_to_user_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    priority TEXT NOT NULL DEFAULT 'routine' CHECK (priority IN ('routine', 'high')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('held', 'open', 'done', 'missed', 'blocked', 'cancelled')),
    required INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    module TEXT NOT NULL DEFAULT 'general',
    link_type TEXT,
    link_id TEXT,
    status_note TEXT NOT NULL DEFAULT '',
    escalated_at TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_patient ON tasks (patient_id, status);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks (status, due_at);
CREATE INDEX IF NOT EXISTS tasks_link ON tasks (link_type, link_id);

-- In-app only for now. Delivery to SMS or WhatsApp needs a provider chosen first.
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    recipient_user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    patient_id TEXT REFERENCES patients (id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'in_app',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    module TEXT NOT NULL DEFAULT 'general',
    delivery_status TEXT NOT NULL DEFAULT 'delivered',
    created_at TEXT NOT NULL,
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS notifications_inbox ON notifications (recipient_user_id, read_at);

-- One line per approved event, so the record reads as a single patient journey.
CREATE TABLE IF NOT EXISTS timeline_events (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    resource_type TEXT,
    resource_id TEXT,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS timeline_patient ON timeline_events (patient_id, occurred_at);

-- Kept outside foreign keys so history survives when an account or record is removed.
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    patient_id TEXT,
    resource_type TEXT,
    resource_id TEXT,
    outcome TEXT NOT NULL DEFAULT 'success',
    reason TEXT NOT NULL DEFAULT '',
    request_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_patient ON audit_events (patient_id, created_at);

CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events cannot be deleted'); END;

-- Who checked a staff account, and how. Never edited: a change is a new row.
CREATE TABLE IF NOT EXISTS staff_verifications (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('verified', 'rejected', 'suspended', 'reinstated')),
    organization_id TEXT,
    decided_by INTEGER,
    decided_by_label TEXT NOT NULL,
    evidence_type TEXT NOT NULL DEFAULT '',
    evidence_reference TEXT NOT NULL DEFAULT '',
    verified_profession TEXT,
    notes TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS staff_verifications_user ON staff_verifications (user_id, decided_at);

CREATE TRIGGER IF NOT EXISTS staff_verifications_no_update BEFORE UPDATE ON staff_verifications
BEGIN SELECT RAISE(ABORT, 'verification records cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS staff_verifications_no_delete BEFORE DELETE ON staff_verifications
BEGIN SELECT RAISE(ABORT, 'verification records cannot be deleted'); END;

CREATE TABLE IF NOT EXISTS organization_invitations (
    code_hash TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'staff', 'coordinator')),
    invited_by INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    accepted_by INTEGER,
    accepted_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_failures (
    email_hash TEXT NOT NULL,
    failed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS login_failures_lookup ON login_failures (email_hash, failed_at);

-- Human approvals the readiness check looks for. Written only from the server's command line.
CREATE TABLE IF NOT EXISTS governance_signoffs (
    id TEXT PRIMARY KEY,
    item TEXT NOT NULL,
    signed_by TEXT NOT NULL,
    signer_role TEXT NOT NULL,
    document_reference TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT
);
CREATE INDEX IF NOT EXISTS governance_signoffs_item ON governance_signoffs (item, revoked_at);

-- What administrators choose in Settings. Only keys declared in settings_store.py are stored.
CREATE TABLE IF NOT EXISTS platform_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

-- Sealed with the document key ring, which never sits with this database.
CREATE TABLE IF NOT EXISTS platform_secrets (
    name TEXT PRIMARY KEY,
    sealed BLOB NOT NULL,
    last_four TEXT NOT NULL,
    updated_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
