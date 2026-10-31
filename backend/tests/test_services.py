"""Checks what we send to Groq, using fake replies, so no key is needed."""
import json

import httpx
import pytest
from pydantic import BaseModel

from backend import llm, settings_store, speech
from backend.clinscribe.drafting import NoteDraft
from backend.config import settings
from backend.prescription.analyzer import ReadPrescription


class Answer(BaseModel):
    text: str


def groq_reply(content='{"text": "hello"}', finish_reason="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]})


class FakeGroq:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings_store, "model_for", lambda tool: "openai/gpt-oss-120b")


def fake(monkeypatch, response):
    double = FakeGroq(response)
    monkeypatch.setattr(httpx, "post", double)
    return double


def test_a_text_request_uses_strict_schema_output(monkeypatch, groq):
    double = fake(monkeypatch, groq_reply())
    assert llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note").text == "hello"
    call = double.calls[0]
    body = call["json"]
    assert call["url"] == f"{settings.ai_base_url}/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["timeout"] == llm.REQUEST_TIMEOUT_SECONDS
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][0] == {"role": "system", "content": "Be careful."}
    assert body["messages"][1] == {"role": "user", "content": "Say hello"}


@pytest.mark.parametrize("schema", [Answer, NoteDraft, ReadPrescription])
def test_schemas_meet_strict_mode_rules(schema):
    def walk(node):
        assert "$ref" not in node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
        for value in node.values():
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, list):
                [walk(item) for item in value if isinstance(item, dict)]

    walk(llm.strict_schema(schema))


def test_images_use_json_mode_and_the_provider_format(monkeypatch, groq):
    double = fake(monkeypatch, groq_reply())
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}},
        {"type": "text", "text": "Read this"},
    ]
    llm.ask("Read.", content, Answer, tool="lablens_read")
    body = double.calls[0]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in body["messages"][0]["content"]
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
    assert parts[1] == {"type": "text", "text": "Read this"}


def test_an_unlisted_model_gets_json_mode_and_is_still_checked(monkeypatch, groq):
    monkeypatch.setattr(settings_store, "model_for", lambda tool: "llama-3.3-70b-versatile")
    double = fake(monkeypatch, groq_reply('{"wrong": "shape"}'))
    with pytest.raises(llm.AIDeclined):
        llm.ask("system", "content", Answer, tool="clinscribe_note")
    assert double.calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_fenced_json_is_accepted(monkeypatch, groq):
    fake(monkeypatch, groq_reply('```json\n{"text": "hello"}\n```'))
    assert llm.ask("s", "c", Answer, tool="clinscribe_note").text == "hello"


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_cut_off_answers_are_not_used(monkeypatch, groq, finish_reason):
    fake(monkeypatch, groq_reply(finish_reason=finish_reason))
    with pytest.raises(llm.AIDeclined):
        llm.ask("system", "content", Answer, tool="clinscribe_note")


def test_no_key_means_no_call(monkeypatch, client):
    monkeypatch.setattr(settings, "groq_api_key", "")
    double = fake(monkeypatch, groq_reply())
    with pytest.raises(llm.AINotConfigured):
        llm.ask("system", "content", Answer, tool="clinscribe_note")
    assert double.calls == []


def test_a_placeholder_key_counts_as_no_key(monkeypatch):
    from backend.config import Settings

    monkeypatch.setenv("GROQ_API_KEY", "your-groq-key")
    assert Settings().groq_api_key == ""


def test_speech_needs_a_key(monkeypatch, client):
    monkeypatch.setattr(settings, "groq_api_key", "")
    with pytest.raises(speech.SpeechUnavailable):
        speech.speak("hello", "en-IN")


def test_transcribe_sends_the_documented_fields(monkeypatch, groq):
    double = fake(monkeypatch, httpx.Response(200, json={"text": "बुखार है"}))
    assert speech.transcribe(b"audio", "audio/webm;codecs=opus", "hi-IN") == "बुखार है"
    call = double.calls[0]
    assert call["url"] == f"{settings.ai_base_url}/audio/transcriptions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["data"]["language"] == "hi"
    assert call["data"]["response_format"] == "verbose_json"
    assert call["files"]["file"][0] == "audio.webm"
    assert call["files"]["file"][1] == b"audio"


