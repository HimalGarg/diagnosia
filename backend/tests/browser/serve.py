"""A test server for the browser checks: synthetic data, throwaway storage, faked speech.

Run by run_microphone.mjs. Speech failures are set with SPEECH_MODE or /test/speech-mode.
Never point this at a real database.
"""
import os
import sys

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from backend import speech
from backend.app import app

mode = {"value": "ok", "calls": 0, "bytes": 0}


def fake_transcribe(audio: bytes, content_type: str, language: str) -> str:
    mode["calls"] += 1
    mode["bytes"] += len(audio)
    if mode["value"] == "fail":
        raise speech.SpeechUnavailable("synthetic outage")
    if mode["value"] == "empty":
        return ""
    return f"Synthetic line {mode['calls']}"


async def set_mode(request: Request) -> JSONResponse:
    mode["value"] = request.query_params.get("value", "ok")
    return JSONResponse(mode)


async def read_mode(_: Request) -> JSONResponse:
    return JSONResponse(mode)


speech.transcribe = fake_transcribe
app.router.routes.insert(0, Route("/test/speech-mode", set_mode, methods=["POST"]))
app.router.routes.insert(0, Route("/test/speech-state", read_mode))

if __name__ == "__main__":
    if "test-run" not in os.environ.get("DIAGNOSIA_DB", ""):
        sys.exit("Use a throwaway DIAGNOSIA_DB containing 'test-run'.")
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
