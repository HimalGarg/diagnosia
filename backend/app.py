"""The Diagnosia web app: the API plus the public site in web/.

Run from the project folder with:  uvicorn backend.app:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import auth, llm, speech
from backend.config import WEB_DIR
from backend.db import init_db
from backend.prescription import routes as prescription
from backend.skin import routes as skin

logger = logging.getLogger("diagnosia")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Diagnosia", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(auth.router)
app.include_router(skin.router)
app.include_router(prescription.router)


def _friendly_failure(status_code: int, message: str):
    # Details go to the server log only: they can hold configuration or patient text.
    async def handler(_: Request, error: Exception) -> JSONResponse:
        logger.warning("%s: %s", type(error).__name__, error)
        return JSONResponse({"detail": message}, status_code=status_code)

    return handler


app.add_exception_handler(
    llm.AIUnavailable, _friendly_failure(503, "The AI service isn't available right now. Please try again.")
)
app.add_exception_handler(
    llm.AIDeclined, _friendly_failure(502, "The AI couldn't process this. Please try again.")
)
app.add_exception_handler(
    speech.SpeechUnavailable, _friendly_failure(503, "The speech service isn't available right now.")
)
app.add_exception_handler(
    speech.SpeechRejected,
    _friendly_failure(422, f"Please keep each answer under {speech.MAX_ANSWER_SECONDS} seconds and try again."),
)

# Must stay last: it answers every path that isn't an API route.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
