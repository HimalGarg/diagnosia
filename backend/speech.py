"""Speech in Indian languages, through Sarvam."""
import base64
from typing import Literal

import httpx

from backend.config import settings

STT_URL = "https://api.sarvam.ai/speech-to-text"
TTS_URL = "https://api.sarvam.ai/text-to-speech"
STT_MODEL = "saaras:v3"
TTS_MODEL = "bulbul:v4-flash"
MAX_SPEECH_CHARS = 2500  # Sarvam's limit for one request
MAX_ANSWER_SECONDS = 30  # Sarvam's limit for one transcription request

Language = Literal["en-IN", "hi-IN"]
# bulbul:v4-flash needs its own voice IDs; these two are Sarvam's medical voices.
VOICES = {"en-IN": "ritu_en_medical", "hi-IN": "ritu_hi_medical"}


class SpeechUnavailable(Exception):
    """Sarvam couldn't be reached or isn't set up."""


class SpeechRejected(Exception):
    """Sarvam refused this audio or text, for example because it's too long."""


def _check(response: httpx.Response) -> dict:
    if response.status_code in (400, 422):
        raise SpeechRejected(response.text[:200])
    if response.status_code != 200:
        raise SpeechUnavailable(f"Sarvam returned {response.status_code}")
    return response.json()


def _headers() -> dict:
    if not settings.sarvam_key:
        raise SpeechUnavailable("SARVAM_API_KEY is not set")
    return {"api-subscription-key": settings.sarvam_key}


def transcribe(audio: bytes, content_type: str, language: Language) -> str:
    try:
        response = httpx.post(
            STT_URL,
            headers=_headers(),
            files={"file": ("answer.webm", audio, content_type)},
            data={"model": STT_MODEL, "language_code": language},
            timeout=60,
        )
    except httpx.HTTPError as error:
        raise SpeechUnavailable(str(error)) from error
    return _check(response)["transcript"]


def speak(text: str, language: Language) -> bytes:
    """Returns WAV audio."""
    if len(text) > MAX_SPEECH_CHARS:
        raise SpeechRejected("Text is too long to read aloud in one request")
    try:
        response = httpx.post(
            TTS_URL,
            headers=_headers(),
            json={
                "text": text,
                "language_code": language,
                "model": TTS_MODEL,
                "speaker": VOICES[language],
            },
            timeout=60,
        )
    except httpx.HTTPError as error:
        raise SpeechUnavailable(str(error)) from error
    return base64.b64decode("".join(_check(response)["audios"]))
