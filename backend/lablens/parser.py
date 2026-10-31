"""Reads lab report text line by line, with plain rules first.

A value only gets a high-confidence mark if every check passes; everything else
goes to a person. Reference ranges are the laboratory's own and are never
replaced, and nothing here decides whether a result is good or bad.

The report-header patterns are adapted from FHIR-XTract (MIT, see
THIRD_PARTY_NOTICES.md).
"""
import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VERSION = "lablens-parser-1"

NUMBER = r"\d[\d,]*(?:\.\d+)?"
RANGE = re.compile(rf"({NUMBER})\s*(?:-|–|—|to)\s*({NUMBER})", re.IGNORECASE)
UPPER_ONLY = re.compile(rf"(?:<|≤|upto|up to|less than|below)\s*({NUMBER})", re.IGNORECASE)
LOWER_ONLY = re.compile(rf"(?:>|≥|more than|above|greater than)\s*({NUMBER})", re.IGNORECASE)
VALUE = re.compile(rf"^[\s:\-.]*([<>]?\s*{NUMBER})(?![\d-]*\s*(?:-|–|to)\s*\d)")
FLAG = re.compile(r"^(H|L|HH|LL|High|Low|\*)(?=\s|$)", re.IGNORECASE)
UNIT_TOKEN = re.compile(
    r"^(%|[a-zA-Zµμ^/0-9.]*/[a-zA-Zµμ^0-9.]+|fl|pg|gm%|mm/1st\s*hr|[a-zA-Zµμ]+/[a-zA-Zµμ]+)$", re.IGNORECASE
)
GENERIC_ROW = re.compile(rf"^([A-Za-z][A-Za-z ,()/\-]{{2,48}}?)\s*[:\-]?\s+({NUMBER})\s+(.*)$")
PARENTHETICAL = re.compile(r"^\s*\([^)]*\)")
SAMPLE_PREFIX = re.compile(r"^(?:sr\.?|s\.|serum|plasma|blood)\s+", re.IGNORECASE)

