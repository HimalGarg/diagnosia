import hashlib
import json
import logging
import os
import sqlite3
from pathlib import Path

import pytest

from backend.config import settings
from backend.db import connect
from backend.operator_cli import main as operator
from backend.records import documents, vault
from backend.tests.conftest import grant, linked_patient

SECRET = b"%PDF-1.4 SYNTHETIC-PATIENT-MARKER haemoglobin 11.2"


def upload(client, patient_id, data=SECRET, name="ravi_kumar_report.pdf"):
    return client.post(f"/api/patients/{patient_id}/documents", files={"file": (name, data, "application/pdf")})


def stored_files():
    return [path for path in settings.documents_dir.rglob("*") if path.is_file()]


def row(document_id):
    db = connect()
    try:
        return dict(db.execute("SELECT * FROM clinical_documents WHERE id = ?", (document_id,)).fetchone())
    finally:
        db.close()


def legacy_document(patient_id, data=b"%PDF-1.4 legacy synthetic report", name="old-file.pdf"):
    """A file as an earlier version stored it, in plaintext."""
    folder = settings.documents_dir / patient_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    db = connect()
    db.execute(
        """
        INSERT INTO clinical_documents (id, patient_id, document_type, filename, content_type, size_bytes, sha256,
            stored_name, source_type, uploaded_by, created_at, encryption_state)
        VALUES (?, ?, 'other', ?, 'application/pdf', ?, ?, ?, 'clinician_entered', 1, '2026-01-01', 'plaintext_legacy')
        """,
        (name, patient_id, name, len(data), hashlib.sha256(data).hexdigest(), name),
    )
    db.commit()
    db.close()
    return name


def test_stored_reports_are_encrypted(doctor, patient):
    document = upload(doctor, patient["id"]).json()
    files = stored_files()
    assert len(files) == 1
    content = files[0].read_bytes()
    assert content.startswith(vault.MAGIC)
    assert b"SYNTHETIC-PATIENT-MARKER" not in content
    assert row(document["id"])["encryption_state"] == "encrypted"


def test_filenames_on_disk_hold_no_personal_details(doctor, patient):
    upload(doctor, patient["id"])
    assert "ravi" not in stored_files()[0].name.lower()


def test_files_and_folders_are_owner_only(doctor, patient):
    upload(doctor, patient["id"])
    file = stored_files()[0]
    assert oct(file.stat().st_mode & 0o777) == "0o600"
    assert oct(file.parent.stat().st_mode & 0o777) == "0o700"


def test_no_temporary_files_remain(doctor, patient):
    upload(doctor, patient["id"])
    assert not [path for path in stored_files() if path.suffix == ".tmp"]


def test_keys_are_never_stored_with_the_data(doctor, patient, key_file):
    upload(doctor, patient["id"])
    key = json.loads(key_file.read_text())["keys"]["test1"].encode()
    db = connect()
    db.execute("PRAGMA wal_checkpoint(FULL)")
    db.close()
    for path in [settings.db_path, *stored_files()]:
        assert key not in path.read_bytes()


def test_each_file_gets_its_own_key_and_nonce(client):
    first, second = vault.encrypt(b"same", "c"), vault.encrypt(b"same", "c")
    assert first != second


def test_the_owner_can_download_and_the_content_is_intact(doctor, patient):
    document = upload(doctor, patient["id"]).json()
    response = doctor.get(f"/api/documents/{document['id']}/file")
    assert response.status_code == 200
    assert response.content == SECRET
    assert "attachment" in response.headers["content-disposition"]


@pytest.mark.parametrize("role", ["doctor", "patient"])
def test_strangers_cannot_download(doctor, person, patient, role):
    document = upload(doctor, patient["id"]).json()
    assert person(role).get(f"/api/documents/{document['id']}/file").status_code == 404


def test_signed_out_users_cannot_download(doctor, client, patient):
    document = upload(doctor, patient["id"]).json()
    client.cookies.clear()
    assert client.get(f"/api/documents/{document['id']}/file").status_code == 401


def test_removed_clinicians_lose_downloads_at_once(doctor, person):
    account, record = linked_patient(doctor, person)
    document = upload(doctor, record["id"]).json()
    member = next(item for item in account.get(f"/api/patients/{record['id']}/care-team").json() if item["user_id"] == doctor.id)
    account.delete(f"/api/patients/{record['id']}/care-team/{member['id']}")
    assert doctor.get(f"/api/documents/{document['id']}/file").status_code == 404


