"""The Diagnosia web app: the API plus the public site in web/.

Run from the project folder with:  uvicorn backend.app:app --reload
"""
import asyncio
import logging
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser

from backend import auth, llm, safety, settings_store, speech
from backend.careflow import delivery as careflow_delivery
from backend.careflow import events as careflow_events
from backend.careflow import live as careflow_live
from backend.careflow import routes as careflow
from backend.clinscribe import routes as clinscribe
from backend.config import WEB_DIR
from backend.db import connect, init_db
from backend.diagnofast import routes as diagnofast
from backend.dischargecare import routes as dischargecare
from backend.integrations import execution
from backend.lablens import extract as lablens_jobs
from backend.lablens import mappings as lablens_mappings
from backend.lablens import routes as lablens
from backend.patient_pack import routes as patient_pack
from backend.patient_pack import sync as pack_sync
from backend.prescription import routes as prescription
from backend.prescription.medicines import get_medicine_list
from backend.records import audit, care, vault
from backend.records import routes as records
from backend.records import staff
from backend.referralloop import routes as referralloop
from backend.reminders import routes as reminders
from backend.reminders import service as reminder_service
from backend.skin import routes as skin

logger = logging.getLogger("diagnosia")

SWEEP_SECONDS = 300
DELIVERY_SECONDS = 5
MAX_REQUEST_BYTES = 25 * 1024 * 1024
MAX_PHOTOS_REQUEST_BYTES = 45 * 1024 * 1024  # four prescription photos of 10 MB, plus the form
# Uploads stay in memory rather than in a plaintext temporary file.
MultiPartParser.spool_max_size = MAX_PHOTOS_REQUEST_BYTES
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
}
CARE_PAGE_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' https://assets.website-files.com; connect-src 'self'; media-src 'self' blob:; "
    "frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
)


def _sweep_once() -> None:
    connection = connect()
    try:
        care.escalate_overdue(connection)
        reminder_service.process_due(connection)
        connection.commit()
    finally:
        connection.close()


async def _sweep_forever() -> None:
    """Overdue high-priority work is escalated even if nobody has the app open."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await run_in_threadpool(_sweep_once)
        except sqlite3.Error as error:
            logger.warning("Escalation sweep failed: %s", error)


async def _deliver_forever() -> None:
    """Sends outbox events on. A destination that's down delays its own channel, and nothing else."""
    while True:
        await asyncio.sleep(DELIVERY_SECONDS)
        try:
            await run_in_threadpool(careflow_delivery.deliver_once)
        except Exception as error:  # An optional integration must never stop the app.
            logger.warning("CareFlow delivery pass failed: %s", type(error).__name__)
        try:
            await run_in_threadpool(pack_sync.run_once)
        except Exception as error:  # An optional integration must never stop the app.
            logger.warning("Patient pack pass failed: %s", type(error).__name__)


def _prepare_content() -> None:
    """Loads starter content as unreviewed, then refuses a deployment that asks for too much."""
    connection = connect()
    try:
        lablens_mappings.seed(connection)
        connection.commit()
        safety.assert_safe_to_start(connection)
    finally:
        connection.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    _prepare_content()
    lablens_jobs.recover_interrupted()
    execution.recover_interrupted()
    # Takes about 10 seconds, so load it now rather than during the first upload.
    threading.Thread(target=get_medicine_list, daemon=True).start()
    sweep = asyncio.create_task(_sweep_forever())
    delivering = asyncio.create_task(_deliver_forever())
    watching = careflow_live.start()
    yield
    sweep.cancel()
    delivering.cancel()
    if watching:
        await run_in_threadpool(watching.stop)


app = FastAPI(title="Diagnosia", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(auth.router)
app.include_router(records.router)
app.include_router(staff.router)
app.include_router(lablens_mappings.router)
app.include_router(settings_store.router)
app.include_router(careflow.router)
app.include_router(patient_pack.router)


def _tool(router, name: str) -> None:
    app.include_router(router, dependencies=[Depends(settings_store.tool_enabled(name))])


_tool(clinscribe.router, "clinscribe")
_tool(lablens.router, "lablens")
_tool(dischargecare.router, "discharge")
_tool(referralloop.router, "referrals")
_tool(diagnofast.router, "diagnofast")
_tool(prescription.router, "prescription")
_tool(skin.router, "skin")
app.include_router(reminders.router)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    """Refuses cross-site writes, and gives each request an ID for the audit log."""
    origin = request.headers.get("origin")
    writing = request.method not in ("GET", "HEAD", "OPTIONS")
    if writing and origin and urlsplit(origin).netloc != request.headers.get("host"):
        return JSONResponse({"detail": "Cross-site requests aren't allowed."}, status_code=403)
    if writing and request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Cross-site requests aren't allowed."}, status_code=403)
    if writing and request.url.path.startswith("/api/"):
        length = request.headers.get("content-length")
        is_upload = request.headers.get("content-type", "").startswith("multipart/form-data")
        if is_upload and length is None:
            return JSONResponse({"detail": "Uploads need a known size."}, status_code=411)
        limit = MAX_PHOTOS_REQUEST_BYTES if request.url.path == "/api/prescription/read" else MAX_REQUEST_BYTES
        if length is not None and (not length.isdigit() or int(length) > limit):
            return JSONResponse({"detail": "This upload is too large."}, status_code=413)
    request_id = uuid.uuid4().hex
    token = audit.request_id.set(request_id)
    try:
        response = await call_next(request)
    finally:
        audit.request_id.reset(token)
        careflow_events.forget(request_id)
    response.headers["X-Request-ID"] = request_id
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if request.url.path == "/care.html":
        response.headers["Content-Security-Policy"] = CARE_PAGE_POLICY
    return response


def _friendly_failure(status_code: int, message: str):
    # Only the type is logged. An error's own text can hold configuration or patient details.
    async def handler(_: Request, error: Exception) -> JSONResponse:
        logger.warning("%s on request %s", type(error).__name__, audit.request_id.get())
        careflow_events.request_failed(audit.request_id.get(), error)
        return JSONResponse({"detail": message}, status_code=status_code)

    return handler


app.add_exception_handler(
    llm.AIUnavailable, _friendly_failure(503, "The AI service isn't available right now. Please try again.")
)
app.add_exception_handler(
    llm.AINotConfigured, _friendly_failure(503, "AI isn't set up yet. An administrator can add a free Groq or Gemini key in Settings.")
)
app.add_exception_handler(
    llm.AIRateLimited, _friendly_failure(503, "The free AI limit has been reached. Please try again later.")
)
app.add_exception_handler(
    llm.AIDeclined, _friendly_failure(502, "The AI couldn't process this. Please try again.")
)
app.add_exception_handler(
    speech.SpeechUnavailable, _friendly_failure(503, "The speech service isn't available right now.")
)
app.add_exception_handler(
    speech.SpeechRejected, _friendly_failure(422, "The speech service couldn't process that audio. Please try again."),
)
app.add_exception_handler(
    sqlite3.IntegrityError, _friendly_failure(409, "That refers to something that doesn't exist or already does.")
)
app.add_exception_handler(
    vault.VaultUnavailable, _friendly_failure(503, "Secure document storage isn't set up, so nothing was stored or read.")
)
app.add_exception_handler(
    vault.IntegrityFailure, _friendly_failure(500, "This file failed its integrity check and wasn't opened.")
)

# Must stay last: it answers every path that isn't an API route.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
