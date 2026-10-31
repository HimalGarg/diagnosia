"""Every external service, tested against every way it can misbehave, using fakes only.

No service may produce an invented clinical result, and no failure may put patient
text in the log.
"""
import logging

import httpx
import pytest
from pydantic import BaseModel

from backend import llm, speech
from backend.clinscribe import drafting
from backend.clinscribe.drafting import NoteDraft
from backend.config import settings
from backend.lablens import extract
from backend.tests.conftest import FakeAI, grant
from backend.tests.test_lablens import blank_pdf, extraction

PHI = "SYNTHETIC-PATIENT-TEXT"


class Answer(BaseModel):
    text: str


class Sequence:
    """Plays back replies in order, and counts calls."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        reply = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


def groq_reply(content='{"text": "hello"}', finish_reason="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]})


@pytest.fixture
def groq_ready(client, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)


def fail_with(status_code, headers=None):
    return httpx.Response(status_code, headers=headers or {}, json={"error": {"message": PHI}})


@pytest.mark.parametrize(
    "reply",
    [fail_with(401), fail_with(403), fail_with(500), fail_with(503), httpx.TimeoutException("slow"), httpx.ConnectError("no route")],
    ids=["auth", "forbidden", "server-error", "unavailable", "timeout", "network"],
)
def test_groq_failures_are_reported_as_unavailable(monkeypatch, groq_ready, reply):
    monkeypatch.setattr(httpx, "post", Sequence([reply]))
    with pytest.raises(llm.AIUnavailable) as caught:
        llm.ask("system", "content", Answer, tool="clinscribe_note")
    assert PHI not in str(caught.value)


def test_a_rate_limit_retries_once_then_says_so(monkeypatch, groq_ready):
    fake = Sequence([fail_with(429, {"retry-after": "1"})])
    monkeypatch.setattr(httpx, "post", fake)
    with pytest.raises(llm.AIRateLimited):
        llm.ask("system", "content", Answer, tool="clinscribe_note")
    assert fake.calls == llm.MAX_ATTEMPTS


def test_a_short_rate_limit_recovers(monkeypatch, groq_ready):
    monkeypatch.setattr(httpx, "post", Sequence([fail_with(429, {"retry-after": "1"}), groq_reply()]))
    assert llm.ask("system", "content", Answer, tool="clinscribe_note").text == "hello"


def test_a_daily_limit_doesnt_wait(monkeypatch, groq_ready):
    fake = Sequence([fail_with(429, {"retry-after": "3600"})])
    monkeypatch.setattr(httpx, "post", fake)
    with pytest.raises(llm.AIRateLimited):
        llm.ask("system", "content", Answer, tool="clinscribe_note")
    assert fake.calls == 1


def test_auth_failures_are_not_retried(monkeypatch, groq_ready):
    fake = Sequence([fail_with(401)])
    monkeypatch.setattr(httpx, "post", fake)
    with pytest.raises(llm.AIUnavailable):
        llm.ask("system", "content", Answer, tool="clinscribe_note")
    assert fake.calls == 1


@pytest.mark.parametrize(
    "reply",
    [
        groq_reply(content=""),
        groq_reply(content="this is not json"),
        groq_reply(content='{"wrong": "shape"}'),
        groq_reply(content='{"text": 5}'),
        groq_reply(content='{"text": "cut of'),
        groq_reply(finish_reason="length"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={}),
        httpx.Response(200, content=b"not json"),
        fail_with(400),
        fail_with(413),
    ],
    ids=["empty", "not-json", "wrong-shape", "wrong-type", "partial", "cut-off", "no-choices", "empty-body", "garbage", "bad-request", "too-large"],
)
def test_replies_that_cant_be_trusted_are_declined(monkeypatch, groq_ready, reply):
    monkeypatch.setattr(httpx, "post", Sequence([reply]))
    with pytest.raises(llm.AIDeclined):
        llm.ask("system", "content", Answer, tool="clinscribe_note")


def test_the_timeout_is_explicit():
    assert llm.REQUEST_TIMEOUT_SECONDS == 120
    assert llm.MAX_ATTEMPTS == 2


def test_an_ai_outage_during_drafting_invents_no_note(doctor, patient, monkeypatch):
    def down(*args, **kwargs):
        raise llm.AIUnavailable("down")

    monkeypatch.setattr(llm, "ask", down)
    encounter = doctor.post(f"/api/patients/{patient['id']}/encounters", json={}).json()
    grant(doctor, patient["id"], "ai_scribe")
    session = doctor.post(f"/api/addons/clinscribe/encounters/{encounter['id']}/sessions", json={}).json()
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/segments", json={"text": "I have a cough"})
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/state", json={"state": "stopped"})
    response = doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/draft-note", json={})
    assert response.status_code == 503
    assert doctor.get(f"/api/addons/clinscribe/patients/{patient['id']}/notes").json() == []


def test_a_declined_draft_invents_no_note(doctor, patient, monkeypatch):
    def refuse(*args, **kwargs):
        raise llm.AIDeclined("refusal")

    monkeypatch.setattr(llm, "ask", refuse)
    encounter = doctor.post(f"/api/patients/{patient['id']}/encounters", json={}).json()
    grant(doctor, patient["id"], "ai_scribe")
    session = doctor.post(f"/api/addons/clinscribe/encounters/{encounter['id']}/sessions", json={}).json()
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/segments", json={"text": "I have a cough"})
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/state", json={"state": "stopped"})
    assert doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/draft-note", json={}).status_code == 502
    assert doctor.get(f"/api/addons/clinscribe/patients/{patient['id']}/notes").json() == []


def test_a_draft_with_nothing_said_stays_empty(monkeypatch):
    monkeypatch.setattr(llm, "ask", FakeAI(NoteDraft(sections=[], missing_information=[])))
    content = drafting.draft_note([{"sequence": 1, "speaker": "patient", "text": "Hello"}], ["Plan"])
    assert content["sections"][0]["statements"] == []


def test_a_lab_ai_outage_leaves_no_results(doctor, patient, monkeypatch):
    def down(*args, **kwargs):
        raise llm.AIUnavailable("down")

    grant(doctor, patient["id"], "ai_processing")
    monkeypatch.setattr(llm, "ask", down)
    report = doctor.post(
        "/api/addons/lablens/reports",
        data={"patient_id": patient["id"]},
        files={"file": ("r.pdf", blank_pdf(), "application/pdf")},
    ).json()
    result = extraction(doctor, report["id"])
    assert result["status"] == "failed"
    assert result["results"] == []


def test_a_non_report_from_ai_gives_no_results(doctor, patient, monkeypatch):
    grant(doctor, patient["id"], "ai_processing")
    read = extract.ReadReport(is_lab_report=False, name_on_report="", laboratory="", collection_date="", results=[])
    monkeypatch.setattr(llm, "ask", FakeAI(read))
    report = doctor.post(
        "/api/addons/lablens/reports",
        data={"patient_id": patient["id"]},
        files={"file": ("r.pdf", blank_pdf(), "application/pdf")},
    ).json()
    result = extraction(doctor, report["id"])
    assert result["status"] == "needs_manual_entry"
    assert result["results"] == []


def speech_reply(status_code, body=None, content=None):
    if content is not None:
        return httpx.Response(status_code, content=content)
    return httpx.Response(status_code, json=body if body is not None else {})


@pytest.fixture
def speech_ready(client, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(speech, "RETRY_PAUSE_SECONDS", 0)


@pytest.mark.parametrize(
    "reply",
    [speech_reply(401), speech_reply(403), speech_reply(429), speech_reply(500), speech_reply(503), httpx.TimeoutException("slow"), httpx.ConnectError("no route")],
    ids=["auth", "forbidden", "rate-limit", "server-error", "unavailable", "timeout", "network"],
)
def test_speech_failures_are_reported(monkeypatch, speech_ready, reply):
    monkeypatch.setattr(speech.httpx, "post", Sequence([reply]))
    with pytest.raises(speech.SpeechUnavailable):
        speech.transcribe(b"audio", "audio/webm", "en-IN")


@pytest.mark.parametrize(
    "reply",
    [speech_reply(200, content=b"not json"), speech_reply(200, ["list"]), speech_reply(200, {}), speech_reply(200, {"text": 5}), speech_reply(200, {"text": None})],
    ids=["not-json", "wrong-shape", "empty", "wrong-type", "null"],
)
def test_malformed_transcripts_invent_nothing(monkeypatch, speech_ready, reply):
    monkeypatch.setattr(speech.httpx, "post", Sequence([reply]))
    with pytest.raises(speech.SpeechUnavailable):
        speech.transcribe(b"audio", "audio/webm", "en-IN")


def test_an_empty_transcript_is_empty_not_invented(monkeypatch, speech_ready):
    monkeypatch.setattr(speech.httpx, "post", Sequence([speech_reply(200, {"text": ""})]))
    assert speech.transcribe(b"audio", "audio/webm", "en-IN") == ""


def test_speech_retries_once_and_no_more(monkeypatch, speech_ready):
    fake = Sequence([speech_reply(429), speech_reply(200, {"text": "hello"})])
    monkeypatch.setattr(speech.httpx, "post", fake)
    assert speech.transcribe(b"audio", "audio/webm", "en-IN") == "hello"
    fake = Sequence([speech_reply(503)])
    monkeypatch.setattr(speech.httpx, "post", fake)
    with pytest.raises(speech.SpeechUnavailable):
        speech.transcribe(b"audio", "audio/webm", "en-IN")
    assert fake.calls == speech.MAX_ATTEMPTS


def test_speech_auth_failures_are_not_retried(monkeypatch, speech_ready):
    fake = Sequence([speech_reply(401)])
    monkeypatch.setattr(speech.httpx, "post", fake)
    with pytest.raises(speech.SpeechUnavailable):
        speech.transcribe(b"audio", "audio/webm", "en-IN")
    assert fake.calls == 1


def test_a_rejected_clip_does_not_echo_its_content(monkeypatch, speech_ready):
    monkeypatch.setattr(speech.httpx, "post", Sequence([httpx.Response(400, text=PHI)]))
    with pytest.raises(speech.SpeechRejected) as caught:
        speech.transcribe(b"audio", "audio/webm", "en-IN")
    assert PHI not in str(caught.value)


def test_empty_speech_audio_is_refused(monkeypatch, speech_ready):
    monkeypatch.setattr(speech.httpx, "post", Sequence([speech_reply(200, content=b"")]))
    with pytest.raises(speech.SpeechUnavailable):
        speech.speak("Hello", "en-IN")


def test_valid_speech_still_works(monkeypatch, speech_ready):
    monkeypatch.setattr(speech.httpx, "post", Sequence([speech_reply(200, content=b"RIFFwav")]))
    assert speech.speak("Hello", "en-IN") == b"RIFFwav"


def test_a_missing_key_never_calls_speech(client, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    fake = Sequence([speech_reply(200, {"text": "x"})])
    monkeypatch.setattr(speech.httpx, "post", fake)
    with pytest.raises(speech.SpeechUnavailable):
        speech.transcribe(b"audio", "audio/webm", "en-IN")
    assert fake.calls == 0


def test_failure_logs_hold_no_patient_text(doctor, patient, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def down(*args, **kwargs):
        raise llm.AIUnavailable(f"Failed with {PHI}")

    monkeypatch.setattr(llm, "ask", down)
    grant(doctor, patient["id"], "ai_scribe")
    encounter = doctor.post(f"/api/patients/{patient['id']}/encounters", json={}).json()
    session = doctor.post(f"/api/addons/clinscribe/encounters/{encounter['id']}/sessions", json={}).json()
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/segments", json={"text": PHI})
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/state", json={"state": "stopped"})
    response = doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/draft-note", json={})
    assert PHI not in response.text
    assert PHI not in caplog.text
