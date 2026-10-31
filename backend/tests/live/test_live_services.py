"""Calls real services. Skipped unless asked for, and only ever sends synthetic data.

Public references:  DIAGNOSIA_LIVE_TESTS=1 pytest -m live backend/tests/live
Groq also needs a real key in GROQ_API_KEY.
"""
import io
import math
import os
import struct
import wave

import httpx
import pytest
from pydantic import BaseModel

from backend import llm, settings_store, speech
from backend.config import settings
from backend.diagnofast import agent
from backend.medguard import references

pytestmark = pytest.mark.live
ENABLED = os.environ.get("DIAGNOSIA_LIVE_TESTS") == "1"
SYNTHETIC_TRANSCRIPT = "Synthetic test. The speaker says only that this is a test sentence."


class Echo(BaseModel):
    word: str


class MemoryDb:
    """Stands in for the cache, so these tests touch no database."""

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return None


def needs_live():
    if not ENABLED:
        pytest.skip("Set DIAGNOSIA_LIVE_TESTS=1 to call real services")


def test_rxnorm_finds_a_well_known_ingredient():
    needs_live()
    result = references.rxnorm_ingredients(MemoryDb(), "tylenol")
    assert result["available"] is True
    assert any("cetaminophen" in name for name in result["ingredients"])
    assert result["retrieved_at"]


def test_rxnorm_handles_a_name_that_does_not_exist():
    needs_live()
    result = references.rxnorm_ingredients(MemoryDb(), "zzzsyntheticnotadrug")
    assert result["available"] is True
    assert result["ingredients"] == []


def test_openfda_label_search_replies_in_the_expected_shape():
    needs_live()
    result = references.label_mentions(MemoryDb(), "warfarin", "ibuprofen")
    assert result["available"] is True
    assert isinstance(result["mentioned"], bool)
    assert "not a complete" in result["limitation"]


def test_openfda_handles_a_name_that_does_not_exist():
    needs_live()
    result = references.label_mentions(MemoryDb(), "zzzsyntheticnotadrug", "zzzsyntheticother")
    assert result["available"] is True
    assert result["mentioned"] is False


def test_rxnav_still_serves_the_routes_we_use():
    needs_live()
    response = httpx.get(f"{references.RXNAV_URL}/rxcui.json", params={"name": "metformin", "search": 2}, timeout=10)
    assert response.status_code == 200
    assert "idGroup" in response.json()


def needs_key():
    needs_live()
    if not settings.groq_api_key:
        pytest.skip("No real Groq key")


@pytest.mark.parametrize("model", ["openai/gpt-oss-120b", "openai/gpt-oss-20b"])
def test_groq_returns_strict_structured_output(monkeypatch, model):
    needs_key()
    monkeypatch.setattr(settings_store, "model_for", lambda tool: model)
    assert llm.ask("Reply with the word given.", "The word is synthetic.", Echo, tool="clinscribe_note", max_tokens=400).word


def test_groq_reads_a_synthetic_image(monkeypatch):
    needs_key()
    from PIL import Image

    from backend.prescription.analyzer import prepare_photo

    monkeypatch.setattr(settings_store, "model_for", lambda tool: "qwen/qwen3.8-27b")
    encoded, media_type = prepare_photo(Image.new("RGB", (64, 64), "white"))
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}},
        {"type": "text", "text": "Give the main colour of this image as one word."},
    ]
    assert llm.ask("Answer briefly.", content, Echo, tool="lablens_read", max_tokens=400).word


def _synthetic_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(n / 20))) for n in range(16000)))
    return buffer.getvalue()


def test_groq_accepts_synthetic_audio(monkeypatch):
    needs_key()
    monkeypatch.setattr(settings_store, "model_for", lambda tool: "whisper-large-v3")
    assert isinstance(speech.transcribe(_synthetic_wav(), "audio/wav", "en-IN"), str)


def test_groq_speaks_a_synthetic_sentence():
    needs_key()
    assert speech.speak(SYNTHETIC_TRANSCRIPT, "en-IN")


def test_groq_takes_a_diagnofast_turn_in_hindi(monkeypatch):
    needs_key()
    form = agent.load_form("general_physician")
    conversation = [{"question": form["fields"][0]["question"]["hi-IN"], "answer": "तीन दिन से बुखार है"}]
    turn = agent.next_turn(form, {}, conversation, "hi-IN")
    assert isinstance(turn, agent.Turn)


def test_groq_writes_a_diagnofast_report(monkeypatch):
    needs_key()
    form = agent.load_form("general_physician")
    report = agent.write_report(form, {"chief_complaint": "Synthetic fever"}, [{"question": "What is the problem?", "answer": "Synthetic fever for three days"}], None)
    assert isinstance(report, agent.Report)


def needs_gemini():
    needs_live()
    if not settings.gemini_api_key:
        pytest.skip("No real Gemini key")


def test_gemini_returns_structured_output_and_reads_an_image(monkeypatch):
    needs_gemini()
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert llm.ask("Reply with the word given.", "The word is synthetic.", Echo, tool="clinscribe_note", max_tokens=400).word == "synthetic"


def test_a_bad_groq_key_falls_back_to_a_real_gemini_answer(monkeypatch):
    needs_gemini()
    monkeypatch.setattr(settings, "groq_api_key", "gsk_deliberately_wrong_key")
    assert llm.ask("Reply with the word given.", "The word is synthetic.", Echo, tool="clinscribe_note", max_tokens=400).word == "synthetic"
