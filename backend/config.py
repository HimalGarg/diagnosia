"""Settings for the Diagnosia backend, read from the environment or a .env file."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"  # the only folder served to the public
STORAGE_DIR = ROOT_DIR / "backend" / "storage"

load_dotenv(ROOT_DIR / ".env")


def _real_key(value: str) -> str:
    return "" if not value or "your" in value.lower() else value


@dataclass
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("DIAGNOSIA_DB", STORAGE_DIR / "diagnosia.db"))
    )
    documents_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DIAGNOSIA_DOCUMENTS", STORAGE_DIR / "documents"))
    )
    # A JSON key ring kept outside the repository and away from the documents. See vault.py.
    document_key_file: str = field(default_factory=lambda: os.environ.get("DIAGNOSIA_DOCUMENT_KEY_FILE", ""))
    # development, staging or production. Anything unknown counts as production.
    environment: str = field(default_factory=lambda: os.environ.get("DIAGNOSIA_ENV", "development"))
    # Safety switches. Each is off unless set to exactly 1, and pilot mode is on unless set to exactly 0.
    # Turning one on does nothing by itself: see safety.py for what each one also requires.
    clinical_pilot_mode: bool = field(default_factory=lambda: os.environ.get("CLINICAL_PILOT_MODE") != "0")
    allow_real_patient_data: bool = field(default_factory=lambda: os.environ.get("ALLOW_REAL_PATIENT_DATA") == "1")
    enable_unvalidated_clinical_rules: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_UNVALIDATED_CLINICAL_RULES") == "1"
    )
    enable_external_messaging: bool = field(default_factory=lambda: os.environ.get("ENABLE_EXTERNAL_MESSAGING") == "1")
    # Set DIAGNOSIA_HTTPS=1 in production so session cookies are HTTPS-only.
    cookie_secure: bool = field(default_factory=lambda: os.environ.get("DIAGNOSIA_HTTPS") == "1")
    # A real key, or leave empty and add one in Settings. Placeholders count as empty.
    groq_api_key: str = field(default_factory=lambda: _real_key(os.environ.get("GROQ_API_KEY", "")))
    # A second provider, used as a fallback or in turn. Its OpenAI-compatible endpoint is the default.
    gemini_api_key: str = field(default_factory=lambda: _real_key(os.environ.get("GEMINI_API_KEY", "")))
    gemini_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
        ).rstrip("/")
    )
    ai_base_url: str = field(
        default_factory=lambda: os.environ.get("AI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    )


settings = Settings()
