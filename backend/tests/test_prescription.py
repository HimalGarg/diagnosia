import io
import json

import pytest
from PIL import Image

from backend import llm
from backend.prescription import analyzer, routes
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


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1 - 1", "One in the morning, none in the afternoon, one at night"),
        ("- 1 -", "None in the morning, one in the afternoon, none at night"),
        ("x x 1", "None in the morning, none in the afternoon, one at night"),
        ("1 x 1", "One in the morning, none in the afternoon, one at night"),
        ("BBF", "Before breakfast"),
    ],
)
def test_column_marks_and_bbf_are_decoded(raw, expected):
    assert decode(raw) == expected


def test_a_schedule_is_withheld_when_the_marks_disagree_with_the_frequency():
    assert daily_slots("1 - 1", "BD") == {"morning": "one", "night": "one"}
    assert daily_slots("- 1 -", "OD") == {"afternoon": "one"}
    assert daily_slots("1 - 1", "OD") == {}
    assert daily_slots("x x 1", "TDS") == {}


def test_medicines_taken_when_needed_never_get_a_fixed_time():
    assert daily_slots("x x 1", "SOS") == {}
    assert daily_slots("1-0-1", "PRN") == {}


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
        medicines=[]
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


def test_doctors_notes_and_ingredients_reach_the_explanation(monkeypatch):
    ai = RecordingAI(guidance())
    monkeypatch.setattr(llm, "ask", ai)
    entry = medicine().model_copy(update={"contains": "Amoxycillin"})
    explain_prescription([entry], "en-IN", doctor_notes="Bronchiectasis")
    assert "Bronchiectasis" in ai.calls[0]["content"]
    assert "Amoxycillin" in ai.calls[0]["content"]


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
                    "frequency": {"value": "BD", "legibility": "clear"},
                    "day_pattern": {"value": "1-0-1", "legibility": "clear"},
                    "duration": {"value": "5 days", "legibility": "clear"},
                    "notes": empty,
                }
            ],
            "follow_up_date": empty,
            "doctor_notes": empty,
        }
    )


