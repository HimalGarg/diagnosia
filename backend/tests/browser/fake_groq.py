"""A stand-in for Groq's API, for browser tests with no key. It proves our requests and
wiring work over real HTTP. It proves nothing about Groq's models or their accuracy.

Run with: python -m backend.tests.browser.fake_groq 8127
"""
import json
import sys

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()
seen = {"chat": [], "audio": 0}

NOTE = {
    "sections": [
        {"heading": "Subjective", "statements": [
            {"text": "Fasting reading was 118 this week", "origin": "patient_statement", "evidence_segments": [2], "uncertain": False, "uncertainty_reason": ""},
            {"text": "Chest pain on exertion", "origin": "patient_statement", "evidence_segments": [2], "uncertain": False, "uncertainty_reason": ""},
        ]},
        {"heading": "Plan", "statements": [
            {"text": "Continue metformin 850 mg twice daily", "origin": "clinician_statement", "evidence_segments": [4], "uncertain": False, "uncertainty_reason": ""},
        ]},
    ],
    "missing_information": ["Blood pressure"],
}
LAB = {
    "is_lab_report": True, "name_on_report": "Ravi Kumar", "laboratory": "Demo Scan Lab", "collection_date": "05/09/2026",
    "results": [{"test_name": "Hemoglobin", "value": "12.9", "unit": "g/dL", "reference_range": "13.0 - 17.0", "printed_flag": "L", "legibility": "clear"}],
}
EMPTY = {"value": "", "legibility": "clear"}
PRESCRIPTION = {
    "is_prescription": True,
    "medicines": [{
        "name": {"value": "Amoxicillin", "legibility": "clear"},
        "dose": {"value": "500 mg", "legibility": "clear"},
        "frequency": {"value": "BD", "legibility": "clear"},
        "day_pattern": {"value": "1-0-1", "legibility": "clear"},
        "duration": {"value": "5 days", "legibility": "clear"},
        "notes": EMPTY,
    }, {
        "name": {"value": "", "legibility": "unreadable"},
        "dose": EMPTY, "frequency": EMPTY, "day_pattern": EMPTY, "duration": EMPTY, "notes": EMPTY,
    }],
    "follow_up_date": {"value": "12/11/2026", "legibility": "clear"},
    "doctor_notes": EMPTY,
}
GUIDANCE = {
    "medicines": [{"name": "Amoxicillin", "purpose": "Treats infection.", "how_to_take": "Take it morning and night."}],
}
REPORT = {
    "chief_complaint": "Fever", "present_illness": "Three days of fever.", "highlights": ["Fever for three days"],
    "current_medicines": [], "allergies": [], "past_history": "", "gaps": ["Temperature not measured"],
}
FIELDS = ["chief_complaint", "duration", "severity", "fever", "current_medicines", "allergies"]


def interview_turn(prompt: str) -> dict:
    if "chest pain" in prompt.lower():
        return {"updates": [], "next_question": "", "finished": True, "urgent_symptoms": ["Chest pain"]}
    return {
        "updates": [{"key": key, "value": "Synthetic answer"} for key in FIELDS],
        "next_question": "",
        "finished": True,
        "urgent_symptoms": [],
    }


def answer_for(system: str, prompt: str, has_image: bool) -> dict:
    if "prescription" in system.lower() and has_image:
        return PRESCRIPTION
    if has_image:
        return LAB
    if "next_question" in system or " Turn" in system:
        return interview_turn(prompt)
    if "explain a doctor's prescription" in system:
        return GUIDANCE
    if "chief_complaint" in system or " Report" in system:
        return REPORT
    return NOTE


@app.post("/openai/v1/chat/completions")
async def chat(request: Request):
    if request.headers.get("authorization") != "Bearer fake-test-key":
        return JSONResponse({"error": {"message": "bad key"}}, status_code=401)
    body = await request.json()
    seen["chat"].append({"model": body["model"], "format": body["response_format"]["type"]})
    system, prompt = body["messages"][0]["content"], body["messages"][1]["content"]
    name = str(body["response_format"].get("json_schema", {}).get("name"))
    answer = answer_for(f"{system} {name}", str(prompt), isinstance(prompt, list))
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(answer)}}]}


@app.post("/openai/v1/audio/transcriptions")
async def transcribe():
    seen["audio"] += 1
    return {"text": "Speech line", "segments": [{"text": "Speech line", "no_speech_prob": 0.01}]}


@app.post("/openai/v1/audio/speech")
async def speak():
    return Response(b"RIFF", media_type="audio/wav")


@app.get("/seen")
async def report():
    return seen


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