def test_uploads_need_write_access(doctor, person):
    account, record = linked_patient(doctor, person)
    code = account.post(f"/api/patients/{record['id']}/share-codes", json={"purpose": "caregiver", "scope": "read"}).json()["code"]
    caregiver = person("patient")
    caregiver.post("/api/patients/join", json={"code": code})
    assert upload(caregiver, record["id"]).status_code == 403


def test_a_changed_file_is_detected(doctor, patient):
    document = upload(doctor, patient["id"]).json()
    file = stored_files()[0]
    damaged = bytearray(file.read_bytes())
    damaged[-5] ^= 0xFF
    file.write_bytes(bytes(damaged))
    response = doctor.get(f"/api/documents/{document['id']}/file")
    assert response.status_code == 500
    assert b"SYNTHETIC" not in response.content
    assert "integrity" in response.text
    db = connect()
    denied = db.execute("SELECT 1 FROM audit_events WHERE action = 'document.integrity_failure'").fetchone()
    db.close()
    assert denied is not None


def test_a_damaged_header_is_detected(doctor, patient):
    document = upload(doctor, patient["id"]).json()
    file = stored_files()[0]
    file.write_bytes(file.read_bytes()[:20])
    assert doctor.get(f"/api/documents/{document['id']}/file").status_code == 500


def test_a_file_moved_to_another_patient_will_not_open(doctor, patient):
    other = doctor.post("/api/patients", json={"first_name": "Sita", "last_name": "Devi"}).json()
    first = upload(doctor, patient["id"]).json()
    second = upload(doctor, other["id"], b"%PDF-1.4 different").json()
    first_path = documents.path_for(row(first["id"]))
    second_path = documents.path_for(row(second["id"]))
    second_path.write_bytes(first_path.read_bytes())
    assert doctor.get(f"/api/documents/{second['id']}/file").status_code == 500


def test_a_missing_file_is_reported(doctor, patient):
    document = upload(doctor, patient["id"]).json()
    stored_files()[0].unlink()
    assert doctor.get(f"/api/documents/{document['id']}/file").status_code == 500


def test_uploads_fail_closed_without_keys(doctor, patient, monkeypatch):
    monkeypatch.setattr(settings, "document_key_file", "")
    response = upload(doctor, patient["id"])
    assert response.status_code == 503
    assert stored_files() == []


def test_lab_uploads_fail_closed_without_keys(doctor, patient, monkeypatch):
    monkeypatch.setattr(settings, "document_key_file", "")
    response = doctor.post(
        "/api/addons/lablens/reports",
        data={"patient_id": patient["id"]},
        files={"file": ("r.pdf", SECRET, "application/pdf")},
    )
    assert response.status_code == 503
    assert stored_files() == []


def test_a_key_inside_the_repository_is_refused(monkeypatch):
    inside = Path(__file__).parents[2] / "backend" / "storage" / "keys.json"
    monkeypatch.setattr(settings, "document_key_file", str(inside))
    with pytest.raises(vault.VaultUnavailable, match="outside"):
        vault.encrypt(b"x", "c")


def test_a_key_next_to_the_documents_is_refused(client, monkeypatch, key_file):
    beside = settings.db_path.parent / "keys.json"
    beside.write_text(key_file.read_text())
    beside.chmod(0o600)
    monkeypatch.setattr(settings, "document_key_file", str(beside))
    with pytest.raises(vault.VaultUnavailable, match="outside"):
        vault.encrypt(b"x", "c")


