import re
import shutil
import subprocess

import pytest

from backend.config import WEB_DIR


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/login.html",
        "/care.html",
        "/js/care/app.js",
        "/css/care.css",
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


TOOL_NAMES = [
    "DiagnoFast", "ClinScribe", "LabLens", "MedGuard", "Skin Cancer Detection", "Prescription Analyzer",
    "ReferralLoop", "DischargeCare", "ChronicCare", "MaternaCare", "AMR Guard", "OptiKinesis",
]


def test_the_homepage_has_one_catalogue_of_all_twelve_tools_in_visit_order(client):
    page = client.get("/index.html").text
    catalogue = page[page.index('class="catalogue"'):page.index('id="FAQ"')]
    assert re.findall(r'<h4><a href="[^"]+">([^<]+)</a></h4>', catalogue) == TOOL_NAMES
    assert "Medical Tools" not in page and "Diagnosia Care" not in page


def test_the_faq_covers_every_tool(client):
    page = client.get("/index.html").text
    faq = page[page.index('id="FAQ"'):page.index("Have more questions")]
    for name in TOOL_NAMES:
        assert name in faq
    assert "four AI tools" not in faq


def test_the_homepage_no_longer_shows_reviews_of_tools_that_do_not_exist(client):
    assert "reviews.png" not in client.get("/index.html").text


def test_the_workspace_lists_the_same_tools_in_the_same_order(client):
    source = client.get("/js/care/catalogue.js").text
    assert re.findall(r"label: '([^']+)'", source) == [name for name in TOOL_NAMES if name != "OptiKinesis"]


@pytest.mark.parametrize(
    "path, target",
    [
        ("/diagnofast.html", "care.html#/diagnofast"),
        ("/skin_cancer.html", "care.html#/skin"),
        ("/prescription_analyzer.html", "care.html#/prescription"),
        ("/dashboard.html", "care.html#/account"),
    ],
)
def test_old_tool_pages_lead_into_the_workspace(client, path, target):
    assert f"url={target}" in client.get(path).text


def test_skin_keeps_its_research_labels_and_licence_credits(client):
    source = client.get("/js/care/skin.js").text
    assert "Research demo, not for clinical use" in source
    assert "PanDerm" in source and "CC-BY-NC-ND 4.0" in source and "ISIC 2019" in source


def test_sign_in_only_returns_to_places_inside_the_workspace(client):
    assert r"/^care\.html(#\/[a-z0-9/-]*)?$/" in client.get("/js/auth.js").text


def test_the_workspace_may_show_heatmaps_but_no_other_data_scripts(client):
    policy = client.get("/care.html").headers["content-security-policy"]
    assert "img-src 'self' data: blob:" in policy
    assert "script-src 'self';" in policy


def test_every_workspace_script_parses(tmp_path):
    """One stray bracket in any module stops the whole workspace, and only a parser can see it."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't installed")
    for script in sorted((WEB_DIR / "js" / "care").glob("*.js")):
        copy = tmp_path / f"{script.stem}.mjs"
        copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
        checked = subprocess.run([node, "--check", str(copy)], capture_output=True, text=True, timeout=30)
        assert checked.returncode == 0, f"{script.name}: {checked.stderr[:300]}"


def test_care_pages_have_no_analytics(client):
    assert "gtag" not in client.get("/care.html").text


def test_the_workspace_never_writes_html_from_data(client):
    from backend.config import WEB_DIR

    for script in (WEB_DIR / "js" / "care").glob("*.js"):
        text = script.read_text()
        assert "innerHTML" not in text, script.name
        assert "insertAdjacentHTML" not in text, script.name
        assert "document.write" not in text, script.name


def test_the_workspace_never_passes_empty_parts_to_the_page(client):
    from backend.config import WEB_DIR

    # replaceChildren prints null and lists as text. show() is the safe one.
    for script in (WEB_DIR / "js" / "care").glob("*.js"):
        uses = script.read_text().count(".replaceChildren(")
        assert uses == (2 if script.name == "ui.js" else 0), script.name
