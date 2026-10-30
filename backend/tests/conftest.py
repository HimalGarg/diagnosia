import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.config import settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def details():
    return {
        "first_name": "Asha",
        "last_name": "Rao",
        "email": "asha@example.com",
        "password": "a-long-password",
        "role": "doctor",
    }
