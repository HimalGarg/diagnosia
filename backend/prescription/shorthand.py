"""Decodes prescription shorthand with plain rules, not AI.

Anything not recognized returns None, so the patient is sent to a pharmacist
rather than given a guess.
"""
import re
from typing import Dict, List, Optional

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
TIMING = {"AC": "Before food", "PC": "After food"}
AMOUNTS = {"0": "none", "1": "one", "2": "two", "3": "three", "½": "half", "1/2": "half"}

AMOUNT = r"(1/2|½|\d)"
PATTERN = re.compile(rf"(?<![\d/]){AMOUNT}\s*[-–—]\s*{AMOUNT}\s*[-–—]\s*{AMOUNT}(?![\d/-])")
DURATION = re.compile(
    r"(?:x|for|×)\s*(\d+)\s*(days?|d|weeks?|wks?|w|months?|m)\b", re.IGNORECASE
)
UNITS = {"d": "day", "w": "week", "wk": "week", "m": "month"}


def _words(raw: str) -> List[str]:
    # B.D. and b.d are both BD.
    return re.findall(r"[A-Za-z]+", re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z]|\b)", "", raw).upper())


def decode(raw: str) -> Optional[str]:
    """Turns shorthand such as "1-0-1 PC x 5 days" into plain language."""
    parts: List[str] = []
    pattern = PATTERN.search(raw)
    if pattern:
        morning, afternoon, night = (AMOUNTS.get(amount, amount) for amount in pattern.groups())
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


def daily_slots(raw: str) -> Dict[str, str]:
    """When to take a medicine, only if the prescription states it exactly."""
    pattern = PATTERN.search(raw)
    if not pattern:
        return {}
    slots = zip(("morning", "afternoon", "night"), pattern.groups())
    return {slot: AMOUNTS.get(amount, amount) for slot, amount in slots if amount != "0"}
