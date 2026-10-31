"""Settings, tool switches, the AI key, local setup and the whole journey end to end."""
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import demo, llm, settings_store
from backend.app import app
from backend.clinscribe.drafting import NoteDraft
from backend.config import settings
from backend.db import connect
from backend.operator_cli import main as operator
from backend.private_files import is_private
from backend.tests.conftest import FakeAI, verified_organization


@pytest.fixture
def admin(person):
    account = person("doctor", "Anil", "Shah")
    verified_organization(account)
    return account


def put(client, key, value):
    return client.put(f"/api/platform/settings/{key}", json={"value": value})


def test_an_admin_can_change_the_model_for_a_tool(admin):
    assert put(admin, "ai.model.clinscribe_note", "openai/gpt-oss-20b").status_code == 200
    assert settings_store.model_for("clinscribe_note") == "openai/gpt-oss-20b"


def test_the_chosen_model_is_what_gets_called(admin, monkeypatch):
    put(admin, "ai.model.prescription_explain", "qwen/qwen3.8-27b")
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    seen = {}

    def fake(url, **kwargs):
        seen.update(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"sections": [], "missing_information": []}'}, "finish_reason": "stop"}]})

    monkeypatch.setattr(httpx, "post", fake)
    llm.ask("s", "c", NoteDraft, tool="prescription_explain")
    assert seen["model"] == "qwen/qwen3.8-27b"


@pytest.mark.parametrize(
    "key, value",
    [
        ("referrals.routine_days", 0), ("referrals.routine_days", "ten"), ("referrals.routine_days", True),
        ("tools.lablens", "yes"), ("clinscribe.default_language", "fr-FR"), ("ai.model.clinscribe_note", "bad model; DROP"),
        ("clinscribe.custom_headings", ["x"] * 13), ("clinscribe.custom_headings", "Subjective"),
    ],
)
def test_bad_values_are_refused(admin, key, value):
    assert put(admin, key, value).status_code == 422


def test_unknown_settings_cannot_be_stored(admin):
    assert put(admin, "allow_real_patient_data", True).status_code == 404


@pytest.mark.parametrize("name", ["ALLOW_REAL_PATIENT_DATA", "CLINICAL_PILOT_MODE", "ENABLE_EXTERNAL_MESSAGING"])
def test_safety_switches_are_not_settings(name):
    assert not any(name.lower() in key.lower() for key in settings_store.SETTINGS)


def test_only_admins_of_verified_organizations_can_change_settings(doctor, person):
    assert put(doctor, "tools.lablens", False).status_code == 403
    founder = person("doctor")
    founder.post("/api/organizations", json={"name": "Self-made", "type": "clinic"})
    assert put(founder, "tools.lablens", False).status_code == 403
    assert put(person("patient"), "tools.lablens", False).status_code == 403


def test_changes_are_audited(admin):
    put(admin, "referrals.routine_days", 7)
    db = connect()
    assert db.execute("SELECT 1 FROM audit_events WHERE action = 'platform.setting_changed'").fetchone()
    db.close()


def test_a_tool_switched_off_is_off_for_the_api(admin, doctor, patient):
    put(admin, "tools.lablens", False)
    response = doctor.get(f"/api/addons/lablens/patients/{patient['id']}/reports")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "tool_disabled"
    assert "lablens" not in doctor.get("/api/platform/config").json()["tools"]


def test_switching_a_tool_back_on_restores_it(admin, doctor):
    put(admin, "tools.lablens", False)
    put(admin, "tools.lablens", True)
    assert "lablens" in doctor.get("/api/platform/config").json()["tools"]


def test_reminder_timing_uses_the_setting(admin, doctor, patient):
    put(admin, "reminders.respond_within_hours", 6)
    reminder = doctor.post(
        f"/api/patients/{patient['id']}/reminders", json={"title": "Check", "scheduled_for": "2099-01-01T00:00:00+00:00"}
    ).json()
    assert reminder["respond_within_hours"] == 6


