import io
import json

import pytest
from PIL import Image

from backend import llm
from backend.prescription import analyzer
from backend.prescription.analyzer import (
    ConfirmedMedicine,
    Guidance,
    ReadField,
    ReadPrescription,
    build_schedule,
    explain_prescription,
    is_explainable,
    prepare_photo,
)
from backend.prescription.shorthand import daily_slots, decode


def field(value="", legibility="clear"):
    return ReadField(value=value, legibility=legibility)


def medicine(name="Amoxicillin", frequency="1-0-1", name_legibility="clear", frequency_legibility="clear"):
    return ConfirmedMedicine(
        name=field(name, name_legibility),
        dose=field("500 mg"),
        frequency=field(frequency, frequency_legibility),
        duration=field("x 5 days"),
        notes=field(),
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1-0-1", "One in the morning, none in the afternoon, one at night"),
        ("0-0-1", "None in the morning, none in the afternoon, one at night"),
        ("1 - 1 - 1", "One in the morning, one in the afternoon, one at night"),
        ("½-0-½", "Half in the morning, none in the afternoon, half at night"),
        ("1/2-0-1/2", "Half in the morning, none in the afternoon, half at night"),
        ("OD", "Once a day"),
        ("od", "Once a day"),
        ("BD", "Twice a day"),
        ("B.D.", "Twice a day"),
        ("BID", "Twice a day"),
        ("TDS", "Three times a day"),
        ("t.d.s", "Three times a day"),
        ("TID", "Three times a day"),
        ("QID", "Four times a day"),
        ("HS", "At bedtime"),
        ("SOS", "Only when needed"),
        ("PRN", "Only when needed"),
        ("AC", "Before food"),
        ("PC", "After food"),
        ("x 5 days", "Take it for 5 days"),
        ("x5d", "Take it for 5 days"),
        ("for 1 week", "Take it for 1 week"),
        ("x 2 wks", "Take it for 2 weeks"),
        ("x 1 month", "Take it for 1 month"),
        ("1-0-1 PC x 5 days", "One in the morning, none in the afternoon, one at night. After food. Take it for 5 days"),
        ("BD AC", "Twice a day. Before food"),
    ],
)
def test_shorthand_is_decoded(raw, expected):
    assert decode(raw) == expected


@pytest.mark.parametrize("raw", ["", "as directed", "squiggle", "1-2", "10-0-1", "1-0-1-0-1"])
def test_unknown_shorthand_is_never_guessed(raw):
    assert decode(raw) is None


def test_ordinary_words_are_not_mistaken_for_shorthand():
    assert decode("apply to the affected area") is None


def test_daily_slots_come_only_from_an_exact_pattern():
    assert daily_slots("1-0-1") == {"morning": "one", "night": "one"}
    assert daily_slots("0-½-0") == {"afternoon": "half"}
    assert daily_slots("BD") == {}


def test_schedule_groups_medicines_by_time_of_day():
    schedule = build_schedule([medicine("Amoxicillin", "1-0-1"), medicine("Vitamin D", "0-0-1")])
    assert [entry["name"] for entry in schedule["morning"]] == ["Amoxicillin"]
    assert [entry["name"] for entry in schedule["night"]] == ["Amoxicillin", "Vitamin D"]
    assert schedule["afternoon"] == []


def test_unreadable_medicines_are_not_explainable():
    assert is_explainable(medicine())
    assert is_explainable(medicine(name_legibility="unsure"))
    assert not is_explainable(medicine(name="", name_legibility="unreadable"))
    assert not is_explainable(medicine(frequency="", frequency_legibility="unreadable"))


class RecordingAI:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, system, content, schema, **options):
        self.calls.append({"system": system, "content": content, "schema": schema, **options})
        return self.result


def guidance():
    return Guidance(
        medicines=[], foods_to_eat=["Dal"], foods_to_avoid=[], yoga=[], lifestyle=[]
    )


def test_unreadable_medicines_never_reach_the_ai(monkeypatch):
    ai = RecordingAI(guidance())
    monkeypatch.setattr(llm, "ask", ai)
    result = explain_prescription(
        [
            medicine("Amoxicillin"),
            medicine("SECRET-UNREADABLE", name_legibility="unreadable"),
            medicine("ALSO-HIDDEN", frequency="zz", frequency_legibility="unreadable"),
        ],
        "en-IN",
    )
    assert len(ai.calls) == 1
    prompt = ai.calls[0]["content"]
    assert "Amoxicillin" in prompt
    assert "SECRET-UNREADABLE" not in prompt
    assert "ALSO-HIDDEN" not in prompt
    assert result["ask_pharmacist"] == ["SECRET-UNREADABLE", "ALSO-HIDDEN"]