def test_safari_recordings_get_the_right_extension(monkeypatch, groq):
    double = fake(monkeypatch, httpx.Response(200, json={"text": "ok"}))
    speech.transcribe(b"audio", "audio/mp4", "en-IN")
    assert double.calls[0]["files"]["file"][0] == "audio.mp4"


def test_silence_whisper_invented_is_dropped(monkeypatch, groq):
    reply = {
        "text": "I have a cough. Thank you for watching.",
        "segments": [
            {"text": " I have a cough.", "no_speech_prob": 0.02},
            {"text": " Thank you for watching.", "no_speech_prob": 0.93},
        ],
    }
    fake(monkeypatch, httpx.Response(200, json=reply))
    assert speech.transcribe(b"audio", "audio/webm", "en-IN") == "I have a cough."


def test_speak_returns_audio_for_english(monkeypatch, groq):
    double = fake(monkeypatch, httpx.Response(200, content=b"RIFF-fake-wav"))
    assert speech.speak("Hello", "en-IN") == b"RIFF-fake-wav"
    body = double.calls[0]["json"]
    assert body["model"] == speech.TTS_MODEL
    assert body["input"] == "Hello"


def test_hindi_is_left_to_the_browser_voice(monkeypatch, groq):
    double = fake(monkeypatch, httpx.Response(200, content=b"x"))
    with pytest.raises(speech.SpeechUnavailable):
        speech.speak("नमस्ते", "hi-IN")
    assert double.calls == []


def test_rejected_audio_is_reported(monkeypatch, groq):
    fake(monkeypatch, httpx.Response(422, text="bad audio"))
    with pytest.raises(speech.SpeechRejected):
        speech.transcribe(b"audio", "audio/webm", "en-IN")


def test_text_over_the_limit_is_refused_before_paying_for_it(monkeypatch, groq):
    double = fake(monkeypatch, httpx.Response(200, content=b"x"))
    with pytest.raises(speech.SpeechRejected):
        speech.speak("a" * (speech.MAX_SPEECH_CHARS + 1), "en-IN")
    assert double.calls == []


def test_the_request_can_be_read_as_json(monkeypatch, groq):
    double = fake(monkeypatch, groq_reply())
    llm.ask("s", "c", Answer, tool="clinscribe_note")
    json.dumps(double.calls[0]["json"])


# Two providers: Groq and Gemini


class TwoProviders:
    """Answers by address, and records who was asked, in order."""

    def __init__(self, groq=None, gemini=None):
        self.replies = {"api.groq.com": groq or groq_reply(), "generativelanguage.googleapis.com": gemini or groq_reply('{"text": "from gemini"}')}
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        reply = next(response for host, response in self.replies.items() if host in url)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def asked(self):
        return ["groq" if "groq.com" in call["url"] else "gemini" for call in self.calls]


@pytest.fixture
def both(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "groq-test-key")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test-key")
    monkeypatch.setattr(settings_store, "model_for", lambda tool: "openai/gpt-oss-120b")
    monkeypatch.setattr(settings_store, "get", lambda key, db=None: {"ai.primary": "groq", "ai.strategy": "fallback", "ai.model.gemini": "gemini-3.5-flash-lite"}[key])
    return monkeypatch


def two(monkeypatch, **replies):
    double = TwoProviders(**replies)
    monkeypatch.setattr(httpx, "post", double)
    return double


def test_the_first_provider_answers_when_it_is_healthy(both):
    double = two(both)
    assert llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note").text == "hello"
    assert double.asked == ["groq"]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(503),
        httpx.Response(429, headers={"retry-after": "60"}),
        httpx.Response(401),
        httpx.Response(400),
        httpx.Response(200, json={"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]}),
        httpx.ConnectTimeout("slow"),
    ],
)
def test_a_failing_first_provider_hands_over_to_the_second(both, failure):
    double = two(both, groq=failure)
    assert llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note").text == "from gemini"
    assert double.asked[0] == "groq" and double.asked[-1] == "gemini"


