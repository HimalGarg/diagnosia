"""Settings for the Diagnosia backend, read from the environment or a .env file."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"  # the only folder served to the public
STORAGE_DIR = ROOT_DIR / "backend" / "storage"

load_dotenv(ROOT_DIR / ".env")


@dataclass
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("DIAGNOSIA_DB", STORAGE_DIR / "diagnosia.db"))
    )
    audio_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DIAGNOSIA_AUDIO", STORAGE_DIR / "audio"))
    )
    # Set DIAGNOSIA_HTTPS=1 in production so session cookies are HTTPS-only.
    cookie_secure: bool = field(default_factory=lambda: os.environ.get("DIAGNOSIA_HTTPS") == "1")
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "claude-opus-5-5"))
    sarvam_key: str = field(default_factory=lambda: os.environ.get("SARVAM_API_KEY", ""))


settings = Settings()
