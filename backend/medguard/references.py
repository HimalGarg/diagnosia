"""Public drug references: NLM RxNorm and openFDA labels.

Only drug names are sent, never anything about a patient. Results are suggestions
for a person to read: both sources are US-based and may not fit Indian products.
Replies are checked before use, and if either service is down or replies with
something unexpected, the answer is "unavailable" and nothing is cached.
"""
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx

from backend.records.store import now_iso

logger = logging.getLogger("diagnosia")

RXNAV_URL = "https://rxnav.nlm.nih.gov/REST"
OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
RXNORM_SOURCE = "RxNorm, U.S. National Library of Medicine"
OPENFDA_SOURCE = "openFDA, US drug labels"
LABEL_LIMITATION = "A label search is not a complete interaction check. Silence here means nothing."
TIMEOUT = 6
MAX_ATTEMPTS = 2
RETRY_PAUSE_SECONDS = 0.5
RETRY_STATUSES = (429, 500, 502, 503, 504)
CACHE_LIFETIME = timedelta(days=30)
MAX_SNIPPET = 900


def _cached(db: sqlite3.Connection, key: str) -> Optional[dict]:
    row = db.execute("SELECT value, fetched_at FROM terminology_cache WHERE cache_key = ?", (key,)).fetchone()
    if row is None or datetime.fromisoformat(row["fetched_at"]) < datetime.now(timezone.utc) - CACHE_LIFETIME:
        return None
    return json.loads(row["value"])


def _remember(db: sqlite3.Connection, key: str, value: dict) -> None:
    db.execute(
        "INSERT OR REPLACE INTO terminology_cache (cache_key, value, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(value), now_iso()),
    )


def _get(url: str, params: dict) -> Optional[dict]:
    """Returns the parsed reply, {} for not found, or None if it can't be trusted."""
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_PAUSE_SECONDS)
        try:
            response = httpx.get(url, params=params, timeout=TIMEOUT)
        except httpx.HTTPError as error:
            logger.warning("Drug reference unavailable: %s", type(error).__name__)
            continue
        if response.status_code == 404:
            return {}
        if response.status_code in RETRY_STATUSES:
            logger.warning("Drug reference returned %s", response.status_code)
            continue
        if response.status_code != 200:
            logger.warning("Drug reference returned %s", response.status_code)
            return None
        try:
            body = response.json()
        except ValueError:
            logger.warning("Drug reference sent something that isn't JSON")
            return None
        return body if isinstance(body, dict) else None
    return None


def _unavailable(source: str, **extra) -> dict:
    return {"available": False, "source": source, "retrieved_at": None, **extra}


def _rxnorm_identifiers(found: dict) -> Optional[List[str]]:
    identifiers = found.get("idGroup", {}).get("rxnormId", []) if isinstance(found.get("idGroup", {}), dict) else None
    if not isinstance(identifiers, list) or not all(isinstance(item, str) and item.isdigit() for item in identifiers):
        return None
    return identifiers


def _ingredient_names(related: dict) -> Optional[List[str]]:
    names: List[str] = []
    try:
        for group in related.get("relatedGroup", {}).get("conceptGroup", []):
            names += [concept["name"] for concept in group.get("conceptProperties", [])]
    except (AttributeError, KeyError, TypeError):
        return None
    return names if all(isinstance(name, str) for name in names) else None


def rxnorm_ingredients(db: sqlite3.Connection, name: str) -> dict:
    key = f"rxnorm:{name.lower()}"
    cached = _cached(db, key)
    if cached is not None:
        return cached
    failure = _unavailable(RXNORM_SOURCE, ingredients=[])
    found = _get(f"{RXNAV_URL}/rxcui.json", {"name": name, "search": 2})
    identifiers = _rxnorm_identifiers(found) if found is not None else None
    if identifiers is None:
        return failure
    ingredients: List[str] = []
    if identifiers:
        related = _get(f"{RXNAV_URL}/rxcui/{identifiers[0]}/related.json", {"tty": "IN"})
        names = _ingredient_names(related) if related is not None else None
        if names is None:
            return failure
        ingredients = names
    result = {
        "available": True,
        "source": RXNORM_SOURCE,
        "retrieved_at": now_iso(),
        "rxcui": identifiers[0] if identifiers else None,
        "ingredients": ingredients,
    }
    _remember(db, key, result)
    return result


def label_mentions(db: sqlite3.Connection, drug: str, other: str) -> dict:
    """Whether a US label for one drug mentions the other. Silence proves nothing."""
    key = f"openfda:{drug.lower()}:{other.lower()}"
    cached = _cached(db, key)
    if cached is not None:
        return cached
    failure = _unavailable(OPENFDA_SOURCE, mentioned=False, snippet="", limitation=LABEL_LIMITATION)
    search = f'openfda.generic_name:"{_quoted(drug)}" AND drug_interactions:"{_quoted(other)}"'
    found = _get(OPENFDA_LABEL_URL, {"search": search, "limit": 1})
    if found is None:
        return failure
    results = found.get("results", [])
    if not isinstance(results, list):
        return failure
    snippet = ""
    if results:
        sections = results[0].get("drug_interactions", []) if isinstance(results[0], dict) else None
        if not isinstance(sections, list) or not all(isinstance(item, str) for item in sections):
            return failure
        text = " ".join(sections)
        position = text.lower().find(other.lower())
        snippet = text[max(0, position - 200): position + MAX_SNIPPET - 200]
    result = {
        "available": True,
        "source": OPENFDA_SOURCE,
        "retrieved_at": now_iso(),
        "mentioned": bool(results),
        "snippet": snippet,
        "limitation": LABEL_LIMITATION,
    }
    _remember(db, key, result)
    return result


def _quoted(value: str) -> str:
    return "".join(char for char in value if char.isalnum() or char in " -")[:80]
