"""What an administrator can change without touching code: AI models, tools and defaults.

Every setting is declared here with its type and limits, so nothing unexpected can
be stored. Safety switches are deliberately not settings: those stay in safety.py,
where no web page can reach them.
"""
import re
import sqlite3
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.auth import require_doctor, require_user
from backend.config import settings
from backend.db import connect, get_db
from backend.records import audit, vault
from backend.records.store import dumps, now_iso

router = APIRouter(prefix="/api/platform")

MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{1,80}$")
TOOLS = {
    "diagnofast": "DiagnoFast",
    "prescription": "Prescription Analyzer",
    "skin": "Skin Cancer Detection",
    "clinscribe": "ClinScribe",
    "lablens": "LabLens",
    "referrals": "ReferralLoop",
    "discharge": "DischargeCare",
}
PROVIDERS = {"groq": "Groq", "gemini": "Google Gemini"}
# Reading handwriting and scanned reports is where Gemini does best, so these three prefer it.
# Anything else follows the general provider setting.
READING_TOOLS = {
    "prescription_read": "Prescription: reading photos",
    "prescription_explain": "Prescription: explaining to patients",
    "prescription_plan": "Prescription: planning the daily times",
    "lablens_read": "LabLens: reading scanned reports",
}
# Chosen by comparing models on synthetic photos (backend/tests/ai_eval). The larger model was the
# only one to keep every value on its own row in a tilted, glared lab photo. Explaining needs no eyes.
READING_MODELS = {"prescription_read": "gemini-3.5-flash", "prescription_explain": "gemini-3.5-flash-lite", "prescription_plan": "gemini-3.5-flash", "lablens_read": "gemini-3.5-flash"}
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
TEXT_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
VISION_MODELS = ["qwen/qwen3.8-27b", "meta-models/muse-spark-open"]
SPEECH_MODELS = ["whisper-large-v3", "whisper-large-v3-turbo"]

SETTINGS: Dict[str, dict] = {
    "ai.model.clinscribe_note": {"section": "AI models", "label": "ClinScribe: drafting notes", "type": "model", "choices": TEXT_MODELS, "default": "openai/gpt-oss-120b"},
    "ai.model.prescription_explain": {"section": "AI models", "label": "Prescription: explaining to patients", "type": "model", "choices": TEXT_MODELS, "default": "openai/gpt-oss-20b"},
    "ai.model.prescription_plan": {"section": "AI models", "label": "Prescription: planning the daily times", "type": "model", "choices": TEXT_MODELS, "default": "openai/gpt-oss-120b"},
    "ai.model.prescription_read": {"section": "AI models", "label": "Prescription: reading photos", "type": "model", "choices": VISION_MODELS, "default": "qwen/qwen3.8-27b"},
    "ai.model.lablens_read": {"section": "AI models", "label": "LabLens: reading scanned reports", "type": "model", "choices": VISION_MODELS, "default": "qwen/qwen3.8-27b"},
    "ai.model.diagnofast_turn": {"section": "AI models", "label": "DiagnoFast: choosing the next question", "type": "model", "choices": TEXT_MODELS, "default": "openai/gpt-oss-20b"},
    "ai.model.diagnofast_report": {"section": "AI models", "label": "DiagnoFast: writing the doctor's report", "type": "model", "choices": TEXT_MODELS, "default": "openai/gpt-oss-120b"},
    "ai.model.transcription": {"section": "AI models", "label": "Speech to text", "type": "model", "choices": SPEECH_MODELS, "default": "whisper-large-v3"},
    "ai.primary": {"section": "AI providers", "label": "Provider tried first", "type": "choice", "choices": list(PROVIDERS), "default": "groq"},
    "ai.strategy": {"section": "AI providers", "label": "When both providers have a key", "type": "choice", "choices": ["fallback", "round_robin"], "default": "fallback"},
    **{
        f"ai.provider.{tool}": {"section": "AI providers", "label": f"{label}: provider", "type": "choice", "choices": ["gemini", "groq", "default"], "default": "gemini"}
        for tool, label in READING_TOOLS.items()
    },
    **{
        f"ai.gemini_model.{tool}": {"section": "AI providers", "label": f"{label}: Gemini model", "type": "model", "choices": GEMINI_MODELS, "default": READING_MODELS[tool]}
        for tool, label in READING_TOOLS.items()
    },
    "ai.model.gemini": {"section": "AI providers", "label": "Gemini model, for every tool", "type": "model", "choices": GEMINI_MODELS, "default": "gemini-3.5-flash-lite"},
    "clinscribe.default_language": {"section": "ClinScribe", "label": "Language spoken in consultations", "type": "choice", "choices": ["en-IN", "hi-IN"], "default": "en-IN"},
    "clinscribe.custom_headings": {"section": "ClinScribe", "label": "Your clinic's note headings", "type": "list", "default": [], "max_items": 12},
    "lablens.use_ai_reading": {"section": "LabLens", "label": "Use AI for reports we can't read locally", "type": "bool", "default": True},
    "referrals.routine_days": {"section": "ReferralLoop", "label": "Suggested reply time, routine (days)", "type": "int", "min": 1, "max": 90, "default": 10},
    "referrals.urgent_days": {"section": "ReferralLoop", "label": "Suggested reply time, urgent (days)", "type": "int", "min": 1, "max": 30, "default": 3},
    "reminders.respond_within_hours": {"section": "Reminders", "label": "Hours a patient has to confirm", "type": "int", "min": 1, "max": 168, "default": 24},
    **{f"tools.{name}": {"section": "Tools", "label": label, "type": "bool", "default": True} for name, label in TOOLS.items()},
}


