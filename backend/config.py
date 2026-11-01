"""Settings for the Diagnosia backend, read from the environment or a .env file."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"  # the only folder served to the public
STORAGE_DIR = ROOT_DIR / "backend" / "storage"

load_dotenv(ROOT_DIR / ".env")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


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
    default_consent: bool = field(default_factory=lambda: os.environ.get("DIAGNOSIA_DEFAULT_CONSENT", "1").strip() != "0")
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
    # Optional integrations. Each is off unless set to exactly 1, and none is needed to run the app.
    demo_mode: bool = field(default_factory=lambda: os.environ.get("DIAGNOSIA_DEMO_MODE", "1").strip().lower() not in ("0", "false", "no", "off"))
    mongodb_enabled: bool = field(default_factory=lambda: _flag("ENABLE_MONGODB_ATLAS_INTEGRATION"))
    mongodb_uri: str = field(default_factory=lambda: _real_key(os.environ.get("MONGODB_ATLAS_URI", "")))
    mongodb_database: str = field(default_factory=lambda: os.environ.get("MONGODB_DATABASE_NAME") or os.environ.get("MONGODB_DATABASE") or "diagnosia_atlas")
    mongodb_timeout_ms: int = field(default_factory=lambda: int(os.environ.get("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "5000")))
    mongodb_change_streams: bool = field(default_factory=lambda: _flag("ENABLE_MONGODB_CHANGE_STREAMS"))
    mongodb_change_stream_collection: str = field(default_factory=lambda: os.environ.get("MONGODB_CHANGE_STREAM_COLLECTION", "care_events"))
    yugabyte_enabled: bool = field(default_factory=lambda: _flag("ENABLE_YUGABYTEDB_INTEGRATION"))
    yugabyte_dsn: str = field(default_factory=lambda: _real_key(os.environ.get("YUGABYTEDB_YSQL_DSN", "")))
    yugabyte_pool_max: int = field(default_factory=lambda: int(os.environ.get("YUGABYTEDB_POOL_MAX_SIZE", "4")))
    meko_enabled: bool = field(default_factory=lambda: _flag("ENABLE_MEKO_INTEGRATION"))
    meko_api_key: str = field(default_factory=lambda: _real_key(os.environ.get("MEKO_API_KEY", "")))
    meko_datapack_id: str = field(default_factory=lambda: os.environ.get("MEKO_DATAPACK_ID", ""))
    meko_url: str = field(default_factory=lambda: os.environ.get("MEKO_MCP_URL", "https://mcp.mekodata.ai/mcp"))
    meko_patient_packs_enabled: bool = field(default_factory=lambda: _flag("ENABLE_MEKO_PATIENT_PACKS"))
    # Deleting a datapack doesn't return its slot, so creations are capped well below the plan's limit.
    meko_pack_limit: int = field(default_factory=lambda: int(os.environ.get("MEKO_PACK_LIMIT", "6")))
    event_batch_size: int = field(default_factory=lambda: int(os.environ.get("EVENT_DELIVERY_BATCH_SIZE", "50")))
    event_max_attempts: int = field(default_factory=lambda: int(os.environ.get("EVENT_DELIVERY_MAX_ATTEMPTS", "8")))
    event_backoff_seconds: int = field(default_factory=lambda: int(os.environ.get("EVENT_DELIVERY_BACKOFF_SECONDS", "30")))
    ai_base_url: str = field(
        default_factory=lambda: os.environ.get("AI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    )


settings = Settings()
