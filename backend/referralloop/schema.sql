CREATE TABLE IF NOT EXISTS referrals (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    encounter_id TEXT REFERENCES encounters (id) ON DELETE SET NULL,
    referring_user_id INTEGER NOT NULL REFERENCES users (id),
    receiving_organization_id TEXT REFERENCES organizations (id) ON DELETE SET NULL,
    receiving_user_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    manual_destination TEXT NOT NULL DEFAULT '',
    specialty TEXT NOT NULL,
    reason TEXT NOT NULL,
    clinical_question TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL CHECK (priority IN ('routine', 'urgent')),
    status TEXT NOT NULL DEFAULT 'sent',
    respond_by TEXT NOT NULL,
    consent_id TEXT NOT NULL REFERENCES consents (id),
    replaces_referral_id TEXT REFERENCES referrals (id),
    duplicate_reason TEXT NOT NULL DEFAULT '',
    contact_attempts INTEGER NOT NULL DEFAULT 0,
    closed_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS referrals_patient ON referrals (patient_id, status);
CREATE INDEX IF NOT EXISTS referrals_receiving ON referrals (receiving_organization_id, status);

CREATE TABLE IF NOT EXISTS referral_events (
    id TEXT PRIMARY KEY,
    referral_id TEXT NOT NULL REFERENCES referrals (id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL,
    actor_side TEXT NOT NULL CHECK (actor_side IN ('referrer', 'receiver', 'system')),
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'app' CHECK (source IN ('app', 'manual')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS referral_events_referral ON referral_events (referral_id, created_at);

CREATE TABLE IF NOT EXISTS referral_documents (
    referral_id TEXT NOT NULL REFERENCES referrals (id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES clinical_documents (id),
    PRIMARY KEY (referral_id, document_id)
);

CREATE TABLE IF NOT EXISTS referral_appointments (
    id TEXT PRIMARY KEY,
    referral_id TEXT NOT NULL REFERENCES referrals (id) ON DELETE CASCADE,
    scheduled_at TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'offered'
        CHECK (status IN ('offered', 'booked', 'rescheduled', 'cancelled', 'attended', 'no_show')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS referral_outcomes (
    id TEXT PRIMARY KEY,
    referral_id TEXT NOT NULL REFERENCES referrals (id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    recommendations TEXT NOT NULL DEFAULT '',
    follow_up_owner TEXT NOT NULL DEFAULT '',
    return_to_referrer INTEGER NOT NULL DEFAULT 1,
    document_id TEXT REFERENCES clinical_documents (id),
    recorded_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT
);
