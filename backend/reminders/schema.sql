CREATE TABLE IF NOT EXISTS communication_preferences (
    patient_id TEXT PRIMARY KEY REFERENCES patients (id) ON DELETE CASCADE,
    in_app INTEGER NOT NULL DEFAULT 1,
    clinic_call INTEGER NOT NULL DEFAULT 1,
    sms INTEGER NOT NULL DEFAULT 0,
    voice INTEGER NOT NULL DEFAULT 0,
    updated_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

-- A reminder records what actually happened. Queued or sent never means delivered.
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks (id) ON DELETE SET NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('appointment', 'medicine_review', 'test', 'follow_up', 'other')),
    title TEXT NOT NULL,
    module TEXT NOT NULL DEFAULT 'general',
    priority TEXT NOT NULL DEFAULT 'routine' CHECK (priority IN ('routine', 'high')),
    scheduled_for TEXT NOT NULL,
    respond_within_hours INTEGER NOT NULL DEFAULT 24,
    channel TEXT CHECK (channel IN ('in_app', 'clinic_call', 'sms', 'voice')),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN (
        'pending', 'in_app_sent', 'external_delivery_pending', 'delivered', 'failed',
        'staff_followup_required', 'acknowledged', 'cancelled')),
    sent_at TEXT,
    external_id TEXT,
    failure_reason TEXT NOT NULL DEFAULT '',
    contact_attempts INTEGER NOT NULL DEFAULT 0,
    followup_task_id TEXT REFERENCES tasks (id) ON DELETE SET NULL,
    outcome TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (state, scheduled_for);
CREATE INDEX IF NOT EXISTS reminders_patient ON reminders (patient_id, state);

CREATE TABLE IF NOT EXISTS reminder_events (
    id TEXT PRIMARY KEY,
    reminder_id TEXT NOT NULL REFERENCES reminders (id) ON DELETE CASCADE,
    from_state TEXT,
    to_state TEXT NOT NULL,
    channel TEXT,
    actor_user_id INTEGER,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reminder_events_reminder ON reminder_events (reminder_id, created_at);
CREATE TRIGGER IF NOT EXISTS reminder_events_no_update BEFORE UPDATE ON reminder_events
BEGIN SELECT RAISE(ABORT, 'reminder history cannot be changed'); END;
