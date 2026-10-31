"""The API for patient packs: one searchable view of a patient's history.

Doctors get the brief and questions. A patient sees what's in their pack, but not an AI's reading of
it, because the AI never tells a patient what something means.
"""
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from backend.auth import require_doctor, require_user
from backend.careflow import events
from backend.db import get_db
from backend.integrations import meko
from backend.patient_pack import brief, sync
from backend.patient_pack.access import open_pack
from backend.records import care
from backend.records.store import rows

router = APIRouter(prefix="/api/patients/{patient_id}/pack")

UNAVAILABLE = "This patient's pack can't be reached right now, so nothing can be said about what it holds."


def _switched_on() -> Optional[meko.MekoClient]:
    """Never with real patient data. There's no agreement with Meko, and this can't be overridden."""
    return meko.pack_client() if events.active() else None


def _ready(db: sqlite3.Connection, patient_id: str) -> meko.MekoClient:
    client = _switched_on()
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Patient packs aren't switched on.")
    care.require_consent(db, patient_id, sync.STORE_PURPOSE)
    return client


@router.get("")
def pack_status(patient_id: str, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    open_pack(db, user, patient_id, "pack.read")
    pack = db.execute("SELECT state FROM patient_packs WHERE patient_id = ?", (patient_id,)).fetchone()
    items = rows(
        db.execute(
            "SELECT id, source_type, source_id, state, last_error, created_at FROM pack_items WHERE patient_id = ? ORDER BY created_at DESC",
            (patient_id,),
        )
    )
    return {
        "enabled": _switched_on() is not None,
        "stored": sync.has_storage_consent(db, patient_id),
        "shared": care.active_consent(db, patient_id, sync.SHARE_PURPOSE) is not None,
        "state": pack["state"] if pack else "none",
        "still_syncing": brief.syncing(db, patient_id),
        "items": [{**item, "label": brief.LABELS[item["source_type"]]} for item in items],
    }


@router.get("/items/{item_id}")
def pack_item(patient_id: str, item_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """The record behind a citation, so a doctor can check a statement against what was written."""
    open_pack(db, user, patient_id, "pack.item.read")
    item = db.execute("SELECT * FROM pack_items WHERE id = ? AND patient_id = ? AND state = 'ready'", (item_id, patient_id)).fetchone()
    source = brief.source_text(db, item) if item else None
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find that record.")
    return {key: source[key] for key in ("label", "date", "text", "source_type", "source_id")}


@router.post("/brief")
def pack_brief(patient_id: str, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    open_pack(db, user, patient_id, "pack.brief")
    _ready(db, patient_id)
    return brief.build_brief(db, patient_id)


@router.post("/ask")
def pack_ask(
    patient_id: str, request: brief.Question, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    open_pack(db, user, patient_id, "pack.ask")
    client = _ready(db, patient_id)
    pack = db.execute("SELECT datapack_id FROM patient_packs WHERE patient_id = ? AND state = 'active'", (patient_id,)).fetchone()
    if pack is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This patient's pack hasn't been built yet.")
    try:
        return brief.answer(db, client, pack["datapack_id"], patient_id, request.question)
    except meko.MekoUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