def test_with_two_providers_a_flaky_one_is_not_retried_first(both):
    double = two(both, groq=httpx.Response(503))
    llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")
    assert double.asked == ["groq", "gemini"]


def test_a_second_provider_gets_its_own_key_url_and_model(both):
    double = two(both, groq=httpx.Response(503))
    llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")
    gemini = double.calls[-1]
    assert gemini["url"] == f"{settings.gemini_base_url}/chat/completions"
    assert gemini["headers"]["Authorization"] == "Bearer gemini-test-key"
    assert gemini["json"]["model"] == "gemini-3.5-flash-lite"
    assert gemini["json"]["response_format"] == {"type": "json_object"}  # Gemini is always asked in JSON mode, and checked here


def test_when_both_fail_the_caller_gets_a_clear_error(both):
    two(both, groq=httpx.Response(503), gemini=httpx.Response(503))
    with pytest.raises(llm.AIUnavailable):
        llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")


def test_a_provider_without_a_key_is_never_asked(both):
    both.setattr(settings, "gemini_api_key", "")
    double = two(both, groq=httpx.Response(503))
    with pytest.raises(llm.AIUnavailable):
        llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")
    assert set(double.asked) == {"groq"}
    assert double.asked.count("groq") == llm.MAX_ATTEMPTS  # alone, it still gets its retry


def test_gemini_alone_works_and_groq_is_left_alone(both):
    both.setattr(settings, "groq_api_key", "")
    double = two(both)
    assert llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note").text == "from gemini"
    assert double.asked == ["gemini"]


def test_no_keys_at_all_says_ai_isnt_set_up(both):
    both.setattr(settings, "groq_api_key", "")
    both.setattr(settings, "gemini_api_key", "")
    with pytest.raises(llm.AINotConfigured):
        llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")


def test_the_primary_provider_is_a_setting(both):
    both.setattr(settings_store, "get", lambda key, db=None: {"ai.primary": "gemini", "ai.strategy": "fallback", "ai.model.gemini": "gemini-3.5-flash-lite"}[key])
    double = two(both)
    llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")
    assert double.asked == ["gemini"]


def test_round_robin_takes_turns(both):
    both.setattr(settings_store, "get", lambda key, db=None: {"ai.primary": "groq", "ai.strategy": "round_robin", "ai.model.gemini": "gemini-3.5-flash-lite"}[key])
    both.setattr(llm, "_turns", __import__("itertools").count())
    double = two(both)
    for _ in range(4):
        llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")
    assert double.asked == ["groq", "gemini", "groq", "gemini"]


def test_round_robin_still_falls_back_when_the_turn_goes_to_a_broken_provider(both):
    both.setattr(settings_store, "get", lambda key, db=None: {"ai.primary": "groq", "ai.strategy": "round_robin", "ai.model.gemini": "gemini-3.5-flash-lite"}[key])
    both.setattr(llm, "_turns", __import__("itertools").count())
    double = two(both, groq=httpx.Response(503))
    assert [llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note").text for _ in range(2)] == ["from gemini"] * 2
    assert double.asked == ["groq", "gemini", "gemini"]


def test_images_go_to_gemini_in_the_same_neutral_shape(both):
    double = two(both, groq=httpx.Response(503))
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}, {"type": "text", "text": "Read this"}]
    llm.ask("Read.", content, Answer, tool="lablens_read")
    assert double.calls[-1]["json"]["messages"][1]["content"][0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}


def test_no_key_value_ever_reaches_the_logs(both, caplog):
    two(both, groq=httpx.Response(503))
    llm.ask("Be careful.", "Say hello", Answer, tool="clinscribe_note")
    assert "groq-test-key" not in caplog.text and "gemini-test-key" not in caplog.text


def test_the_environment_placeholder_for_gemini_counts_as_empty(monkeypatch):
    from backend.config import Settings

    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-key")
    assert Settings().gemini_api_key == ""