def test_a_key_file_others_can_read_is_refused(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("loose") / "keys.json"
    path.write_text(json.dumps({"active": "a", "keys": {"a": vault.new_key()}}))
    path.chmod(0o644)
    monkeypatch.setattr(settings, "document_key_file", str(path))
    with pytest.raises(vault.VaultUnavailable, match="600"):
        vault.encrypt(b"x", "c")


@pytest.mark.parametrize("content", ["not json", "{}", '{"active": "a", "keys": {"a": "c2hvcnQ="}}'])
def test_a_broken_key_file_fails_closed(tmp_path_factory, monkeypatch, content):
    path = tmp_path_factory.mktemp("bad") / "keys.json"
    path.write_text(content)
    path.chmod(0o600)
    monkeypatch.setattr(settings, "document_key_file", str(path))
    with pytest.raises(vault.VaultUnavailable):
        vault.encrypt(b"x", "c")


def test_the_repository_holds_no_key_material():
    root = Path(__file__).parents[2]
    ignored = (root / ".gitignore").read_text()
    assert ".env" in ignored
    assert "backend/storage/" in ignored


def test_uploads_are_kept_in_memory_not_a_temp_file():
    from starlette.formparsers import MultiPartParser

    from backend.app import MAX_REQUEST_BYTES

    assert MultiPartParser.spool_max_size >= MAX_REQUEST_BYTES


def test_oversized_uploads_are_refused_before_reading(doctor, patient):
    response = doctor.post(
        f"/api/patients/{patient['id']}/documents",
        content=b"x",
        headers={"Content-Type": "multipart/form-data; boundary=x", "Content-Length": str(30 * 1024 * 1024)},
    )
    assert response.status_code == 413


def test_document_contents_never_reach_the_logs(doctor, patient, caplog):
    caplog.set_level(logging.DEBUG)
    document = upload(doctor, patient["id"]).json()
    doctor.get(f"/api/documents/{document['id']}/file")
    assert "SYNTHETIC-PATIENT-MARKER" not in caplog.text


def test_lab_processing_reads_the_encrypted_file(doctor, patient):
    from backend.tests.test_lablens import extraction, lab_pdf

    report = doctor.post(
        "/api/addons/lablens/reports",
        data={"patient_id": patient["id"]},
        files={"file": ("r.pdf", lab_pdf(), "application/pdf")},
    ).json()
    assert extraction(doctor, report["id"])["status"] == "needs_review"


def test_migration_dry_run_changes_nothing(client, doctor, patient):
    name = legacy_document(patient["id"])
    assert operator(["migrate-documents", "--dry-run"]) == 0
    db = connect()
    assert db.execute("SELECT encryption_state FROM clinical_documents").fetchone()[0] == "plaintext_legacy"
    db.close()
    assert (settings.documents_dir / patient["id"] / name).exists()


def test_migration_encrypts_verifies_and_keeps_the_original(doctor, patient):
    name = legacy_document(patient["id"])
    assert operator(["migrate-documents"]) == 0
    db = connect()
    document = dict(db.execute("SELECT * FROM clinical_documents").fetchone())
    db.close()
    assert document["encryption_state"] == "encrypted"
    assert document["legacy_stored_name"] == name
    assert (settings.documents_dir / patient["id"] / name).exists()
    assert documents.read(document) == b"%PDF-1.4 legacy synthetic report"
    assert doctor.get(f"/api/documents/{document['id']}/file").content == b"%PDF-1.4 legacy synthetic report"


def test_a_damaged_original_is_reported_and_left_alone(doctor, patient):
    name = legacy_document(patient["id"])
    (settings.documents_dir / patient["id"] / name).write_bytes(b"%PDF-1.4 changed")
    assert operator(["migrate-documents"]) == 1
    db = connect()
    assert db.execute("SELECT encryption_state FROM clinical_documents").fetchone()[0] == "plaintext_legacy"
    db.close()


def test_migration_without_keys_fails_and_keeps_everything(doctor, patient, monkeypatch):
    name = legacy_document(patient["id"])
    monkeypatch.setattr(settings, "document_key_file", "")
    assert operator(["migrate-documents"]) == 1
    assert (settings.documents_dir / patient["id"] / name).exists()


def test_running_migration_twice_is_safe(doctor, patient):
    legacy_document(patient["id"])
    assert operator(["migrate-documents"]) == 0
    assert operator(["migrate-documents"]) == 0


def test_originals_need_a_named_approval_to_delete(doctor, patient):
    name = legacy_document(patient["id"])
    operator(["migrate-documents"])
    assert operator(["delete-migrated-originals", "--retention-approval", " "]) == 1
    assert (settings.documents_dir / patient["id"] / name).exists()
    assert operator(["delete-migrated-originals", "--retention-approval", "RET-1"]) == 0
    assert not (settings.documents_dir / patient["id"] / name).exists()


def test_an_original_stays_if_its_copy_fails_the_check(doctor, patient):
    name = legacy_document(patient["id"])
    operator(["migrate-documents"])
    db = connect()
    document = dict(db.execute("SELECT * FROM clinical_documents").fetchone())
    db.close()
    documents.path_for(document).write_bytes(b"damaged")
    assert operator(["delete-migrated-originals", "--retention-approval", "RET-1"]) == 1
    assert (settings.documents_dir / patient["id"] / name).exists()


def test_key_rotation_keeps_documents_readable(doctor, patient, tmp_path_factory, monkeypatch, key_file):
    copy = tmp_path_factory.mktemp("rotate") / "keys.json"
    copy.write_text(key_file.read_text())
    copy.chmod(0o600)
    monkeypatch.setattr(settings, "document_key_file", str(copy))
    document = upload(doctor, patient["id"]).json()
    assert operator(["add-key", "--key-id", "next"]) == 0
    assert operator(["retire-key", "--key-id", "test1"]) == 1
    assert operator(["rewrap-documents"]) == 0
    assert row(document["id"])["key_id"] == "next"
    assert operator(["retire-key", "--key-id", "test1"]) == 0
    assert doctor.get(f"/api/documents/{document['id']}/file").content == SECRET


def test_verify_finds_a_damaged_file(doctor, patient):
    upload(doctor, patient["id"])
    assert operator(["verify-documents"]) == 0
    stored_files()[0].write_bytes(b"damaged")
    assert operator(["verify-documents"]) == 1


def test_backup_is_encrypted_and_restores(doctor, patient, tmp_path_factory):
    document = upload(doctor, patient["id"]).json()
    destination = tmp_path_factory.mktemp("backup") / "set"
    assert operator(["backup", "--to", str(destination)]) == 0
    assert not (destination / "database.plain").exists()
    assert b"SQLite format" not in (destination / "database.enc").read_bytes()
    for path in destination.rglob("*"):
        if path.is_file():
            assert b"SYNTHETIC-PATIENT-MARKER" not in path.read_bytes()
            assert b"Ravi" not in path.read_bytes()

    restored = tmp_path_factory.mktemp("restored")
    assert operator(["restore", "--source", str(destination), "--database", str(restored / "r.db"), "--documents", str(restored / "docs")]) == 0
    db = sqlite3.connect(restored / "r.db")
    assert db.execute("SELECT first_name FROM patients").fetchone()[0] == "Ravi"
    assert db.execute("SELECT id FROM clinical_documents").fetchone()[0] == document["id"]
    db.close()


def test_restore_refuses_to_overwrite(doctor, patient, tmp_path_factory):
    upload(doctor, patient["id"])
    destination = tmp_path_factory.mktemp("backup") / "set"
    operator(["backup", "--to", str(destination)])
    assert operator(["restore", "--source", str(destination), "--database", str(settings.db_path), "--documents", "/tmp/none"]) == 1


def test_restore_rejects_a_tampered_backup(doctor, patient, tmp_path_factory):
    upload(doctor, patient["id"])
    destination = tmp_path_factory.mktemp("backup") / "set"
    operator(["backup", "--to", str(destination)])
    victim = next(path for path in (destination / "documents").rglob("*") if path.is_file())
    victim.write_bytes(b"tampered")
    target = tmp_path_factory.mktemp("restore")
    assert operator(["restore", "--source", str(destination), "--database", str(target / "r.db"), "--documents", str(target / "d")]) == 1
    assert not (target / "r.db").exists()


def test_generate_key_refuses_unsafe_places(client, tmp_path_factory):
    assert operator(["generate-key", "--path", str(Path(__file__).parent / "x.json")]) == 1
    good = tmp_path_factory.mktemp("newkey") / "ring.json"
    assert operator(["generate-key", "--path", str(good)]) == 0
    assert oct(good.stat().st_mode & 0o777) == "0o600"
    assert operator(["generate-key", "--path", str(good)]) == 1


def test_consent_gated_ai_reading_still_runs_on_encrypted_files(doctor, patient, monkeypatch):
    from backend import llm
    from backend.tests.conftest import FakeAI
    from backend.tests.test_lablens import blank_pdf, extraction, scan_result

    grant(doctor, patient["id"], "ai_processing")
    monkeypatch.setattr(llm, "ask", FakeAI(scan_result()))
    report = doctor.post(
        "/api/addons/lablens/reports",
        data={"patient_id": patient["id"]},
        files={"file": ("r.pdf", blank_pdf(), "application/pdf")},
    ).json()
    assert extraction(doctor, report["id"])["extraction_method"] == "ai-vision"


def test_no_stray_plaintext_in_temp_folder(doctor, patient, tmp_path):
    before = set(os.listdir(tmp_path))
    upload(doctor, patient["id"])
    created = set(os.listdir(tmp_path)) - before
    assert created <= {"documents", "test.db-wal", "test.db-shm"}
