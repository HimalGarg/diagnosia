-- Technical execution metadata owned by Diagnosia, in a separately provisioned YugabyteDB.
-- No clinical records, identities, consent or event history belong here.
CREATE TABLE IF NOT EXISTS integration_agent_runs (
    run_id TEXT PRIMARY KEY,
    workflow_ref TEXT,
    module TEXT NOT NULL,
    tool TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
    provider TEXT,
    model TEXT,
    fallback_used BOOLEAN NOT NULL DEFAULT false,
    fallback_provider TEXT,
    fallback_model TEXT,
    error_category TEXT,
    trace_ref TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration_task_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES integration_agent_runs (run_id),
    attempt_number INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    error_category TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    duration_ms INTEGER NOT NULL,
    UNIQUE (run_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS integration_handoffs (
    handoff_id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    workflow_ref TEXT NOT NULL,
    source_module TEXT NOT NULL,
    target_module TEXT NOT NULL,
    source_event_id TEXT NOT NULL UNIQUE,
    handoff_state TEXT NOT NULL CHECK (handoff_state IN ('recorded', 'context_shared', 'context_unavailable')),
    human_review_required BOOLEAN NOT NULL,
    context_ref TEXT,
    meko_datapack_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS integration_handoffs_workflow ON integration_handoffs (workflow_ref, created_at);
CREATE INDEX IF NOT EXISTS integration_agent_runs_tool ON integration_agent_runs (tool, started_at);
CREATE INDEX IF NOT EXISTS integration_agent_runs_status ON integration_agent_runs (status, started_at);
