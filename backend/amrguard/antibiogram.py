"""Validates susceptibility data and builds facility-level antibiograms.

Native code using the published first-isolate approach. The AGPL Foundation AMR
Guard prototype and GPL AMR R package were reviewed but no code was taken from
either. Output is aggregate surveillance, never advice for an individual patient.
"""
import csv
import io
import json
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

METHOD = (
    "First isolate per patient, per organism, per period. Percent susceptible is S / (S + I + R). "
    "Organisms below the minimum isolate count are not reported."
)
MIN_ISOLATES = 30
REQUIRED_COLUMNS = (
    "patient_ref", "isolate_ref", "specimen", "organism", "antibiotic", "interpretation",
    "standard", "standard_version", "collected_on", "laboratory",
)
MAX_ROWS = 50000


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((Path(__file__).parent / "catalog.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _organism_names() -> Dict[str, str]:
    names: Dict[str, str] = {}
    for organism, aliases in catalog()["organisms"].items():
        names[organism.lower()] = organism
        for alias in aliases:
            names[alias.lower()] = organism
    return names


def parse_csv(raw: bytes) -> Tuple[List[dict], List[dict], int]:
    """Returns good rows, rejected rows with reasons, and the row count."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Please save the file as UTF-8 CSV.") from None
    reader = csv.DictReader(io.StringIO(text))
    missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")

    good, rejected = [], []
    seen = set()
    total = 0
    for line, row in enumerate(reader, start=2):
        total += 1
        if total > MAX_ROWS:
            raise ValueError(f"Files are limited to {MAX_ROWS} rows.")
        parsed, problems = validate_row(row)
        if parsed is not None:
            key = (parsed["isolate_ref"], parsed["antibiotic"])
            if key in seen:
                problems.append("The same isolate and antibiotic appear twice in this file")
            seen.add(key)
        if problems:
            rejected.append({"line": line, "problems": problems})
        else:
            good.append(parsed)
    return good, rejected, total


def validate_row(row: Dict[str, Optional[str]], today: Optional[date] = None) -> Tuple[Optional[dict], List[str]]:
    today = today or date.today()
    data = catalog()
    value = {key: (row.get(key) or "").strip() for key in REQUIRED_COLUMNS + ("mic", "zone_mm")}
    problems = [f"{column} is empty" for column in REQUIRED_COLUMNS if not value[column]]

    organism = _organism_names().get(value["organism"].lower())
    if value["organism"] and organism is None:
        problems.append(f"Unknown organism: {value['organism']}")
    antibiotic = value["antibiotic"].upper()
    if value["antibiotic"] and antibiotic not in data["antibiotics"]:
        problems.append(f"Unknown antibiotic code: {value['antibiotic']}")
    interpretation = value["interpretation"].upper()
    if value["interpretation"] and interpretation not in ("S", "I", "R"):
        problems.append(f"Interpretation must be S, I or R, not {value['interpretation']}")
    if value["standard"] and value["standard"].upper() not in data["standards"]:
        problems.append(f"Unknown testing standard: {value['standard']}")
    if value["collected_on"]:
        try:
            collected = date.fromisoformat(value["collected_on"])
            if collected > today:
                problems.append("The collection date is in the future")
        except ValueError:
            problems.append("collected_on must look like 2026-10-31")
    if not value["mic"] and not value["zone_mm"]:
        problems.append("Needs an MIC or a zone diameter")
    if problems:
        return None, problems
    return {
        **value,
        "organism": organism,
        "antibiotic": antibiotic,
        "interpretation": interpretation,
        "standard": value["standard"].upper(),
    }, []


def first_isolates(results: List[dict]) -> Tuple[List[dict], int]:
    """Keeps each patient's earliest isolate of each organism. Returns rows and isolates dropped."""
    earliest: Dict[tuple, tuple] = {}
    for row in results:
        key = (row["patient_ref"], row["organism"])
        candidate = (row["collected_on"], row["isolate_ref"])
        if key not in earliest or candidate < earliest[key]:
            earliest[key] = candidate
    kept_isolates = {(key[1], value[1]) for key, value in earliest.items()}
    kept = [row for row in results if (row["organism"], row["isolate_ref"]) in kept_isolates]
    all_isolates = {(row["organism"], row["isolate_ref"]) for row in results}
    return kept, len(all_isolates) - len(kept_isolates)


def build(results: List[dict], min_isolates: int = MIN_ISOLATES) -> dict:
    """Returns the table and a data-quality report. Too few isolates means no figure at all."""
    kept, duplicates = first_isolates(results)
    by_organism: Dict[str, List[dict]] = defaultdict(list)
    for row in kept:
        by_organism[row["organism"]].append(row)

    table = []
    for organism in sorted(by_organism):
        rows = by_organism[organism]
        isolates = len({row["isolate_ref"] for row in rows})
        entry = {"organism": organism, "isolates": isolates, "reported": isolates >= min_isolates, "antibiotics": []}
        if entry["reported"]:
            by_drug: Dict[str, List[str]] = defaultdict(list)
            for row in rows:
                by_drug[row["antibiotic"]].append(row["interpretation"])
            for code in sorted(by_drug):
                calls = by_drug[code]
                tested = len(calls)
                entry["antibiotics"].append(
                    {
                        "code": code,
                        "name": catalog()["antibiotics"][code],
                        "tested": tested,
                        "percent_susceptible": round(100 * calls.count("S") / tested, 1) if tested >= min_isolates else None,
                        "note": "" if tested >= min_isolates else f"Tested in fewer than {min_isolates} isolates",
                    }
                )
        table.append(entry)

    standards = sorted({f"{row['standard']} {row['standard_version']}" for row in results})
    warnings = []
    if len(standards) > 1:
        warnings.append(f"More than one testing standard in this period: {', '.join(standards)}")
    below = [entry["organism"] for entry in table if not entry["reported"]]
    if below:
        warnings.append(f"Not reported, below {min_isolates} isolates: {', '.join(below)}")
    return {
        "table": table,
        "quality": {
            "rows": len(results),
            "isolates": len({row["isolate_ref"] for row in results}),
            "repeat_isolates_excluded": duplicates,
            "standards": standards,
            "laboratories": sorted({row["laboratory"] for row in results}),
            "warnings": warnings,
        },
    }


def quarter_of(collected_on: str) -> str:
    moment = date.fromisoformat(collected_on)
    return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"


def trend(results: List[dict], organism: str, antibiotic: str, min_isolates: int = MIN_ISOLATES) -> List[dict]:
    """Percent susceptible by quarter, with the same first-isolate and minimum-count rules."""
    by_quarter: Dict[str, List[dict]] = defaultdict(list)
    for row in results:
        if row["organism"] == organism:
            by_quarter[quarter_of(row["collected_on"])].append(row)
    points = []
    for quarter in sorted(by_quarter):
        kept, _ = first_isolates(by_quarter[quarter])
        calls = [row["interpretation"] for row in kept if row["antibiotic"] == antibiotic]
        points.append(
            {
                "quarter": quarter,
                "tested": len(calls),
                "percent_susceptible": round(100 * calls.count("S") / len(calls), 1) if len(calls) >= min_isolates else None,
            }
        )
    return points