def test_nothing_is_sent_when_nothing_could_be_read(monkeypatch):
    ai = RecordingAI(guidance())
    monkeypatch.setattr(llm, "ask", ai)
    result = explain_prescription([medicine("", name_legibility="unreadable")], "hi-IN")
    assert ai.calls == []
    assert result["guidance"] is None
    assert result["ask_pharmacist"] == ["A medicine we couldn't read"]


def test_the_explanation_prompt_forbids_changing_doses():
    text = analyzer.EXPLAIN_SYSTEM.lower()
    assert "never change a dose" in text
    assert "substitute" in text
    assert "never guess" in analyzer.READ_SYSTEM.lower()


def photo_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_photos_lose_metadata_and_are_size_limited():
    exif = Image.Exif()
    exif[0x010E] = "private note"
    exif[0x0112] = 6  # stored sideways
    image = Image.new("RGB", (3000, 2000), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    import base64

    data, media_type = prepare_photo(Image.open(io.BytesIO(buffer.getvalue())))
    decoded = base64.b64decode(data)
    assert media_type == "image/jpeg"
    assert b"private note" not in decoded
    result = Image.open(io.BytesIO(decoded))
    assert max(result.size) <= analyzer.MAX_PHOTO_SIDE
    assert result.height > result.width  # turned upright


def signed_in(client, details):
    client.post("/api/auth/register", json={**details, "role": "patient"})


def read_result():
    empty = {"value": "", "legibility": "clear"}
    return ReadPrescription.model_validate(
        {
            "is_prescription": True,
            "medicines": [
                {
                    "name": {"value": "Amoxicillin", "legibility": "unsure"},
                    "dose": {"value": "500 mg", "legibility": "clear"},
                    "frequency": {"value": "1-0-1", "legibility": "clear"},
                    "duration": {"value": "x 5 days", "legibility": "clear"},
                    "notes": empty,
                }
            ],
            "follow_up_date": empty,
            "doctor_notes": empty,
        }
    )


def post_photo(client, consent="true"):
    return client.post(
        "/api/prescription/read",
        files={"image": ("rx.jpg", photo_bytes(), "image/jpeg")},
        data={"consent": consent},
    )


def test_reading_requires_sign_in(client, monkeypatch):
    ai = RecordingAI(read_result())
    monkeypatch.setattr(llm, "ask", ai)
    assert post_photo(client).status_code == 401
    assert ai.calls == []


def test_reading_requires_consent(client, details, monkeypatch):
    ai = RecordingAI(read_result())
    monkeypatch.setattr(llm, "ask", ai)
    signed_in(client, details)
    assert post_photo(client, consent="false").status_code == 422
    assert ai.calls == []


def test_reading_decodes_shorthand_and_keeps_legibility(client, details, monkeypatch):
    monkeypatch.setattr(llm, "ask", RecordingAI(read_result()))
    signed_in(client, details)
    body = post_photo(client).json()
    entry = body["medicines"][0]
    assert entry["name"]["legibility"] == "unsure"
    assert entry["plain"].startswith("One in the morning")


def test_the_photo_is_sent_as_an_image_to_the_ai(client, details, monkeypatch):
    ai = RecordingAI(read_result())
    monkeypatch.setattr(llm, "ask", ai)
    signed_in(client, details)
    post_photo(client)
    assert ai.calls[0]["content"][0]["type"] == "image"
    assert ai.calls[0]["schema"] is ReadPrescription


def test_explain_endpoint_returns_a_schedule(client, details, monkeypatch):
    monkeypatch.setattr(llm, "ask", RecordingAI(guidance()))
    signed_in(client, details)
    response = client.post(
        "/api/prescription/explain",
        json={"medicines": [json.loads(medicine().model_dump_json())], "language": "hi-IN"},
    )
    assert response.status_code == 200
    assert response.json()["schedule"]["morning"][0]["name"] == "Amoxicillin"


def test_ai_outage_gives_a_clear_message(client, details, monkeypatch):
    def fail(*args, **kwargs):
        raise llm.AIUnavailable("no key")

    monkeypatch.setattr(llm, "ask", fail)
    signed_in(client, details)
    response = post_photo(client)
    assert response.status_code == 503
    assert "no key" not in response.text
