"""Decodes prescription shorthand with plain rules, not AI.

Anything not recognized returns None, so the patient is sent to a pharmacist
rather than given a guess.
"""
import re
from typing import Dict, List, Optional, Tuple

FREQUENCY = {
    "OD": "Once a day",
    "QD": "Once a day",
    "BD": "Twice a day",
    "BID": "Twice a day",
    "TDS": "Three times a day",
    "TID": "Three times a day",
    "QID": "Four times a day",
    "HS": "At bedtime",
    "SOS": "Only when needed",
    "PRN": "Only when needed",
}
TIMING = {"AC": "Before food", "PC": "After food", "BBF": "Before breakfast"}
# A medicine taken only when needed must never get a fixed time.
AS_NEEDED = {"SOS", "PRN"}
# Times a day each instruction implies, to catch a misread pattern.
DOSES_A_DAY = {"OD": 1, "QD": 1, "HS": 1, "BBF": 1, "BD": 2, "BID": 2, "TDS": 3, "TID": 3}
AMOUNTS = {"0": "none", "1": "one", "2": "two", "3": "three", "½": "half", "1/2": "half"}

AMOUNT = r"(1/2|½|\d)"
PATTERN = re.compile(rf"(?<![\d/–—-])\s*{AMOUNT}\s*[-–—]\s*{AMOUNT}\s*[-–—]\s*{AMOUNT}(?![\d/–—-])")
# Marks in three columns, where a dash or x means none: "1 - 1" or "x x 1".
MARK = r"(1/2|½|[0-3]|x|X|-|–|—)"
COLUMNS = re.compile(rf"^\s*{MARK}\s+{MARK}\s+{MARK}\s*$")
DURATION = re.compile(
    r"(?:x|for|×)\s*(\d+)\s*(days?|d|weeks?|wks?|w|months?|m)\b", re.IGNORECASE
)
UNITS = {"d": "day", "w": "week", "wk": "week", "m": "month"}


def _words(raw: str) -> List[str]:
    # B.D. and b.d are both BD.
    return re.findall(r"[A-Za-z]+", re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z]|\b)", "", raw).upper())


def _day_amounts(raw: str) -> Optional[Tuple[str, str, str]]:
    match = PATTERN.search(raw) or COLUMNS.match(raw)
    if not match:
        return None
    return tuple("0" if mark in ("x", "X", "-", "–", "—") else mark for mark in match.groups())


def decode(raw: str) -> Optional[str]:
    """Turns shorthand such as "1-0-1 PC x 5 days" into plain language."""
    parts: List[str] = []
    day_amounts = _day_amounts(raw)
    if day_amounts:
        morning, afternoon, night = (AMOUNTS.get(amount, amount) for amount in day_amounts)
        sentence = f"{morning} in the morning, {afternoon} in the afternoon, {night} at night"
        parts.append(sentence[0].upper() + sentence[1:])

    words = _words(raw)
    parts.extend(FREQUENCY[word] for word in words if word in FREQUENCY)
    parts.extend(TIMING[word] for word in words if word in TIMING)

    duration = DURATION.search(raw)
    if duration:
        amount, unit = int(duration.group(1)), duration.group(2).lower().rstrip("s")
        unit = UNITS.get(unit, unit)
        parts.append(f"Take it for {amount} {unit}{'' if amount == 1 else 's'}")

    return ". ".join(dict.fromkeys(parts)) if parts else None


def is_as_needed(raw: str) -> bool:
    """Checks whether a medicine is taken only as needed (SOS/PRN)."""
    return bool(AS_NEEDED & set(_words(raw)))


DURATION_PARSER = re.compile(
    r"(?:(?:x|for|×)\s*)?(\d+)\s*(days?|d|weeks?|wks?|w|months?|m)\b", re.IGNORECASE
)


def parse_duration_days(raw: str) -> Optional[int]:
    """Extracts duration in days from shorthand or duration string."""
    if not raw:
        return None
    match = DURATION_PARSER.search(raw)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower().rstrip("s")
    unit = UNITS.get(unit, unit)
    if unit == "day":
        return amount
    if unit == "week":
        return amount * 7
    if unit == "month":
        return amount * 30
    return None


def daily_slots(raw: str, frequency: str = "") -> Dict[str, str]:
    """When to take a medicine, only if the prescription states it exactly.

    If the marks disagree with the written frequency, one of them was misread,
    so no schedule is given rather than a possibly wrong one.
    """
    amounts = _day_amounts(raw)
    if not amounts or is_as_needed(frequency):
        return {}
    slots = zip(("morning", "afternoon", "night"), amounts)
    result = {slot: AMOUNTS.get(amount, amount) for slot, amount in slots if amount != "0"}
    expected = next((DOSES_A_DAY[word] for word in _words(frequency) if word in DOSES_A_DAY), None)
    return {} if expected is not None and expected != len(result) else result

