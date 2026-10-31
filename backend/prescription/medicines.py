"""Checks medicine names read from a prescription against a list of Indian medicines.

Handwriting can be misread as a different, real-looking name, so a name that
isn't on the list is flagged for the patient to check. This only ever flags
names. It never changes what was read.
"""
import csv
import logging
import re
import threading
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process

from backend.config import ROOT_DIR

DATA_FILE = (
    ROOT_DIR / "datasets" / "medicines" / "indian-medicine-dataset" / "DATA" / "indian_medicine_data.csv"
)
CLOSE_SCORE = 84  # out of 100, how alike two names must be to suggest one

# Words that describe the form of a medicine rather than name it.
FORM_WORDS = {
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps", "syrup", "syp",
    "suspension", "susp", "injection", "inj", "cream", "ointment", "oint", "gel", "drop", "drops",
    "lotion", "solution", "sol", "powder", "sachet", "respules", "inhaler", "mg", "mcg", "gm", "ml",
    "iu", "kid", "kids", "forte", "sr", "er", "xr", "mr", "dt", "cr", "od", "t", "c", "i", "neb", "nebs",
    "nebulizer", "nebuliser", "nebulisation", "respule", "rotacap", "rotacaps",
}

logger = logging.getLogger("diagnosia.medicines")
_load_lock = threading.Lock()


@dataclass
class Match:
    status: str  # found, close, not_found or unchecked
    name: Optional[str] = None  # the listed name, as a suggestion or confirmation
    contains: Optional[str] = None  # the active ingredients

    def to_dict(self) -> dict:
        return asdict(self)


def normalize(name: str) -> str:
    """Reduces "Tab. Augmentin 625 Duo" to "augmentin duo"."""
    without_brackets = re.sub(r"\(.*?\)", " ", name.lower())
    words = re.findall(r"[a-z]+", without_brackets)
    return " ".join(word for word in words if word not in FORM_WORDS)


def _listed_name(name: str) -> str:
    """The brand as a person would say it, without the strength and form."""
    kept = [word for word in re.findall(r"[A-Za-z]+", re.sub(r"\(.*?\)", " ", name)) if word.lower() not in FORM_WORDS]
    return " ".join(kept)


def _ingredients(composition: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", composition).strip()


class MedicineList:
    def __init__(self, entries: Dict[str, Tuple[str, str]]):
        self.entries = entries
        self.keys: List[str] = list(entries)

    @classmethod
    def from_rows(cls, rows) -> "MedicineList":
        entries: Dict[str, Tuple[str, str]] = {}

        def add(key: str, name: str, contains: str) -> None:
            if len(key) >= 3:
                entries.setdefault(key, (name, contains))

        for row in rows:
            name = (row.get("name") or "").strip()
            parts = [row.get("short_composition1") or "", row.get("short_composition2") or ""]
            contains = " + ".join(filter(None, (_ingredients(part) for part in parts)))
            key = normalize(name)
            if key:
                add(key, _listed_name(name), contains)
                # Doctors often write only the first word of a brand.
                add(key.split()[0], _listed_name(name).split()[0], contains)
            for part in parts:
                generic = _ingredients(part)
                if generic:
                    add(normalize(generic), generic, generic)
        return cls(entries)

    def check(self, written: str) -> Match:
        key = normalize(written)
        if len(key) < 3:
            return Match("not_found")
        if key in self.entries:
            name, contains = self.entries[key]
            return Match("found", name, contains)
        best = process.extractOne(key, self.keys, scorer=fuzz.ratio, score_cutoff=CLOSE_SCORE)
        if best is None:
            return Match("not_found")
        name, contains = self.entries[best[0]]
        return Match("close", name, contains)


@lru_cache(maxsize=1)
def _load() -> Optional[MedicineList]:
    if not DATA_FILE.exists():
        logger.warning("Medicine list not found at %s, so names won't be checked", DATA_FILE)
        return None
    with DATA_FILE.open(encoding="utf-8", errors="replace", newline="") as stream:
        return MedicineList.from_rows(csv.DictReader(stream))


def get_medicine_list() -> Optional[MedicineList]:
    """The list from disk, or None if it isn't installed, so the check is skipped."""
    with _load_lock:  # loading takes seconds, and only needs doing once
        return _load()


def check_name(written: str) -> dict:
    medicines = get_medicine_list()
    return (medicines.check(written) if medicines else Match("unchecked")).to_dict()