def test_ai_reading_can_be_switched_off(admin, doctor, patient, monkeypatch):
    from backend.tests.conftest import grant
    from backend.tests.test_lablens import blank_pdf, extraction

    put(admin, "lablens.use_ai_reading", False)
    grant(doctor, patient["id"], "ai_processing")
    ai = FakeAI(None)
    monkeypatch.setattr(llm, "ask", ai)
    report = doctor.post(
        "/api/addons/lablens/reports", data={"patient_id": patient["id"]}, files={"file": ("r.pdf", blank_pdf(), "application/pdf")}
    ).json()
    assert extraction(doctor, report["id"])["status"] == "needs_manual_entry"
    assert ai.calls == []


def test_an_ai_key_is_sealed_and_never_returned(admin, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    secret = "gsk_synthetic_secret_value_1234"
    assert admin.put("/api/platform/ai-key", json={"key": secret}).json() == {"configured": True, "last_four": "1234"}
    assert settings_store.api_key() == secret
    body = admin.get("/api/platform/settings").text
    assert secret not in body
    assert "1234" in body
    db = connect()
    db.execute("PRAGMA wal_checkpoint(FULL)")
    db.close()
    assert secret.encode() not in settings.db_path.read_bytes()


def test_a_saved_key_makes_ai_ready(admin, doctor, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert doctor.get("/api/platform/config").json()["ai_ready"] is False
    admin.put("/api/platform/ai-key", json={"key": "gsk_synthetic_secret_value_1234"})
    assert doctor.get("/api/platform/config").json()["ai_ready"] is True


def test_the_environment_key_wins(admin, monkeypatch):
    admin.put("/api/platform/ai-key", json={"key": "gsk_synthetic_secret_value_1234"})
    monkeypatch.setattr(settings, "groq_api_key", "gsk_from_environment")
    assert settings_store.api_key() == "gsk_from_environment"


def test_only_admins_can_save_a_key(doctor):
    assert doctor.put("/api/platform/ai-key", json={"key": "gsk_synthetic_secret_value"}).status_code == 403


def test_a_key_can_be_removed(admin, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    admin.put("/api/platform/ai-key", json={"key": "gsk_synthetic_secret_value_1234"})
    assert admin.delete("/api/platform/ai-key").status_code == 204
    assert settings_store.api_key() == ""


def test_no_key_gives_a_message_that_says_what_to_do(doctor, patient, monkeypatch):
    from backend.tests.conftest import grant

    monkeypatch.setattr(settings, "groq_api_key", "")
    grant(doctor, patient["id"], "ai_scribe")
    encounter = doctor.post(f"/api/patients/{patient['id']}/encounters", json={}).json()
    session = doctor.post(f"/api/addons/clinscribe/encounters/{encounter['id']}/sessions", json={}).json()
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/segments", json={"text": "A cough"})
    doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/state", json={"state": "stopped"})
    response = doctor.post(f"/api/addons/clinscribe/sessions/{session['id']}/draft-note", json={})
    assert response.status_code == 503
    assert "Settings" in response.json()["detail"]


def test_setup_steps_show_what_is_missing(admin, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    steps = {step["name"]: step["done"] for step in admin.get("/api/platform/settings").json()["setup"]}
    assert steps == {"Document encryption": True, "A verified organization": True, "AI key": False}


@pytest.fixture
def journey(client):
    """Runs the demo through the real API, as setup-local does."""
    people = {}
    accounts = {"doctor": "doctor", "specialist": "doctor", "patient": "patient"}
    from backend.tests.conftest import Person

    for name, role in accounts.items():
        people[name] = Person(role, name.title(), "Demo", f"{name}@example.com")
    organizations = {
        "clinic": verified_organization(people["doctor"], demo.DEMO_ORGANIZATION),
        "specialist": verified_organization(people["specialist"], demo.SPECIALIST_ORGANIZATION),
    }
    return people, demo.seed(people, organizations), organizations


def test_the_journey_reaches_every_tool(journey):
    people, ids, _ = journey
    doctor, ravi = people["doctor"], ids["ravi"]
    assert len(doctor.get(f"/api/addons/lablens/patients/{ravi}/trends").json()["series"]) >= 3
    assert doctor.get(f"/api/addons/clinscribe/sessions/{ids['session']}").json()["state"] == "stopped"
    assert doctor.get(f"/api/addons/referrals/patients/{ravi}").json()[0]["status"] == "booked"
    assert doctor.get("/api/addons/dischargecare/board").json()[0]["status"] == "active"
    assert doctor.get(f"/api/patients/{ravi}/reminders").json()


def test_the_journey_is_visible_to_the_patient(journey):
    people, ids, _ = journey
    patient = people["patient"]
    assert patient.get(f"/api/addons/lablens/patients/{ids['ravi']}/trends").status_code == 200
    assert patient.get(f"/api/patients/{ids['ravi']}/tasks").json()
    assert patient.get(f"/api/addons/dischargecare/patients/{ids['ravi']}/episodes").json()
    assert patient.get(f"/api/addons/referrals/patients/{ids['ravi']}").json()[0]["side"] == "patient"


def test_a_note_can_go_from_transcript_to_signed_record(journey, monkeypatch):
    people, ids, _ = journey
    doctor = people["doctor"]
    draft = NoteDraft.model_validate(
        {
            "sections": [{"heading": "Subjective", "statements": [{"text": "No chest pain", "origin": "patient_statement", "evidence_segments": [2], "uncertain": False, "uncertainty_reason": ""}]}],
            "missing_information": [],
        }
    )
    monkeypatch.setattr(llm, "ask", FakeAI(draft))
    note = doctor.post(f"/api/addons/clinscribe/sessions/{ids['session']}/draft-note", json={}).json()
    signed = doctor.post(f"/api/addons/clinscribe/notes/{note['id']}/approve", json={"acknowledge_flags": True})
    assert signed.status_code == 200
    assert any(event["module"] == "clinscribe" for event in doctor.get(f"/api/patients/{ids['ravi']}/timeline").json())


def test_setup_local_refuses_outside_development(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "environment", "production")
    assert operator(["setup-local", "--home", str(tmp_path / "h"), "--env-file", ""]) == 1


def test_setup_local_refuses_with_real_data_switched_on(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "allow_real_patient_data", True)
    assert operator(["setup-local", "--home", str(tmp_path / "h"), "--env-file", ""]) == 1


def test_setup_local_builds_a_working_platform_once(client, tmp_path_factory, capsys):
    home = tmp_path_factory.mktemp("home")
    assert operator(["setup-local", "--home", str(home), "--env-file", ""]) == 0
    logins = home / "local-accounts.txt"
    assert is_private(logins)
    text = logins.read_text(encoding="utf-8")
    assert "Synthetic data only" in text
    assert operator(["setup-local", "--home", str(home), "--env-file", ""]) == 0
    assert "already exists" in capsys.readouterr().out
    db = connect()
    assert db.execute("SELECT COUNT(*) FROM organizations WHERE verification_status = 'verified'").fetchone()[0] == 2
    db.close()
    first = text.splitlines()[2]
    email, password = first.split(": ")[1].split("  ")[0], first.rsplit("password: ", 1)[1]
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
    assert fresh.get("/api/patients").json()


def test_setup_local_writes_env_lines_without_changing_others(client, tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    env = home / ".env"
    env.write_text("KEEP_ME=1\n")
    monkeypatch.setattr(settings, "document_key_file", "")
    assert operator(["setup-local", "--home", str(home), "--env-file", str(env)]) == 0
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "KEEP_ME=1"
    assert any(line.startswith("DIAGNOSIA_DOCUMENT_KEY_FILE=") for line in lines)
    assert "GROQ_API_KEY=your-groq-key" in lines


def test_setup_local_never_approves_clinical_content(client, tmp_path_factory):
    assert operator(["setup-local", "--home", str(tmp_path_factory.mktemp("home")), "--env-file", ""]) == 0
    db = connect()
    assert db.execute("SELECT COUNT(*) FROM loinc_mappings WHERE status = 'approved'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM governance_signoffs").fetchone()[0] == 0
    db.close()


def test_nothing_mentions_the_old_providers_in_code():
    root = Path(__file__).parents[1]
    for path in root.rglob("*.py"):
        if "tests" in path.parts or "training" in path.parts:
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert "anthropic" not in text, path
        assert "sarvam" not in text, path


def test_requirements_hold_no_anthropic_client():
    assert "anthropic" not in (Path(__file__).parents[1] / "requirements.txt").read_text(encoding="utf-8")
    assert os.path.exists(Path(__file__).parents[1] / "demo.py")
    assert json.dumps({})


def test_without_torch_the_skin_tool_says_it_isnt_installed(monkeypatch):
    from fastapi import HTTPException

    from backend.skin import routes as skin

    def missing():
        raise ImportError("torch")

    monkeypatch.setattr(skin, "get_classifier", missing)
    monkeypatch.setattr(skin.events, "emit_separately", lambda *args, **options: None)
    with pytest.raises(HTTPException) as refused:
        skin.classifier_dependency({"id": 1, "role": "doctor"})
    assert refused.value.status_code == 503


def test_each_provider_has_its_own_sealed_key(admin, monkeypatch):
    secret = "AQ.synthetic_gemini_value_9876"
    assert admin.put("/api/platform/ai-key?provider=gemini", json={"key": secret}).json() == {"configured": True, "last_four": "9876"}
    assert settings_store.api_key(None, "gemini") == secret
    assert settings_store.api_key() == ""  # Groq is untouched
    body = admin.get("/api/platform/settings").json()
    assert body["ai_keys"]["gemini"]["configured"] and not body["ai_keys"]["groq"]["configured"]
    assert secret not in admin.get("/api/platform/settings").text
    db = connect()
    db.execute("PRAGMA wal_checkpoint(FULL)")
    db.close()
    assert secret.encode() not in settings.db_path.read_bytes()


def test_a_gemini_key_alone_makes_ai_ready(admin, doctor):
    assert doctor.get("/api/platform/config").json()["ai_ready"] is False
    admin.put("/api/platform/ai-key?provider=gemini", json={"key": "AQ.synthetic_gemini_value_9876"})
    assert doctor.get("/api/platform/config").json()["ai_ready"] is True
    steps = {step["name"]: step["done"] for step in admin.get("/api/platform/settings").json()["setup"]}
    assert steps["AI key"] is True


def test_removing_one_providers_key_keeps_the_other(admin):
    admin.put("/api/platform/ai-key", json={"key": "gsk_synthetic_secret_value_1234"})
    admin.put("/api/platform/ai-key?provider=gemini", json={"key": "AQ.synthetic_gemini_value_9876"})
    assert admin.delete("/api/platform/ai-key?provider=gemini").status_code == 204
    assert settings_store.api_key(None, "gemini") == ""
    assert settings_store.api_key() == "gsk_synthetic_secret_value_1234"


def test_an_unknown_provider_is_refused(admin):
    assert admin.put("/api/platform/ai-key?provider=other", json={"key": "synthetic-value-123"}).status_code == 422


def test_only_admins_can_save_a_gemini_key(doctor):
    assert doctor.put("/api/platform/ai-key?provider=gemini", json={"key": "AQ.synthetic_gemini_value_9876"}).status_code == 403


def test_provider_settings_are_validated(admin):
    assert put(admin, "ai.strategy", "round_robin").status_code == 200
    assert put(admin, "ai.strategy", "random").status_code == 422
    assert put(admin, "ai.primary", "gemini").status_code == 200
    assert put(admin, "ai.primary", "other").status_code == 422
    assert put(admin, "ai.model.gemini", "bad model;drop").status_code == 422


def test_the_provider_order_follows_the_setting_and_skips_missing_keys(admin, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_a")
    monkeypatch.setattr(settings, "gemini_api_key", "AQ.b")
    assert settings_store.provider_order() == ["groq", "gemini"]
    put(admin, "ai.primary", "gemini")
    assert settings_store.provider_order() == ["gemini", "groq"]
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert settings_store.provider_order() == ["gemini"]


def test_the_reading_tools_have_their_own_provider_and_model_settings(admin):
    rows = {row["key"]: row["value"] for row in admin.get("/api/platform/settings").json()["settings"]}
    for tool in ("prescription_read", "prescription_explain", "lablens_read"):
        assert rows[f"ai.provider.{tool}"] == "gemini"
        assert rows[f"ai.gemini_model.{tool}"].startswith("gemini-")
    assert put(admin, "ai.provider.lablens_read", "groq").status_code == 200
    assert put(admin, "ai.provider.lablens_read", "other").status_code == 422
    assert put(admin, "ai.gemini_model.lablens_read", "gemini-2.5-flash").status_code == 200
    assert settings_store.gemini_models("lablens_read") == ["gemini-2.5-flash", "gemini-3.5-flash-lite"]
