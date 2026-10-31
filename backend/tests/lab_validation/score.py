"""Measures LabLens field by field against the synthetic formats.

Run with:  python -m backend.tests.lab_validation.score
"""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.lablens import extract, parser
from backend.tests.lab_validation.formats import Case, cases

FIELDS = ("found", "value", "unit", "low", "high")


@dataclass
class Score:
    hits: Dict[str, int] = field(default_factory=lambda: {name: 0 for name in FIELDS})
    totals: Dict[str, int] = field(default_factory=lambda: {name: 0 for name in FIELDS})
    wrong_values: List[str] = field(default_factory=list)
    silent_misses: List[str] = field(default_factory=list)


def _close(first: Optional[float], second: Optional[float]) -> bool:
    if first is None or second is None:
        return first is second
    return abs(first - second) < 1e-9


def _find(expected, results: List[dict]) -> Optional[dict]:
    for result in results:
        if expected.code and result["loinc_code"] == expected.code:
            return result
    return None


def score_case(case: Case, score: Score, header: Dict[str, Dict[str, int]]) -> dict:
    text = extract.native_text(case.pdf)
    results = parser.parse_results(text) if len(text.strip()) >= extract.MIN_TEXT_CHARS else []
    parsed_header = parser.parse_header(text)
    for key, want, got in (("patient", case.patient, parsed_header["name_on_report"]), ("collected", case.collected, parsed_header["collected_at"])):
        if want is not None:
            header[key]["total"] += 1
            header[key]["hits"] += got.lower() == want.lower() if isinstance(got, str) else False
    flagged = any(issue for result in results for issue in result["issues"])
    for expected in case.results:
        result = _find(expected, results)
        score.totals["found"] += 1
        if result is None:
            if case.readable_locally and not flagged:
                score.silent_misses.append(f"{case.name}: {expected.name}")
            continue
        score.hits["found"] += 1
        score.totals["value"] += 1
        if _close(result["value_number"], expected.value):
            score.hits["value"] += 1
        else:
            score.wrong_values.append(f"{case.name}: {expected.name} read {result['value_text']}, expected {expected.value}")
        if expected.unit:
            score.totals["unit"] += 1
            score.hits["unit"] += result["unit"] == expected.unit
        if expected.low is not None:
            score.totals["low"] += 1
            score.hits["low"] += _close(result["reference_low"], expected.low)
        if expected.high is not None:
            score.totals["high"] += 1
            score.hits["high"] += _close(result["reference_high"], expected.high)
    return {"case": case.name, "read": len(results), "expected": len(case.results)}


def run() -> dict:
    score = Score()
    header = {"patient": {"hits": 0, "total": 0}, "collected": {"hits": 0, "total": 0}}
    per_case = [score_case(case, score, header) for case in cases() if case.readable_locally]
    return {
        "fields": {name: {"hits": score.hits[name], "total": score.totals[name]} for name in FIELDS},
        "header": header,
        "wrong_values": score.wrong_values,
        "silent_misses": score.silent_misses,
        "per_case": per_case,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