class SettingValue(BaseModel):
    value: Any


class ApiKey(BaseModel):
    key: str = Field(min_length=10, max_length=200)


def validate(key: str, value: Any) -> Any:
    spec = SETTINGS.get(key)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such setting.")
    kind = spec["type"]
    problem = HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"That isn't a valid value for {spec['label']}.")
    if kind == "bool":
        if not isinstance(value, bool):
            raise problem
    elif kind == "int":
        if not isinstance(value, int) or isinstance(value, bool) or not spec["min"] <= value <= spec["max"]:
            raise problem
    elif kind == "choice":
        if value not in spec["choices"]:
            raise problem
    elif kind == "model":
        # Other model names are allowed, because the provider's list changes.
        if not isinstance(value, str) or not MODEL_ID.match(value):
            raise problem
    elif kind == "text":
        if not isinstance(value, str) or not value.strip() or len(value) > spec["max_length"]:
            raise problem
        value = value.strip()
    elif kind == "list":
        if not isinstance(value, list) or len(value) > spec["max_items"]:
            raise problem
        if not all(isinstance(item, str) and 0 < len(item.strip()) <= 80 for item in value):
            raise problem
        value = [item.strip() for item in value]
    return value


def _stored(db: sqlite3.Connection) -> Dict[str, Any]:
    import json

    return {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key, value FROM platform_settings")}


def get(key: str, db: Optional[sqlite3.Connection] = None) -> Any:
    connection = db or connect()
    try:
        return _stored(connection).get(key, SETTINGS[key]["default"])
    except sqlite3.OperationalError:  # the table doesn't exist yet, during first start
        return SETTINGS[key]["default"]
    finally:
        if db is None:
            connection.close()


def model_for(tool: str) -> str:
    """The Groq model chosen for a tool. Gemini uses one model for every tool."""
    return get(f"ai.model.{tool}")


def gemini_models(tool: Optional[str] = None) -> List[str]:
    """Gemini models to try, best first. The larger ones are often busy, so the general model backs them up."""
    general = get("ai.model.gemini")
    if tool in READING_TOOLS:
        return list(dict.fromkeys([get(f"ai.gemini_model.{tool}"), general]))
    return [general]


def provider_order(db: Optional[sqlite3.Connection] = None, tool: Optional[str] = None) -> List[str]:
    """Providers that have a key, in the order they should be tried, before any rotation."""
    primary = get("ai.primary", db)
    if tool in READING_TOOLS:
        pinned = get(f"ai.provider.{tool}", db)
        primary = pinned if pinned in PROVIDERS else primary
    names = [primary] + [name for name in PROVIDERS if name != primary]
    return [name for name in names if api_key(db, name)]


def enabled_tools(db: sqlite3.Connection) -> List[str]:
    stored = _stored(db)
    return [name for name in TOOLS if stored.get(f"tools.{name}", True)]


def tool_enabled(name: str):
    """A route guard. A tool switched off is off for the API too, not only hidden."""

    def check(db: sqlite3.Connection = Depends(get_db)) -> None:
        if not get(f"tools.{name}", db):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                {"code": "tool_disabled", "message": f"{TOOLS[name]} is switched off in Settings."},
            )

    return check


def api_key(db: Optional[sqlite3.Connection] = None, provider: str = "groq") -> str:
    """The environment wins, then a key saved in Settings. Empty means this provider isn't set up."""
    from_environment = getattr(settings, f"{provider}_api_key")
    if from_environment:
        return from_environment
    connection = db or connect()
    try:
        row = connection.execute("SELECT sealed FROM platform_secrets WHERE name = ?", (f"{provider}_api_key",)).fetchone()
        return vault.decrypt(row["sealed"], f"secret:{provider}_api_key").decode() if row else ""
    except (sqlite3.OperationalError, vault.VaultUnavailable, vault.IntegrityFailure):
        return ""
    finally:
        if db is None:
            connection.close()


