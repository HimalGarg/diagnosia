"""Checks a drafted note against its transcript with plain rules, not AI.

Adapted from OpenScribe's note verifier (MIT, see THIRD_PARTY_NOTICES.md), with
checks added for the errors that harm patients: "500mg" read as no number, a
lost negation, a swapped side, and a changed unit. A transcript in another
language can't be matched word by word, so those statements are left for the
clinician to check rather than marked wrong.
"""
import re
from typing import Dict, List, Set

VERSION = "1.0"

STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "of", "to", "in", "on", "for", "with", "by", "as", "is",
    "are", "was", "were", "be", "been", "it", "this", "that", "at", "from", "can", "do", "does", "we", "you",
    "they", "i", "he", "she", "has", "have", "had", "will", "patient", "reports", "states", "says", "no", "not",
    "denies", "denied", "without", "never", "nil", "negative",
}
NEGATION = re.compile(r"\b(no|not|denies|denied|without|never|nil|negative for|absent|none)\b|n't\b", re.IGNORECASE)
LATERALITY = {"left": "left", "lt": "left", "right": "right", "rt": "right", "bilateral": "both", "both": "both"}
NUMBER_UNIT = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(mcg|µg|ug|mg|gm|g|kg|ml|l|iu|units?|mmhg|%|tablets?|tabs?|days?|weeks?|months?)\b",
    re.IGNORECASE,
)
UNIT_ALIASES = {"µg": "mcg", "ug": "mcg", "gm": "g", "unit": "units", "tablet": "tab", "tablets": "tab", "tabs": "tab"}
NUMBER = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

MIN_OVERLAP_SUPPORTED = 0.5
MIN_OVERLAP_UNCERTAIN = 0.25


def _normal(text: str) -> str:
    return (text or "").translate(DEVANAGARI_DIGITS).lower()


def tokens(text: str) -> Set[str]:
    words = re.sub(r"[^\w-]+", " ", _normal(text)).split()
    return {word for word in words if len(word) >= 2 and word not in STOP_WORDS and not word.isdigit()}


def numbers(text: str) -> Set[str]:
    return {_canonical_number(found) for found in NUMBER.findall(_normal(text))}


def _canonical_number(value: str) -> str:
    return value.rstrip("0").rstrip(".") if "." in value else value


def quantities(text: str) -> Set[tuple]:
    found = set()
    for number, unit in NUMBER_UNIT.findall(_normal(text)):
        unit = UNIT_ALIASES.get(unit, unit)
        found.add((_canonical_number(number), unit))
    return found


def sides(text: str) -> Set[str]:
    return {LATERALITY[word] for word in re.findall(r"[a-z]+", _normal(text)) if word in LATERALITY}


def is_negated(text: str) -> bool:
    return bool(NEGATION.search(_normal(text)))


def overlap(claim: str, evidence: str) -> float:
    claim_tokens = tokens(claim)
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & tokens(evidence)) / len(claim_tokens)


def _is_english(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return not letters or sum(char.isascii() for char in letters) / len(letters) > 0.8


def check_statement(text: str, cited: List[int], segments: Dict[int, str]) -> dict:
    """Finds the best evidence for one statement and says what doesn't line up."""
    scored = sorted(
        ((overlap(text, line), sequence) for sequence, line in segments.items()),
        key=lambda item: (item[0], item[1] in cited),
        reverse=True,
    )
    best_score, best_sequence = scored[0] if scored else (0.0, None)
    window = [segments[n] for n in {*cited, *([best_sequence] if best_sequence is not None else [])} if n in segments]
    evidence = " ".join(window)
    transcript = " ".join(segments.values())
    reasons: List[str] = []

    missing_numbers = numbers(text) - numbers(transcript)
    if missing_numbers:
        reasons.append(f"Number not in the transcript: {', '.join(sorted(missing_numbers))}")
    changed_units = {f"{number} {unit}" for number, unit in quantities(text) - quantities(transcript)}
    if changed_units and not missing_numbers:
        reasons.append(f"Amount or unit differs from the transcript: {', '.join(sorted(changed_units))}")

    if not _is_english(transcript):
        if not cited:
            reasons.append("No transcript line cited")
        reasons.append("Transcript is in another language: compare by hand")
        return {"verdict": "unsupported" if missing_numbers else "uncertain", "score": 0.0, "reasons": reasons, "evidence": sorted(cited)}

    if sides(text) and sides(text) - sides(transcript):
        reasons.append("Side of the body differs from the transcript")
    if best_score >= MIN_OVERLAP_UNCERTAIN and evidence and is_negated(text) != is_negated(evidence):
        reasons.append("Possible lost or added negation: check 'no' or 'denies'")

    if best_score < MIN_OVERLAP_UNCERTAIN or missing_numbers:
        verdict = "unsupported"
        if best_score < MIN_OVERLAP_UNCERTAIN:
            reasons.append("Couldn't find this in the transcript")
    elif reasons or best_score < MIN_OVERLAP_SUPPORTED:
        verdict = "uncertain"
    else:
        verdict = "supported"
    return {
        "verdict": verdict,
        "score": round(best_score, 2),
        "reasons": reasons,
        "evidence": sorted({*[n for n in cited if n in segments], *([best_sequence] if best_sequence is not None and best_score > 0 else [])}),
    }


def verify_note(content: dict, segments: Dict[int, str]) -> dict:
    """Adds a check to every statement, and a summary for the review screen."""
    counts = {"supported": 0, "uncertain": 0, "unsupported": 0}
    for section in content["sections"]:
        for statement in section["statements"]:
            check = check_statement(statement["text"], statement.get("evidence_segments", []), segments)
            if statement.get("origin") == "ai_summary" and check["verdict"] == "supported":
                check["verdict"] = "uncertain"
                check["reasons"].append("AI summary: confirm this reflects your judgement")
            if statement.get("uncertain"):
                reason = statement.get("uncertainty_reason") or "The AI marked this as uncertain"
                check["reasons"].append(reason)
                check["verdict"] = "uncertain" if check["verdict"] == "supported" else check["verdict"]
            statement["check"] = check
            counts[check["verdict"]] += 1
    checked = counts["supported"] + counts["unsupported"] + counts["uncertain"]
    status = "verified"
    if checked and counts["unsupported"] / checked > 0.3:
        status = "failed"
    elif counts["unsupported"] or counts["uncertain"]:
        status = "partial"
    content["verification"] = {"status": status, "version": VERSION, **counts}
    return content


def open_flags(content: dict) -> int:
    return sum(
        1
        for section in content["sections"]
        for statement in section["statements"]
        if statement.get("check", {}).get("verdict") in ("uncertain", "unsupported")
    )
