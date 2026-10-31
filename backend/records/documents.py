"""Stores original clinical files, encrypted. They are never served from web/, and never edited."""
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, status

from backend.config import settings
from backend.private_files import create_private_dir, make_private
from backend.records import vault
from backend.records.store import insert, new_id, now_iso, one

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
SIGNATURES = {
    b"%PDF-": ("application/pdf", ".pdf"),
    b"\xff\xd8\xff": ("image/jpeg", ".jpg"),
    b"\x89PNG\r\n\x1a\n": ("image/png", ".png"),
}


def sniff(data: bytes) -> tuple:
    """Trusts the file's own bytes, not the name or type the browser sent."""
    for signature, details in SIGNATURES.items():
        if data.startswith(signature):
            return details
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Please upload a PDF, JPG or PNG file.")


def path_for(document: dict) -> Path:
    return settings.documents_dir / document["patient_id"] / document["stored_name"]


def context_for(patient_id: str, document_id: str) -> str:
    return f"document:{patient_id}:{document_id}"


def write_private(path: Path, data: bytes) -> None:
    """Owner-only, and never visible half written."""
    create_private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{new_id()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            make_private(temporary)  # before any data, because Windows ignores the mode above
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def store(
    db: sqlite3.Connection,
    patient_id: str,
    data: bytes,
    filename: str,
    document_type: str,
    source_type: str,
    uploaded_by: int,
    encounter_id: Optional[str] = None,
) -> tuple:
    """Returns the document and whether it was new. The same file twice is one document."""
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Files must be under 20 MB.")
    content_type, _ = sniff(data)
    digest = hashlib.sha256(data).hexdigest()
    existing = db.execute(
        "SELECT * FROM clinical_documents WHERE patient_id = ? AND sha256 = ?", (patient_id, digest)
    ).fetchone()
    if existing is not None:
        return dict(existing), False

    document_id = new_id()
    stored_name = f"{document_id}.enc"
    sealed = vault.encrypt(data, context_for(patient_id, document_id))
    write_private(settings.documents_dir / patient_id / stored_name, sealed)
    insert(
        db,
        "clinical_documents",
        id=document_id,
        patient_id=patient_id,
        encounter_id=encounter_id,
        document_type=document_type,
        filename=Path(filename or "document").name[:200],
        content_type=content_type,
        size_bytes=len(data),
        sha256=digest,
        stored_name=stored_name,
        source_type=source_type,
        encryption_state="encrypted",
        key_id=vault.key_id_of(sealed),
        uploaded_by=uploaded_by,
        created_at=now_iso(),
    )
    return one(db.execute("SELECT * FROM clinical_documents WHERE id = ?", (document_id,)).fetchone()), True


def read(document: dict) -> bytes:
    """Decrypts and proves the content is what was stored."""
    try:
        raw = path_for(document).read_bytes()
    except OSError:
        raise vault.IntegrityFailure("The stored file is missing") from None
    if document["encryption_state"] == "encrypted":
        data = vault.decrypt(raw, context_for(document["patient_id"], document["id"]))
    else:
        data = raw  # awaiting migration: see operator_cli migrate-documents
    if hashlib.sha256(data).hexdigest() != document["sha256"]:
        raise vault.IntegrityFailure("The stored file doesn't match its recorded checksum")
    return data


def public(document: dict) -> dict:
    return {key: value for key, value in document.items() if key not in ("stored_name", "legacy_stored_name", "key_id")}
