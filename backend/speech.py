"""Speech, through Groq. Whisper transcribes English and Hindi, and Orpheus reads English aloud.

Groq has no Hindi voice, so Hindi reading is done by the browser's own voice.
Whisper can invent words from silence, so segments it marks as probably not speech
are dropped rather than written into a clinical transcript.
"""
import re
import time
from typing import Literal, Optional

import httpx

from backend import settings_store
from backend.config import settings
from backend.integrations import execution

TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "troy"
MAX_SPEECH_CHARS = 2500
TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 2
RETRY_PAUSE_SECONDS = 0.5
RETRY_STATUSES = (429, 500, 502, 503, 504)
NO_SPEECH_LIMIT = 0.6
# The extension tells Whisper the format, and browsers differ: Safari records mp4.
EXTENSIONS = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/mpeg": "mp3", "audio/wav": "wav"}
LANGUAGE_CODES = {"en-IN": "en", "hi-IN": "hi"}

Language = Literal["en-IN", "hi-IN"]
VOICED_LANGUAGES = ("en-IN",)  # anything else is read by the browser


class SpeechUnavailable(Exception):
    """Speech couldn't be reached or isn't set up."""


class SpeechRejected(Exception):
    """The service refused this audio or text, for example because it's too long."""


def _key() -> str:
    key = settings_store.api_key()
    if not key:
        raise SpeechUnavailable("No API key")
    return key


def _post(path: str, **options) -> httpx.Response:
    """One retry for a blip. Anything else is reported, and no text is ever invented."""
    url = f"{settings.ai_base_url}{path}"
    headers = {"Authorization": f"Bearer {_key()}"}
    failure: Optional[SpeechUnavailable] = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_PAUSE_SECONDS)
        try:
            response = httpx.post(url, headers=headers, timeout=TIMEOUT_SECONDS, **options)
        except httpx.HTTPError as error:
            failure = SpeechUnavailable(type(error).__name__)
            continue
        if response.status_code in (400, 413, 422):
            raise SpeechRejected(f"Speech service returned {response.status_code}")
        if response.status_code in RETRY_STATUSES:
            failure = SpeechUnavailable(f"Speech service returned {response.status_code}")
            continue
        if response.status_code != 200:
            raise SpeechUnavailable(f"Speech service returned {response.status_code}")
        return response
    raise failure


def _spoken_text(body: dict) -> str:
    segments = body.get("segments")
    if not isinstance(segments, list) or not segments:
        return body["text"]
    kept = [
        segment.get("text", "")
        for segment in segments
        if isinstance(segment, dict) and float(segment.get("no_speech_prob") or 0) < NO_SPEECH_LIMIT
    ]
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def transcribe(audio: bytes, content_type: str, language: Language) -> str:
    run = execution.AgentRun("speech_to_text")
    model = settings_store.model_for("transcription")
    try:
        text = _transcribe(audio, content_type, language, model)
    except (SpeechUnavailable, SpeechRejected) as error:
        run.tried("groq", model, type(error).__name__)
        run.finish("failed")
        raise
    run.tried("groq", model, "succeeded")
    run.finish("succeeded")
    return text


def _transcribe(audio: bytes, content_type: str, language: Language, model: str) -> str:
    base_type = content_type.split(";")[0].strip()
    response = _post(
        "/audio/transcriptions",
        files={"file": (f"audio.{EXTENSIONS.get(base_type, 'webm')}", audio, content_type)},
        data={
            "model": model,
            "language": LANGUAGE_CODES[language],
            "response_format": "verbose_json",
            "temperature": "0",
        },
    )
    try:
        text = _spoken_text(response.json())
    except (ValueError, KeyError, TypeError, AttributeError):
        raise SpeechUnavailable("The speech service sent an unexpected reply") from None
    if not isinstance(text, str):
        raise SpeechUnavailable("The speech service's reply had no text")
    return text


def speak(text: str, language: Language) -> bytes:
    """Returns WAV audio. Hindi isn't available here: the browser reads it instead."""
    if len(text) > MAX_SPEECH_CHARS:
        raise SpeechRejected("Text is too long to read aloud in one request")
    if language != "en-IN":
        raise SpeechUnavailable("No voice for this language")
    response = _post(
        "/audio/speech",
        json={"model": TTS_MODEL, "input": text, "voice": TTS_VOICE, "response_format": "wav"},
    )
    if not response.content:
        raise SpeechUnavailable("The speech service sent no audio")
    return response.content
