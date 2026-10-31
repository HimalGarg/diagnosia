import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import safety
from backend.app import app
from backend.config import Settings, settings
from backend.db import connect
from backend.operator_cli import main as operator
from backend.tests.conftest import verified_organization


@pytest.fixture
def db(client):
    connection = connect()
    yield connection
    connection.close()


def fake_environment(monkeypatch, **values):
    for name in ("CLINICAL_PILOT_MODE", "ALLOW_REAL_PATIENT_DATA", "ENABLE_EXTERNAL_MESSAGING"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings()


def test_the_defaults_are_the_safe_ones(monkeypatch):
    configured = fake_environment(monkeypatch)
    assert configured.clinical_pilot_mode is True
    assert configured.allow_real_patient_data is False
    assert configured.enable_external_messaging is False


@pytest.mark.parametrize("value", ["true", "yes", "on", "TRUE", "2", " 1", ""])
def test_only_an_exact_1_turns_a_switch_on(monkeypatch, value):
    configured = fake_environment(monkeypatch, ALLOW_REAL_PATIENT_DATA=value, ENABLE_EXTERNAL_MESSAGING=value)
    assert configured.allow_real_patient_data is False
    assert configured.enable_external_messaging is False


@pytest.mark.parametrize("value", ["false", "no", "off", "", "1"])
def test_only_an_exact_0_turns_pilot_mode_off(monkeypatch, value):
    assert fake_environment(monkeypatch, CLINICAL_PILOT_MODE=value).clinical_pilot_mode is True


def test_a_fresh_installation_is_not_ready(db):
    assert safety.ready_for_real_patients(db) is False
    assert safety.real_patient_data_allowed(db) is False
    assert safety.pilot_mode(db) is True


def test_the_flag_alone_does_not_allow_real_data(db, monkeypatch):
    monkeypatch.setattr(settings, "allow_real_patient_data", True)
    assert safety.real_patient_data_allowed(db) is False


def test_asking_for_real_data_without_readiness_stops_startup(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_real_patient_data", True)
    with pytest.raises(RuntimeError, match="refused to start"):
        with TestClient(app):
            pass


def test_turning_off_pilot_mode_without_readiness_stops_startup(client, monkeypatch):
    monkeypatch.setattr(settings, "clinical_pilot_mode", False)
    with pytest.raises(RuntimeError, match="refused to start"):
        with TestClient(app):
            pass


def test_pilot_mode_stays_on_even_if_the_switch_is_off(db, monkeypatch):
    monkeypatch.setattr(settings, "clinical_pilot_mode", False)
    assert safety.pilot_mode(db) is True


def test_production_without_https_or_keys_stops_startup(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "document_key_file", "")
    with pytest.raises(RuntimeError, match="https_cookies"):
        with TestClient(app):
            pass


def test_an_unknown_environment_stops_startup(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "prod")
    with pytest.raises(RuntimeError, match="Unknown"):
        with TestClient(app):
            pass


def test_an_unknown_environment_counts_as_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "prod")
    assert safety.is_production()


def test_messaging_without_a_provider_fails_the_check(db, monkeypatch):
    monkeypatch.setattr(settings, "enable_external_messaging", True)
    check = next(item for item in safety.readiness(db) if item.name == "external_messaging_reviewed")
    assert check.passed is False


def test_the_interface_gets_its_status_from_the_server(doctor):
    body = doctor.get("/api/system/status").json()
    assert body["pilot_mode"] is True
    assert body["real_patient_data_allowed"] is False
    assert "synthetic" in body["message"].lower()


def test_status_cannot_be_written_from_a_browser(doctor):
    for method in (doctor.post, doctor.put, doctor.patch, doctor.delete):
        assert method("/api/system/status").status_code in (404, 405)


def test_no_route_can_change_a_safety_setting():
    paths = " ".join(getattr(route, "path", "") for route in app.routes).lower()
    for word in ("override", "bypass", "force", "signoff", "allow_real"):
        assert word not in paths


def test_the_banner_is_in_the_page_before_any_script_runs(client):
    page = client.get("/care.html").text
    assert "Use synthetic data only" in page


def test_every_required_signoff_starts_missing(db):
    signed = [check for check in safety.readiness(db) if check.name.startswith("signoff:") and check.passed]
    assert signed == []


def test_a_signoff_records_a_decision_and_can_be_revoked(db):
    item = "laboratory_mapping_review"
    assert operator(["signoff", "--item", item, "--signed-by", "Synthetic", "--role", "Laboratory professional",
                     "--document-reference", "DOC-1", "--operator", "tester"]) == 0
    assert next(check for check in safety.readiness(db) if check.name == f"signoff:{item}").passed
    assert operator(["revoke-signoff", "--item", item, "--operator", "tester"]) == 0
    assert not next(check for check in safety.readiness(db) if check.name == f"signoff:{item}").passed


def test_an_unknown_signoff_is_refused(db):
    assert operator(["signoff", "--item", "everything", "--signed-by", "x", "--role", "y",
                     "--document-reference", "z", "--operator", "t"]) == 1


def test_readiness_never_passes_on_signoffs_alone(db):
    for item in safety.REQUIRED_SIGNOFFS:
        operator(["signoff", "--item", item, "--signed-by", "x", "--role", "y", "--document-reference", "z", "--operator", "t"])
    assert safety.ready_for_real_patients(db) is False


def test_there_is_no_all_in_one_override():
    source = (Path(__file__).parents[1] / "safety.py").read_text(encoding="utf-8").lower()
    assert "override" not in source.replace("no switch", "")
    assert "skip_readiness" not in source
    assert "force" not in source


def test_readiness_is_only_for_administrators_of_verified_organizations(doctor, person):
    assert doctor.get("/api/system/readiness").status_code == 403
    admin = person("doctor")
    verified_organization(admin)
    body = admin.get("/api/system/readiness").json()
    assert body["ready_for_real_patients"] is False
    assert body["total"] > 15


def test_readiness_exposes_no_secret(doctor, person, key_file):
    admin = person("doctor")
    verified_organization(admin)
    text = admin.get("/api/system/readiness").text
    assert str(key_file) not in text


def test_the_command_line_reports_not_ready(db, capsys):
    assert operator(["readiness"]) == 1
    assert "Ready for real patients: False" in capsys.readouterr().out


def test_external_messaging_has_no_provider_in_the_code():
    folder = Path(__file__).parents[1] / "reminders"
    for path in folder.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert not {"httpx", "requests", "twilio"} & imported
