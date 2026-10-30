"""Checks what we send to Claude and Sarvam, using fake servers, so no keys are needed."""
import base64
import json

import anthropic
import httpx
import httpx2
import pytest
from pydantic import BaseModel

from backend import llm, speech
from backend.config import settings


class Answer(BaseModel):
    text: str


def claude_reply(text='{"text": "hello"}', stop_reason="end_turn"):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def fake_claude(monkeypatch, handler):
    client = anthropic.Anthropic(
        api_key="test",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    monkeypatch.setattr(llm, "get_client", lambda: client)


def test_request_to_claude_has_the_agreed_settings(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        return httpx2.Response(200, json=claude_reply())

    fake_claude(monkeypatch, handler)
    result = llm.ask("Be careful.", "Say hello", Answer, effort="low")

    assert result.text == "hello"
    assert seen["body"]["model"] == settings.llm_model
    assert seen["body"]["fallbacks"] == "default"
    assert llm.FALLBACK_BETA in seen["beta"]
    assert seen["body"]["output_config"]["effort"] == "low"
    assert seen["body"]["output_config"]["format"]["type"] == "json_schema"
    assert seen["body"]["system"] == "Be careful."


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_declined_or_cut_off_answers_are_not_used(monkeypatch, stop_reason):
    fake_claude(
        monkeypatch,
        lambda request: httpx2.Response(200, json=claude_reply(text="", stop_reason=stop_reason)),
    )
    with pytest.raises(llm.AIDeclined):
        llm.ask("system", "content", Answer)


def test_server_errors_become_unavailable(monkeypatch):
    fake_claude(
        monkeypatch,
        lambda request: httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}}),
    )
    with pytest.raises(llm.AIUnavailable):
        llm.ask("system", "content", Answer)


class FakeSarvam:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_speech_needs_a_key(monkeypatch):
    monkeypatch.setattr(settings, "sarvam_key", "")
    with pytest.raises(speech.SpeechUnavailable):
        speech.speak("hello", "en-IN")


def test_transcribe_sends_the_documented_fields(monkeypatch):
    monkeypatch.setattr(settings, "sarvam_key", "key")
    fake = FakeSarvam(httpx.Response(200, json={"transcript": "बुखार है", "language_code": "hi-IN"}))
    monkeypatch.setattr(speech.httpx, "post", fake)

    assert speech.transcribe(b"audio", "audio/webm", "hi-IN") == "बुखार है"
    call = fake.calls[0]
    assert call["url"] == speech.STT_URL
    assert call["headers"]["api-subscription-key"] == "key"
    assert call["data"] == {"model": speech.STT_MODEL, "language_code": "hi-IN"}
    assert call["files"]["file"][1] == b"audio"


def test_speak_sends_the_documented_fields_and_decodes_audio(monkeypatch):
    monkeypatch.setattr(settings, "sarvam_key", "key")
    wav = b"RIFF-fake-wav"
    fake = FakeSarvam(httpx.Response(200, json={"audios": [base64.b64encode(wav).decode()]}))
    monkeypatch.setattr(speech.httpx, "post", fake)

    assert speech.speak("नमस्ते", "hi-IN") == wav
    body = fake.calls[0]["json"]
    assert body == {
        "text": "नमस्ते",
        "language_code": "hi-IN",
        "model": speech.TTS_MODEL,
        "speaker": "ritu_hi_medical",
    }


def test_too_long_audio_is_reported_as_rejected(monkeypatch):
    monkeypatch.setattr(settings, "sarvam_key", "key")
    monkeypatch.setattr(speech.httpx, "post", FakeSarvam(httpx.Response(422, text="audio over 30 seconds")))
    with pytest.raises(speech.SpeechRejected):
        speech.transcribe(b"audio", "audio/webm", "en-IN")


def test_text_over_the_limit_is_refused_before_paying_for_it(monkeypatch):
    monkeypatch.setattr(settings, "sarvam_key", "key")
    fake = FakeSarvam(httpx.Response(200, json={"audios": []}))
    monkeypatch.setattr(speech.httpx, "post", fake)
    with pytest.raises(speech.SpeechRejected):
        speech.speak("a" * (speech.MAX_SPEECH_CHARS + 1), "en-IN")
    assert fake.calls == []
