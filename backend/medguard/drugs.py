"""Turns the name written on a record into active ingredients.

A match must be exact once doses and dose forms are removed. Medicines that are
only close, such as "Dolo Cold" next to "Dolo", stay unmatched and are shown as
suggestions: a confident wrong match is worse than an honest "couldn't match".
"""
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

DOSE_WORDS = re.compile(
    r"\b(tab|tabs|tablet|tablets|cap|caps|capsule|capsules|syp|syrup|susp|suspension|inj|injection|"
    r"oint|ointment|drops?|cream|gel)\b\.?",
    re.IGNORECASE,
)
STRENGTH = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mcg|mg|gm|g|ml|iu|%)?(?:/\d+(?:\.\d+)?\s*(?:mcg|mg|ml)?)?(?=\s|$)", re.IGNORECASE)
SEPARATORS = re.compile(r"\s*(?:\+|/|&|\band\b|\bwith\b)\s*", re.IGNORECASE)


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((KNOWLEDGE_DIR / "catalog.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads((KNOWLEDGE_DIR / "interactions.json").read_text(encoding="utf-8"))


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def clean_name(written: str) -> str:
    without_forms = DOSE_WORDS.sub(" ", written)
    without_strength = STRENGTH.sub(" ", without_forms)
    return _key(without_strength)


@lru_cache(maxsize=1)
def _index() -> Dict[str, List[str]]:
    data = catalog()
    index: Dict[str, List[str]] = {}
    for ingredient, details in data["ingredients"].items():
        index[_key(ingredient)] = [ingredient]
        for alias in details["aliases"]:
            index[_key(alias)] = [ingredient]
    for brand, ingredients in data["brands"].items():
        index[_key(brand)] = ingredients
    return index


def normalise(written: str) -> dict:
    """Returns matched ingredients, or none with close names to offer a person."""
    index = _index()
    cleaned = clean_name(written)
    if cleaned in index:
        return {"written": written, "ingredients": index[cleaned], "match_status": "matched", "suggestions": []}

    parts = [clean_name(part) for part in SEPARATORS.split(written) if clean_name(part)]
    if len(parts) > 1 and all(part in index for part in parts):
        ingredients = sorted({ingredient for part in parts for ingredient in index[part]})
        return {"written": written, "ingredients": ingredients, "match_status": "matched", "suggestions": []}

    close = difflib.get_close_matches(cleaned, index, n=3, cutoff=0.75)
    first_word = cleaned.split(" ")[0] if cleaned else ""
    if first_word in index and first_word not in close:
        close.insert(0, first_word)
    suggestions = [{"name": name, "ingredients": index[name]} for name in close]
    return {"written": written, "ingredients": [], "match_status": "unmatched", "suggestions": suggestions}


def classes_of(ingredient: str) -> List[str]:
    details = catalog()["ingredients"].get(ingredient)
    return details["classes"] if details else []


def known_ingredient(name: str) -> Optional[str]:
    found = _index().get(_key(name))
    return found[0] if found and len(found) == 1 else None