def post_photo(client, consent="true", photos=1):
    return client.post(
        "/api/prescription/read",
        files=[("images", (f"rx{n}.jpg", photo_bytes(), "image/jpeg")) for n in range(photos)],
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
    first = ai.calls[0]["content"][0]
    assert first["type"] == "image"
    assert first["source"]["media_type"] == "image/jpeg"
    assert first["source"]["data"]
    assert ai.calls[0]["schema"] is ReadPrescription


def test_front_and_back_photos_are_read_as_one_prescription(client, details, monkeypatch):
    ai = RecordingAI(read_result())
    monkeypatch.setattr(llm, "ask", ai)
    signed_in(client, details)
    assert post_photo(client, photos=2).status_code == 200
    assert len(ai.calls) == 1
    content = ai.calls[0]["content"]
    assert [part["type"] for part in content] == ["image", "image", "text"]
    assert "2 photos" in content[-1]["text"]


def test_too_many_photos_are_refused(client, details, monkeypatch):
    ai = RecordingAI(read_result())
    monkeypatch.setattr(llm, "ask", ai)
    signed_in(client, details)
    assert post_photo(client, photos=routes.MAX_PHOTOS + 1).status_code == 422
    assert ai.calls == []


@pytest.mark.parametrize(
    "status, expected",
    [("found", "clear"), ("unchecked", "clear"), ("close", "unsure"), ("not_found", "unsure")],
)
def test_a_name_not_on_the_medicine_list_must_be_checked(client, details, monkeypatch, status, expected):
    result = read_result()
    result.medicines[0].name.legibility = "clear"
    monkeypatch.setattr(llm, "ask", RecordingAI(result))
    monkeypatch.setattr(analyzer, "check_name", lambda name: {"status": status, "name": None, "contains": None})
    signed_in(client, details)
    entry = post_photo(client).json()["medicines"][0]
    assert entry["name"]["legibility"] == expected
    assert entry["name"]["value"] == "Amoxicillin"  # never changed


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


def test_more_photos_than_one_vision_request_takes_are_read_in_groups(client, details, monkeypatch):
    ai = RecordingAI(read_result())
    monkeypatch.setattr(llm, "ask", ai)
    signed_in(client, details)
    response = post_photo(client, photos=routes.MAX_PHOTOS)
    assert response.status_code == 200
    assert [sum(part["type"] == "image" for part in call["content"]) for call in ai.calls] == [3, 1]
    assert len(response.json()["medicines"]) == 2 * len(read_result().medicines)


def test_duration_parsing_handles_various_formats():
    from backend.prescription import shorthand

    assert shorthand.parse_duration_days("5 days") == 5
    assert shorthand.parse_duration_days("x 5 days") == 5
    assert shorthand.parse_duration_days("for 2 weeks") == 14
    assert shorthand.parse_duration_days("1 month") == 30
    assert shorthand.parse_duration_days("3d") == 3
    assert shorthand.parse_duration_days("") is None


def test_calendar_reminders_build_google_urls_and_ics():
    med1 = medicine("Amoxicillin", frequency="1-0-1").model_copy(update={"duration": field("5 days")})
    med2 = medicine("Paracetamol", frequency="SOS").model_copy(update={"duration": field("3 days")})
    reminders = analyzer.build_calendar_reminders([med1, med2], "en-IN", start_date="2026-11-01")

    assert "morning" in reminders["slots"]
    assert "night" in reminders["slots"]
    assert "afternoon" not in reminders["slots"]

    morning = reminders["slots"]["morning"]
    assert morning["duration_days"] == 5
    assert "calendar.google.com/calendar/render" in morning["google_url"]
    assert "20261101T080000" in morning["google_url"]

    # Paracetamol is SOS so it must be in as_needed, not scheduled slots
    as_needed = reminders["as_needed"]
    assert len(as_needed) == 1
    assert as_needed[0]["name"] == "Paracetamol"

    # ICS calendar payload
    assert "BEGIN:VCALENDAR" in reminders["ics_calendar"]
    assert "RRULE:FREQ=DAILY;COUNT=5" in reminders["ics_calendar"]
    assert "TRIGGER:-PT10M" in reminders["ics_calendar"]


def test_calendar_endpoint_with_custom_times(client, details):
    signed_in(client, details)
    med = medicine("Azithromycin", frequency="1-0-0").model_copy(update={"duration": field("3 days")})
    response = client.post(
        "/api/prescription/calendar",
        json={
            "medicines": [json.loads(med.model_dump_json())],
            "language": "hi-IN",
            "start_date": "2026-11-05",
            "times": {"morning": "09:15"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["times"]["morning"] == "09:15"
    assert "morning" in data["slots"]
    assert "20261105T091500" in data["slots"]["morning"]["google_url"]
    assert data["slots"]["morning"]["duration_days"] == 3



# Reminders and the plan behind them


def timed(name, frequency="", day_pattern="", duration="", notes="", dose=""):
    return ConfirmedMedicine(
        name=field(name), dose=field(dose), frequency=field(frequency), day_pattern=field(day_pattern),
        duration=field(duration), notes=field(notes),
    )


@pytest.mark.parametrize(
    "frequency, notes, expected",
    [
        ("BD", "", ["morning", "night"]),
        ("TDS", "", ["morning", "afternoon", "night"]),
        ("OD", "", ["morning"]),
        ("OD", "take at night", ["night"]),
        ("HS", "", ["night"]),
        ("twice daily after meals", "", ["morning", "night"]),
        ("1 tab thrice a day", "", ["morning", "afternoon", "night"]),
        ("QID", "", []),
        ("SOS", "", []),
        ("every other day", "", []),
        ("", "", []),
    ],
)
def test_usual_times_for_a_frequency_written_without_marks(frequency, notes, expected):
    from backend.prescription.shorthand import typical_slots

    assert list(typical_slots(frequency, "", notes)) == expected


def test_a_medicine_written_as_bd_is_in_the_plan_marked_as_usual_times():
    reminders = analyzer.build_calendar_reminders([timed("T. Cefix 200", "BD", duration="x 5 days")], "en-IN", start_date="2026-11-01")
    assert set(reminders["slots"]) == {"morning", "night"}
    item = reminders["slots"]["morning"]["medicines"][0]
    assert item["assumed"] is True
    assert reminders["unscheduled"] == []
    assert [entry["assumed"] for entry in build_schedule([timed("T. Cefix 200", "BD")])["night"]] == [True]


def test_marks_that_disagree_with_the_frequency_are_not_scheduled_or_replaced_by_usual_times():
    reminders = analyzer.build_calendar_reminders([timed("T. Cefix 200", "OD", day_pattern="1-0-1")], "en-IN")
    assert reminders["slots"] == {}
    assert reminders["unscheduled"][0]["name"] == "T. Cefix 200"
    assert "don't match" in reminders["unscheduled"][0]["reason"]


def test_a_medicine_with_no_usable_timing_is_listed_and_not_dropped():
    reminders = analyzer.build_calendar_reminders([timed("T. Feburic 40mg", "QID")], "en-IN")
    assert reminders["slots"] == {} and reminders["ics_calendar"] == ""
    assert [item["name"] for item in reminders["unscheduled"]] == ["T. Feburic 40mg"]


def test_each_medicine_stops_being_listed_when_its_own_course_ends():
    short = timed("T. Short", day_pattern="1-0-0", duration="x 5 days")
    long = timed("T. Long", day_pattern="1-0-0", duration="x 15 days")
    reminders = analyzer.build_calendar_reminders([short, long], "en-IN", start_date="2026-11-01")
    first, second = reminders["slots"]["morning"]["segments"]
    assert [m["name"] for m in first["medicines"]] == ["T. Short", "T. Long"]
    assert [m["name"] for m in second["medicines"]] == ["T. Long"]
    assert (first["duration_days"], second["duration_days"]) == (5, 10)
    assert first["dtstart"].startswith("20261101") and second["dtstart"].startswith("20261106")
    assert "COUNT=5" in reminders["ics_calendar"] and "COUNT=10" in reminders["ics_calendar"]


def test_the_plan_says_when_the_course_length_was_not_written():
    reminders = analyzer.build_calendar_reminders([timed("T. Open", day_pattern="1-0-0")], "en-IN")
    item = reminders["slots"]["morning"]["medicines"][0]
    assert item["duration_assumed"] is True and item["duration_days"] == analyzer.UNKNOWN_DURATION_DAYS


def test_reminder_lines_read_naturally_and_never_repeat_the_dose():
    reminders = analyzer.build_calendar_reminders(
        [timed("T. Pregaba 75mg", day_pattern="0-0-1", dose="75mg", duration="x 5 days", frequency="HS PC")], "en-IN"
    )
    text = reminders["slots"]["night"]["description"]
    assert "T. Pregaba 75mg - 1 tablet, after food" in text
    assert text.count("75mg") == 1


def test_unreadable_medicines_never_get_a_reminder():
    reminders = analyzer.build_calendar_reminders([medicine(name="", name_legibility="unreadable")], "en-IN")
    assert reminders["slots"] == {} and reminders["unscheduled"] == []


class PlanAI:
    """Answers the planning question, and fails the test if it is asked something else."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def __call__(self, system, content, schema, **options):
        self.calls.append({"schema": schema, **options})
        assert schema is analyzer.SchedulePlan
        if self.error:
            raise self.error
        return analyzer.SchedulePlan(medicines=self.answer)


def slot_plan(index, slots, clear=True, reason=""):
    return analyzer.SlotPlan(index=index, slots=slots, clear=clear, reason=reason)


def test_only_what_the_rules_cannot_place_is_sent_to_the_planning_model(monkeypatch):
    ai = PlanAI([slot_plan(2, ["morning", "afternoon", "night"])])
    monkeypatch.setattr(llm, "ask", ai)
    meds = [timed("A", day_pattern="1-0-1"), timed("B", "BD"), timed("C", "every 8 hours"), timed("D", "SOS")]
    plan = analyzer.plan_unresolved(meds)
    assert plan == {"2": {"slots": ["morning", "afternoon", "night"], "reason": ""}}
    assert ai.calls[0]["tool"] == "prescription_plan"


def test_the_model_is_not_called_when_the_rules_place_everything(monkeypatch):
    ai = PlanAI([])
    monkeypatch.setattr(llm, "ask", ai)
    assert analyzer.plan_unresolved([timed("A", "BD"), timed("B", day_pattern="1-0-0")]) == {}
    assert ai.calls == []


def test_a_model_answer_that_changes_the_times_a_day_is_refused(monkeypatch):
    monkeypatch.setattr(llm, "ask", PlanAI([slot_plan(0, ["morning"])]))
    plan = analyzer.plan_unresolved([timed("A", "every 8 hours, three times a day text", notes="")])
    # "three times a day" is stated, so a one-slot answer disagrees with it and is dropped
    assert plan == {} or plan["0"]["slots"] == []


def test_an_unclear_model_answer_keeps_its_reason_and_schedules_nothing(monkeypatch):
    monkeypatch.setattr(llm, "ask", PlanAI([slot_plan(0, [], clear=False, reason="Every other day")]))
    meds = [timed("A", "alternate days", duration="x 5 days")]
    plan = analyzer.plan_unresolved(meds)
    reminders = analyzer.build_calendar_reminders(meds, "en-IN", plan=plan)
    assert reminders["slots"] == {} and reminders["unscheduled"][0]["reason"] == "Every other day"


def test_a_planned_medicine_is_scheduled_and_marked_as_not_written(monkeypatch):
    monkeypatch.setattr(llm, "ask", PlanAI([slot_plan(0, ["morning", "night"])]))
    meds = [timed("A", "subah shaam", duration="x 3 days")]
    plan = analyzer.plan_unresolved(meds)
    reminders = analyzer.build_calendar_reminders(meds, "en-IN", plan=plan)
    assert set(reminders["slots"]) == {"morning", "night"}
    assert reminders["slots"]["morning"]["medicines"][0]["assumed"] is True


def test_the_plan_survives_an_ai_outage_by_leaving_medicines_for_the_pharmacist(monkeypatch):
    monkeypatch.setattr(llm, "ask", PlanAI(error=llm.AIUnavailable("down")))
    meds = [timed("A", "every 8 hours")]
    assert analyzer.plan_unresolved(meds) == {}
    assert analyzer.build_calendar_reminders(meds, "en-IN")["unscheduled"][0]["name"] == "A"


def test_the_calendar_endpoint_reuses_the_plan_without_calling_the_ai(client, details, monkeypatch):
    def refuse(*_args, **_options):
        raise AssertionError("the calendar route must not call the AI")

    monkeypatch.setattr(llm, "ask", refuse)
    signed_in(client, details)
    meds = [json.loads(timed("A", "subah shaam", duration="x 3 days").model_dump_json())]
    response = client.post(
        "/api/prescription/calendar",
        json={"medicines": meds, "language": "en-IN", "plan": {"0": {"slots": ["morning", "night"], "reason": ""}}},
    )
    assert response.status_code == 200
    assert set(response.json()["slots"]) == {"morning", "night"}


def test_marks_alone_are_enough_directions_to_explain_a_medicine():
    assert is_explainable(timed("T. Pulmocus AB", day_pattern="1-0-1"))
    assert not is_explainable(timed("T. Pulmocus AB"))
    assert not is_explainable(ConfirmedMedicine(
        name=field("T. X"), dose=field(), frequency=field("", "unreadable"), day_pattern=field("", "unreadable"), duration=field(), notes=field()))
