import pytest


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/login.html",
        "/diagnofast_hiw.html",
        "/skin_cancer_hiw.html",
        "/prescription_analyzer_hiw.html",
        "/optikinesis_hiw.html",
        "/optikinesis_download.html",
        "/css/index.css",
    ],
)
def test_public_pages_are_served(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/.git/config",
        "/backend/app.py",
        "/backend/storage/diagnosia.db",
        "/archive/php/connect.php",
        "/archive/talkie/.env",
        "/connect.php",
        "/../backend/app.py",
        "/%2e%2e/backend/app.py",
        "/..%2fbackend%2fapp.py",
        "/..%5cbackend%5capp.py",
        "/css/../../backend/app.py",
    ],
)
def test_private_files_are_not_served(client, path):
    response = client.get(path)
    assert response.status_code in (404, 405)
    assert "FastAPI" not in response.text


def test_api_docs_are_off(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
