"""Migrations are tested on throwaway copies. The real database is never touched."""
import sqlite3

import pytest

from backend import db as database
from backend import migrations
from backend.config import settings
from backend.operator_cli import main as operator
from backend.private_files import is_private

# The schema as it stood before account states, with synthetic rows.
OLD_DATABASE = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('doctor', 'patient')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE, expires_at TEXT NOT NULL
);
CREATE TABLE practitioners (
    user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    profession TEXT NOT NULL DEFAULT 'doctor'
        CHECK (profession IN ('doctor', 'nurse', 'midwife', 'pharmacist', 'coordinator', 'community_health_worker', 'microbiologist')),
    speciality TEXT, registration_number TEXT, verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE organizations (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, address TEXT, phone TEXT,
    specialities TEXT NOT NULL DEFAULT '', created_by INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE clinical_documents (
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, encounter_id TEXT, document_type TEXT NOT NULL, filename TEXT NOT NULL,
    content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, stored_name TEXT NOT NULL,
    source_type TEXT NOT NULL, retention_status TEXT NOT NULL DEFAULT 'retained', uploaded_by INTEGER NOT NULL,
    created_at TEXT NOT NULL, UNIQUE (patient_id, sha256)
);
CREATE TABLE medication_reconciliations (
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, encounter_id TEXT, status TEXT NOT NULL DEFAULT 'draft',
    source_snapshot TEXT NOT NULL, knowledge_version TEXT NOT NULL, summary TEXT, created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL, finalised_by INTEGER, finalised_at TEXT
);
CREATE TABLE medication_alerts (
    id TEXT PRIMARY KEY, reconciliation_id TEXT NOT NULL, patient_id TEXT NOT NULL, alert_type TEXT NOT NULL,
    severity TEXT NOT NULL, title TEXT NOT NULL, explanation TEXT NOT NULL, evidence TEXT NOT NULL, rule_id TEXT,
    knowledge_version TEXT NOT NULL, fingerprint TEXT NOT NULL, disposition TEXT NOT NULL DEFAULT 'pending',
    disposition_reason TEXT NOT NULL DEFAULT '', reviewed_by INTEGER, reviewed_at TEXT,
    UNIQUE (reconciliation_id, fingerprint)
);
INSERT INTO users (id, first_name, last_name, email, password_hash, role) VALUES
    (1, 'Synthetic', 'Doctor', 'd@example.com', 'x', 'doctor'), (2, 'Synthetic', 'Patient', 'p@example.com', 'x', 'patient');
INSERT INTO sessions VALUES ('doctor-token', 1, '2099-01-01'), ('patient-token', 2, '2099-01-01');
INSERT INTO practitioners (user_id, profession, verified) VALUES (1, 'pharmacist', 1);
INSERT INTO organizations (id, name, type, created_by, created_at) VALUES ('o1', 'Old Clinic', 'clinic', 1, '2026-01-01');
INSERT INTO clinical_documents (id, patient_id, document_type, filename, content_type, size_bytes, sha256, stored_name,
    source_type, uploaded_by, created_at) VALUES ('d1', 'p1', 'other', 'a.pdf', 'application/pdf', 1, 'abc', 'a.pdf', 'x', 1, '2026-01-01');
"""


@pytest.fixture
def old_database(tmp_path, monkeypatch):
    path = tmp_path / "copy.db"
    connection = sqlite3.connect(path)
    connection.executescript(OLD_DATABASE)
    connection.close()
    monkeypatch.setattr(settings, "db_path", path)
    return path


def open_database(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def table_names(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_a_fresh_database_is_created_with_every_migration(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    monkeypatch.setattr(settings, "db_path", path)
    database.init_db()
    connection = open_database(path)
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(migrations.MIGRATIONS)
    found = table_names(connection)
    assert {"patients", "reminders", "loinc_mappings", "staff_verifications", "discharge_episodes"} <= found
    # The medication, pregnancy, chronic care and antibiogram tools were removed, and take no tables with them.
    assert not {"medication_rules", "medication_records", "maternal_episodes", "care_plans", "antibiogram_runs"} & found
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_an_existing_database_upgrades_and_keeps_its_rows(old_database):
    database.init_db()
    connection = open_database(old_database)
    assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    assert connection.execute("SELECT filename FROM clinical_documents").fetchone()[0] == "a.pdf"
    assert connection.execute("SELECT verification_status FROM organizations").fetchone()[0] == "unverified"
    connection.close()


def test_upgraded_staff_lose_trust_until_verified(old_database):
    database.init_db()
    connection = open_database(old_database)
    statuses = dict(connection.execute("SELECT role, account_status FROM users").fetchall())
    assert statuses == {"doctor": "pending_verification", "patient": "active"}
    assert [row[0] for row in connection.execute("SELECT token_hash FROM sessions")] == ["patient-token"]
    connection.close()


def test_an_old_verified_flag_does_not_become_a_verified_profession(old_database):
    database.init_db()
    connection = open_database(old_database)
    row = connection.execute("SELECT profession, verified_profession FROM practitioners").fetchone()
    assert row["profession"] == "pharmacist"
    assert row["verified_profession"] is None
    connection.close()


def test_existing_documents_are_marked_plaintext_until_proved(old_database):
    database.init_db()
    connection = open_database(old_database)
    assert connection.execute("SELECT encryption_state FROM clinical_documents").fetchone()[0] == "plaintext_legacy"
    connection.close()


def test_running_twice_changes_nothing(old_database):
    database.init_db()
    connection = open_database(old_database)
    first = connection.execute("SELECT * FROM schema_migrations").fetchall()
    connection.close()
    database.init_db()
    connection = open_database(old_database)
    assert [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")] == [tuple(row) for row in first]
    connection.close()


def test_a_copy_is_taken_before_changing_a_database_with_records(old_database):
    database.init_db()
    copies = list(old_database.parent.glob("copy.db.pre-migration-*.bak"))
    assert len(copies) == 1
    assert is_private(copies[0])
    backup = open_database(copies[0])
    assert "account_status" not in [row[1] for row in backup.execute("PRAGMA table_info(users)")]
    backup.close()


def test_no_copy_is_made_when_nothing_changes(old_database):
    database.init_db()
    database.init_db()
    assert len(list(old_database.parent.glob("*.bak"))) == 1


def failing_step(connection):
    connection.execute("ALTER TABLE users ADD COLUMN half_done TEXT")
    raise RuntimeError("simulated power cut")


def test_an_interrupted_migration_rolls_back_and_can_retry(tmp_path):
    path = tmp_path / "partial.db"
    connection = sqlite3.connect(path)
    connection.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY); INSERT INTO users VALUES (1);")
    steps = [(1, "good", lambda db: db.execute("ALTER TABLE users ADD COLUMN first TEXT")), (2, "bad", failing_step)]
    with pytest.raises(RuntimeError):
        migrations.run(connection, path, steps)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(users)")]
    assert "first" in columns
    assert "half_done" not in columns
    assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations")] == [1]

    fixed = [steps[0], (2, "bad", lambda db: db.execute("ALTER TABLE users ADD COLUMN second TEXT"))]
    assert migrations.run(connection, path, fixed) == [2]
    connection.close()


def test_a_crash_mid_transaction_leaves_nothing_behind(tmp_path):
    path = tmp_path / "crash.db"
    connection = sqlite3.connect(path)
    connection.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY); INSERT INTO users VALUES (1);")
    connection.close()
    connection = sqlite3.connect(path)
    with pytest.raises(RuntimeError):
        migrations.run(connection, None, [(1, "bad", failing_step)])
    connection.close()
    fresh = sqlite3.connect(path)
    assert "half_done" not in [row[1] for row in fresh.execute("PRAGMA table_info(users)")]
    assert fresh.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    fresh.close()


def test_recovery_from_the_pre_migration_copy_works(old_database):
    database.init_db()
    copy = next(old_database.parent.glob("*.bak"))
    connection = open_database(copy)
    assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_foreign_keys_hold_after_an_upgrade(old_database):
    database.init_db()
    connection = open_database(old_database)
    assert connection.execute("PRAGMA foreign_key_check(sessions)").fetchall() == []
    connection.close()


def test_foreign_keys_are_on_for_every_connection(client):
    connection = database.connect()
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    connection.close()


def test_a_record_cannot_point_at_a_patient_who_does_not_exist(client):
    connection = database.connect()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO encounters (id, patient_id, type, started_at, created_by) VALUES ('e', 'missing', 'outpatient', 'x', 1)"
        )
    connection.close()


def test_a_reminder_must_belong_to_a_real_patient(client):
    connection = database.connect()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO reminders (id, patient_id, purpose, title, scheduled_for, created_by, created_at, updated_at)
            VALUES ('r', 'missing', 'other', 't', 'x', 1, 'x', 'x')
            """
        )
    connection.close()


def test_the_command_line_backup_restores_into_a_working_database(doctor, patient, tmp_path_factory, monkeypatch):
    destination = tmp_path_factory.mktemp("backup") / "set"
    assert operator(["backup", "--to", str(destination)]) == 0
    restored = tmp_path_factory.mktemp("restored")
    assert operator(["restore", "--source", str(destination), "--database", str(restored / "r.db"), "--documents", str(restored / "d")]) == 0
    monkeypatch.setattr(settings, "db_path", restored / "r.db")
    database.init_db()
    connection = database.connect()
    assert connection.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 1
    connection.close()