PATIENT_NAME = re.compile(
    r"(?:Patient(?:\s+Name)?|Name)\s*[:\-]\s*(?:Mrs\.?|Mr\.?|Ms\.?|Miss|Master|Baby|Smt\.?|Shri)?\s*"
    r"([A-Za-z][A-Za-z .']+?)(?=\s{2,}|\s+(?:Age|Sex|Gender|Ref|Lab|UHID|MR|Sample|DOB)\b|\n|$)",
    re.IGNORECASE,
)
COLLECTED = re.compile(
    r"(?:Collect(?:ed|ion)(?:\s+(?:Date|On|At))?|Sample\s+(?:Date|Collected(?:\s+On)?)|Registered(?:\s+On)?|"
    r"Report(?:ed)?\s+(?:Date|On))\s*[:\-]?\s*(\d{1,2}[/\-. ](?:\d{1,2}|[A-Za-z]{3,9})[/\-. ]\d{2,4})",
    re.IGNORECASE,
)
LABORATORY = re.compile(r"(?:Lab(?:oratory)?(?:\s+Name)?|Performed\s+at)\s*:\s*(.+)", re.IGNORECASE)
LAB_HEADER = re.compile(r"\b(lab|labs|laboratory|diagnostics?|patholog\w+)\b", re.IGNORECASE)
DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y", "%d/%b/%Y", "%d %B %Y",
    "%d/%m/%y", "%d-%m-%y", "%d.%m.%y", "%d-%b-%y", "%d/%b/%y", "%d %b %y",
)


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((Path(__file__).parent / "analytes.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def synonyms() -> List[Tuple[str, dict]]:
    pairs = [(name, analyte) for analyte in catalog()["analytes"] for name in analyte["synonyms"]]
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def normal_unit(unit: str) -> str:
    return (
        unit.lower().replace("µ", "u").replace("μ", "u").replace(" ", "").replace("cmm", "cumm")
        .replace("gm/", "g/").replace("gm%", "g/dl")
    )


def parse_number(text: str) -> Tuple[Optional[float], List[str]]:
    """Commas split thousands in lab reports. Any other use is flagged, not guessed."""
    cleaned = text.strip().lstrip("<>").strip()
    issues = []
    if "," in cleaned:
        if re.fullmatch(r"\d{1,3}(,\d{2,3})+(\.\d+)?", cleaned):
            cleaned = cleaned.replace(",", "")
        else:
            return None, ["Unclear decimal or thousands separator"]
    try:
        return float(cleaned), issues
    except ValueError:
        return None, ["Couldn't read this number"]


def parse_reference(text: str) -> dict:
    span = RANGE.search(text)
    if span:
        low, _ = parse_number(span.group(1))
        high, _ = parse_number(span.group(2))
        return {"reference_text": span.group(0).strip(), "reference_low": low, "reference_high": high}
    upper = UPPER_ONLY.search(text)
    if upper:
        high, _ = parse_number(upper.group(1))
        return {"reference_text": upper.group(0).strip(), "reference_low": None, "reference_high": high}
    lower = LOWER_ONLY.search(text)
    if lower:
        low, _ = parse_number(lower.group(1))
        return {"reference_text": lower.group(0).strip(), "reference_low": low, "reference_high": None}
    return {"reference_text": "", "reference_low": None, "reference_high": None}


def _match_analyte(line: str) -> Optional[Tuple[dict, str, str]]:
    found = _match_name(line)
    prefix = SAMPLE_PREFIX.match(line.strip(" -•*\t"))
    if found is None and prefix:
        found = _match_name(line.strip(" -•*\t")[prefix.end():])
    return found


def _match_name(line: str) -> Optional[Tuple[dict, str, str]]:
    lowered = line.lower().strip(" -•*\t")
    offset = len(line) - len(line.lstrip(" -•*\t"))
    for name, analyte in synonyms():
        if lowered.startswith(name):
            after = lowered[len(name):]
            if after and (after[0].isalnum() or after[0] == "-" and after[1:2].isalpha()):
                continue
            end = offset + len(name)
            remainder = line[end:]
            while PARENTHETICAL.match(remainder):
                remainder = PARENTHETICAL.sub("", remainder, count=1)
            return analyte, line[offset:end].strip(), remainder
    return None


def _split_row(remainder: str) -> Optional[dict]:
    value = VALUE.match(remainder)
    if not value:
        return None
    rest = remainder[value.end():].strip()
    flag = ""
    found_flag = FLAG.match(rest)
    if found_flag:
        flag = found_flag.group(1)
        rest = rest[found_flag.end():].strip()

    reference = parse_reference(rest)
    leftover = rest.replace(reference["reference_text"], " ") if reference["reference_text"] else rest
    unit = ""
    for token in leftover.split():
        if not unit and UNIT_TOKEN.match(token):
            unit = token
        elif not flag and FLAG.match(token):
            flag = token
    return {"value_text": value.group(1).replace(" ", ""), "reported_flag": flag, "unit": unit, **reference}


def check(row: dict, analyte: Optional[dict]) -> List[str]:
    """What a person needs to look at for this result. Empty means nothing stood out."""
    issues = list(row.pop("number_issues", []))
    number = row.get("value_number")
    if analyte is None:
        issues.append("Test not recognised: not mapped to a standard code")
    if not row["unit"]:
        issues.append("No unit found")
    elif analyte is not None and normal_unit(row["unit"]) not in analyte["units"]:
        issues.append(f"Unexpected unit for {analyte['name']}: {row['unit']}")
    if not row["reference_text"]:
        issues.append("No reference range found")
    if number is not None and analyte is not None and not analyte["min"] <= number <= analyte["max"]:
        issues.append("Value is outside what is physically plausible: possible misread decimal")
    if number is not None:
        low, high = row.get("reference_low"), row.get("reference_high")
        outside = (low is not None and number < low) or (high is not None and number > high)
        reported = row["reported_flag"].lower()
        if reported in ("h", "hh", "high", "l", "ll", "low") and row["reference_text"] and not outside:
            issues.append("The report's high/low mark doesn't match its range")
    return issues


def build_result(original_name: str, line: str, parsed: dict, analyte: Optional[dict]) -> dict:
    number, number_issues = parse_number(parsed["value_text"])
    row = {
        "original_name": original_name,
        "original_line": line.strip()[:300],
        "loinc_code": analyte["loinc"] if analyte else None,
        "standard_name": analyte["name"] if analyte else None,
        "value_number": number,
        "number_issues": number_issues,
        **parsed,
    }
    issues = check(row, analyte)
    return {**row, "issues": issues, "confidence": "review" if issues else "high"}


def analyte_named(name: str) -> Optional[dict]:
    found = _match_analyte(name)
    return found[0] if found and not found[2].strip() else None


def _second_column(line: str) -> Optional[int]:
    """Where a second test starts on a line, for two-column reports."""
    lowered = line.lower()
    for name, _ in synonyms():
        start = 0
        while (position := lowered.find(name, start)) != -1:
            start = position + 1
            after = lowered[position + len(name):position + len(name) + 1]
            before = lowered[position - 1] if position else " "
            if position < 8 or before.isalnum() or after.isalnum():
                continue
            if re.search(r"\d", lowered[:position]) and re.search(r"\d", lowered[position + len(name):]):
                return position
    return None


def parse_results(text: str) -> List[dict]:
    results = []
    lines = []
    for line in text.splitlines():
        split = _second_column(line) if _match_analyte(line) else None
        if split is None:
            lines.append((line, False))
        else:
            lines += [(line[:split], True), (line[split:], True)]
    for line, from_columns in lines:
        if not line.strip():
            continue
        matched = _match_analyte(line)
        if matched:
            analyte, name, remainder = matched
            parsed = _split_row(remainder)
            if parsed:
                result = build_result(name, line, parsed, analyte)
                if from_columns:
                    result["issues"].append("Read from a line holding two tests: check the columns")
                    result["confidence"] = "review"
                results.append(result)
            continue
        generic = GENERIC_ROW.match(line.strip())
        # Unknown tests are kept only when the line clearly has a range, so headers stay out.
        if generic and parse_reference(generic.group(3))["reference_text"] and RANGE.search(generic.group(3)):
            parsed = _split_row(line.strip()[len(generic.group(1)):])
            if parsed:
                results.append(build_result(generic.group(1).strip(), line, parsed, None))
    return results


def parse_date(text: str) -> Optional[str]:
    cleaned = re.sub(r"\s+", " ", text.strip())
    for pattern in DATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
        return parsed.isoformat() if parsed <= date.today() else None
    return None


def parse_header(text: str) -> Dict[str, str]:
    name = PATIENT_NAME.search(text)
    collected = COLLECTED.search(text)
    laboratory = LABORATORY.search(text)
    lab_name = laboratory.group(1).strip() if laboratory else ""
    if not lab_name:
        first_lines = [line.strip() for line in text.splitlines() if line.strip()][:3]
        lab_name = next((line for line in first_lines if LAB_HEADER.search(line)), "")
    return {
        "name_on_report": name.group(1).strip() if name else "",
        "collected_at": parse_date(collected.group(1)) if collected else None,
        "laboratory": lab_name[:160],
    }


def identity_check(name_on_report: str, first_name: str, last_name: str) -> str:
    if not name_on_report:
        return "unknown"
    words = {word.lower().strip(".") for word in name_on_report.split()}
    return "match" if first_name.lower() in words and last_name.lower() in words else "mismatch"


def range_status(number: Optional[float], low: Optional[float], high: Optional[float]) -> str:
    """Where a value sits against the printed range. Not a clinical judgement."""
    if number is None or (low is None and high is None):
        return "no_range"
    if low is not None and number < low:
        return "below_lab_range"
    if high is not None and number > high:
        return "above_lab_range"
    return "within_lab_range"
