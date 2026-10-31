-- Diagnosia CareFlow: technical bookkeeping for the activity timeline. Nothing here is a clinical record.

-- Maps a pseudonymous reference to what it stands for. This mapping stays in Diagnosia's own database,
-- which is what keeps the references pseudonymous in every other store.
CREATE TABLE IF NOT EXISTS careflow_workflows (
    workflow_ref TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    tenant_ref TEXT NOT NULL,
    patient_id TEXT UNIQUE REFERENCES patients (id) ON DELETE SET NULL,
    intake_id INTEGER UNIQUE,
    owner_user_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    owner_module TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (owner_user_id, owner_module)
);

-- The transactional outbox. A row commits with the change it describes.
CREATE TABLE IF NOT EXISTS careflow_outbox (
    event_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    workflow_ref TEXT NOT NULL REFERENCES careflow_workflows (workflow_ref),
    module TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    event TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS careflow_outbox_workflow ON careflow_outbox (workflow_ref, occurred_at);

-- One row per event per destination, so each destination fails and recovers on its own.
CREATE TABLE IF NOT EXISTS careflow_deliveries (
    event_id TEXT NOT NULL REFERENCES careflow_outbox (event_id),
    channel TEXT NOT NULL CHECK (channel IN ('mongodb', 'yugabyte', 'meko')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    delivered_at TEXT,
    PRIMARY KEY (event_id, channel)
);
CREATE INDEX IF NOT EXISTS careflow_deliveries_due ON careflow_deliveries (channel, status, next_attempt_at);

-- Where a Change Stream got to, so a restart carries on instead of missing events.
CREATE TABLE IF NOT EXISTS careflow_stream_state (
    name TEXT PRIMARY KEY,
    resume_token TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- What was sent to Meko's knowledge base, and which version of it. The files are in the repository.
CREATE TABLE IF NOT EXISTS careflow_knowledge (
    document TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    document_key TEXT,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