def is_platform_admin(db: sqlite3.Connection, user: sqlite3.Row) -> bool:
    return (
        db.execute(
            """
            SELECT 1 FROM organization_members AS member JOIN organizations ON organizations.id = member.organization_id
            WHERE member.user_id = ? AND member.role = 'admin' AND organizations.verification_status = 'verified'
            """,
            (user["id"],),
        ).fetchone()
        is not None
    )


def _require_admin(db: sqlite3.Connection, user: sqlite3.Row, action: str) -> None:
    if not is_platform_admin(db, user):
        audit.record_denied(user, action, None, "needs an administrator of a verified organization")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an administrator of a verified organization can change this.")


def setup_steps(db: sqlite3.Connection) -> List[dict]:
    """What is needed for the platform to work, each with what to do about it."""
    key_ok, _ = vault.status()
    has_ai = bool(provider_order(db))
    verified_orgs = db.execute("SELECT COUNT(*) FROM organizations WHERE verification_status = 'verified'").fetchone()[0]
    return [
        {"name": "Document encryption", "done": key_ok, "help": "Run: python -m backend.operator_cli setup-local"},
        {"name": "A verified organization", "done": verified_orgs > 0, "help": "Run: python -m backend.operator_cli setup-local"},
        {"name": "AI key", "done": has_ai, "help": "Add a free Groq or Gemini key under AI keys below."},
    ]


@router.get("/config")
def public_config(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)) -> dict:
    """What the interface needs to know. It holds no secrets."""
    return {
        "tools": enabled_tools(db),
        "ai_ready": bool(provider_order(db)),
        "defaults": {
            "referrals_routine_days": get("referrals.routine_days", db),
            "referrals_urgent_days": get("referrals.urgent_days", db),
            "reminders_respond_hours": get("reminders.respond_within_hours", db),
            "clinscribe_language": get("clinscribe.default_language", db),
            "clinscribe_custom_headings": get("clinscribe.custom_headings", db),
        },
        "can_administer": user["role"] == "doctor" and user["account_status"] == "verified" and is_platform_admin(db, user),
    }


@router.get("/settings")
def list_settings(user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)) -> dict:
    stored = _stored(db)
    keys = {}
    for provider in PROVIDERS:
        row = db.execute("SELECT last_four FROM platform_secrets WHERE name = ?", (f"{provider}_api_key",)).fetchone()
        keys[provider] = {
            "label": PROVIDERS[provider],
            "configured": bool(api_key(db, provider)),
            "source": "environment" if getattr(settings, f"{provider}_api_key") else ("settings" if row else None),
            "last_four": row["last_four"] if row else None,
        }
    return {
        "can_edit": is_platform_admin(db, user),
        "settings": [{**spec, "key": key, "value": stored.get(key, spec["default"])} for key, spec in SETTINGS.items()],
        "setup": setup_steps(db),
        "ai_key": keys["groq"],
        "ai_keys": keys,
    }


@router.put("/settings/{key}")
def save_setting(
    key: str, request: SettingValue, user=Depends(require_doctor), db: sqlite3.Connection = Depends(get_db)
) -> dict:
    _require_admin(db, user, "platform.setting.save")
    value = validate(key, request.value)
    db.execute(
        """
        INSERT INTO platform_settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at
        """,
        (key, dumps(value), user["id"], now_iso()),
    )
    audit.record(db, user, "platform.setting_changed", resource_type="setting", resource_id=key, reason=dumps(value)[:200])
    return {"key": key, "value": value}


@router.put("/ai-key")
def save_ai_key(
    request: ApiKey,
    provider: Literal["groq", "gemini"] = Query("groq"),
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Sealed with the document key ring, and never sent back to a browser."""
    _require_admin(db, user, "platform.ai_key.save")
    key = request.key.strip()
    name = f"{provider}_api_key"
    db.execute(
        """
        INSERT INTO platform_secrets (name, sealed, last_four, updated_by, updated_at) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (name) DO UPDATE SET sealed = excluded.sealed, last_four = excluded.last_four,
            updated_by = excluded.updated_by, updated_at = excluded.updated_at
        """,
        (name, vault.encrypt(key.encode(), f"secret:{name}"), key[-4:], user["id"], now_iso()),
    )
    audit.record(db, user, "platform.ai_key_changed", resource_type="setting", resource_id=name)
    return {"configured": True, "last_four": key[-4:]}


@router.delete("/ai-key", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_key(
    provider: Literal["groq", "gemini"] = Query("groq"),
    user=Depends(require_doctor),
    db: sqlite3.Connection = Depends(get_db),
) -> None:
    _require_admin(db, user, "platform.ai_key.delete")
    name = f"{provider}_api_key"
    db.execute("DELETE FROM platform_secrets WHERE name = ?", (name,))
    audit.record(db, user, "platform.ai_key_removed", resource_type="setting", resource_id=name)
