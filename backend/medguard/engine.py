"""Compares medication sources and flags what a clinician should look at.

Everything here is a plain rule that can be read and tested. Nothing here is
sent to AI, and nothing here changes a medicine: it only raises questions. No
dose checks exist yet, because a safe dose depends on age, pregnancy and kidney
or liver function, and no validated rules for those have been supplied.

Findings carry their authority. Only a pharmacist-approved rule gives a clinical
alert. A rule nobody has reviewed is reported as a match with its content held
back, so the clinician knows to check, without it reading as a safety judgement.
List comparisons, such as a shared ingredient, need no rule, but they rely on the
unreviewed drug list, and they say so.
"""
import hashlib
import json
import re
from datetime import date
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Tuple

from backend.medguard import drugs
from backend.prescription.shorthand import PATTERN
from backend.safety import unvalidated_rules_visible

TIMES_A_DAY = {"OD": 1, "QD": 1, "HS": 1, "BD": 2, "BID": 2, "TDS": 3, "TID": 3, "QID": 4}
SOURCE_LABELS = {
    "prescription": "Prescription",
    "patient_reported": "Patient reported",
    "discharge_summary": "Discharge summary",
    "imported_record": "Imported record",
    "clinician_entered": "Clinician entry",
    "reconciled": "Last reconciled list",
}


def times_a_day(frequency: str) -> Optional[int]:
    """How often a medicine is taken, or None if it can't be said for certain."""
    pattern = PATTERN.search(frequency or "")
    if pattern:
        return sum(1 for amount in pattern.groups() if amount != "0")
    words = re.findall(r"[A-Za-z]+", (frequency or "").replace(".", "").upper())
    known = [TIMES_A_DAY[word] for word in words if word in TIMES_A_DAY]
    return known[0] if len(known) == 1 else None


def _strength(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


def _evidence(record: dict) -> dict:
    return {
        "medication_id": record["id"],
        "written_name": record["written_name"],
        "source": SOURCE_LABELS.get(record["source"], record["source"]),
        "strength": record["strength"],
        "frequency": record["frequency"],
    }


def _finding(kind: str, severity: str, title: str, explanation: str, records: Iterable[dict], **extra) -> dict:
    evidence = [_evidence(record) for record in records]
    identity = json.dumps([kind, extra.get("rule_id"), sorted(item["medication_id"] for item in evidence)])
    return {
        "alert_type": kind,
        "severity": severity,
        "title": title,
        "explanation": explanation,
        "evidence": evidence,
        "rule_id": extra.get("rule_id"),
        "authority": extra.get("authority", "list_comparison"),
        "fingerprint": hashlib.sha256(identity.encode()).hexdigest()[:32],
    }


def _ingredients(record: dict) -> List[str]:
    value = record["ingredients"]
    return json.loads(value) if isinstance(value, str) else value


def _matches(side: dict, ingredient: str) -> bool:
    if "ingredient" in side:
        return side["ingredient"] == ingredient
    return side["class"] in drugs.classes_of(ingredient)


def _unverified_finding(rule: dict, records: Tuple[dict, ...], subject: str) -> dict:
    """No severity, no claim: a reviewer hasn't said this rule is right."""
    detail = (
        f" Unreviewed text, for pilot testing only: {rule['summary']}. {rule['basis']}."
        if unvalidated_rules_visible()
        else " Its content is held back until a pharmacist has reviewed it."
    )
    return _finding(
        "unverified_rule_match",
        "info",
        f"Unverified rule matched: {subject}",
        f"Rule {rule['id']} has not been approved, so this isn't a clinical alert.{detail} "
        "Check an approved reference yourself.",
        records,
        rule_id=rule["id"],
        authority="unverified_rule",
    )


def interaction_findings(records: List[dict], rules: Optional[List[dict]] = None) -> List[dict]:
    found = {}
    interactions = [rule for rule in rules or [] if rule["category"] == "drug_interaction"]
    for first, second in combinations(records, 2):
        if set(_ingredients(first)) & set(_ingredients(second)):
            continue  # the same medicine in two lists, handled by the duplicate check
        for rule in interactions:
            for one, other in ((first, second), (second, first)):
                hit_a = any(_matches(rule["side_a"], ingredient) for ingredient in _ingredients(one))
                hit_b = any(_matches(rule["side_b"], ingredient) for ingredient in _ingredients(other))
                if not (hit_a and hit_b):
                    continue
                subject = f"{first['written_name']} and {second['written_name']}"
                if rule["status"] == "approved":
                    finding = _finding(
                        "interaction",
                        rule["severity"],
                        f"{subject}: {rule['summary'].lower()}",
                        f"A potential interaction, not a confirmed contraindication. {rule['basis']}. "
                        f"Approved rule {rule['id']}, reviewed by {rule['reviewer_name']} on {rule['last_reviewed_at'][:10]}. "
                        f"Evidence: {rule['evidence_source']}, {rule['evidence_version']}.",
                        (first, second),
                        rule_id=rule["id"],
                        authority="approved_rule",
                    )
                else:
                    finding = _unverified_finding(rule, (first, second), subject)
                found[finding["fingerprint"]] = finding
    return list(found.values())


def allergy_findings(records: List[dict], allergies: List[dict], rules: Optional[List[dict]] = None) -> List[dict]:
    knowledge = drugs.rules()
    cross_reactions = [rule for rule in rules or [] if rule["category"] == "allergy_cross_reaction"]
    findings = []
    for allergy in allergies:
        if allergy["status"] != "active":
            continue
        substance = allergy["substance"].strip().lower()
        allergy_class = knowledge["allergy_terms"].get(substance)
        allergen = drugs.known_ingredient(substance)
        allergen_classes = set(drugs.classes_of(allergen)) if allergen else set()
        for record in records:
            ingredients = _ingredients(record)
            classes = {name for ingredient in ingredients for name in drugs.classes_of(ingredient)}
            reaction = f" Recorded reaction: {allergy['reaction']}." if allergy["reaction"] else ""
            if allergen in ingredients or allergy_class in classes:
                findings.append(
                    _finding(
                        "allergy",
                        "high",
                        f"{record['written_name']} may conflict with a recorded {allergy['substance']} allergy",
                        f"Severity on record: {allergy['severity']}.{reaction} Please confirm against the allergy history. "
                        "Matched by name on a drug list that is still under review.",
                        [record],
                        rule_id=f"allergy:{allergy['id']}",
                    )
                )
                continue
            for cross in cross_reactions:
                drug_class, allergy_group = cross["side_b"]["drug_class"], cross["side_a"]["allergy_class"]
                if drug_class not in classes or allergy_group not in ({allergy_class} | allergen_classes):
                    continue
                if cross["status"] != "approved":
                    findings.append(_unverified_finding(cross, (record,), f"{record['written_name']} and a recorded {allergy['substance']} allergy"))
                    continue
                findings.append(
                    _finding(
                        "allergy_possible",
                        "moderate",
                        f"{record['written_name']}: possible cross-reaction with {allergy['substance']} allergy",
                        f"{cross['summary']}. An uncertain match.{reaction} Approved rule {cross['id']}.",
                        [record],
                        rule_id=f"{cross['id']}:{allergy['id']}",
                        authority="approved_rule",
                    )
                )
    return findings


def discrepancy_findings(records: List[dict]) -> List[dict]:
    findings: List[dict] = []
    by_ingredient: Dict[str, List[dict]] = {}
    for record in records:
        for ingredient in _ingredients(record):
            by_ingredient.setdefault(ingredient, []).append(record)

    for ingredient, group in by_ingredient.items():
        if len(group) < 2:
            continue
        products = {tuple(sorted(_ingredients(record))) for record in group}
        same_source = len({record["source"] for record in group}) < len(group)
        if len(products) > 1 or (same_source and len({drugs.clean_name(r["written_name"]) for r in group}) > 1):
            findings.append(
                _finding(
                    "duplicate_ingredient",
                    "high",
                    f"{ingredient.title()} appears in more than one medicine",
                    "These medicines share an active ingredient, which may mean a duplicate therapy. "
                    "Combination products often hide this.",
                    group,
                    rule_id=ingredient,
                )
            )
        for field, label in (("strength", "strength"), ("frequency", "schedule")):
            values = {}
            for record in group:
                if field == "strength":
                    value = _strength(record["strength"])
                else:
                    value = times_a_day(record["frequency"])
                if value:
                    values.setdefault(value, []).append(record)
            if len(values) > 1 and len(products) == 1:
                findings.append(
                    _finding(
                        f"{field}_conflict",
                        "moderate",
                        f"{ingredient.title()}: sources disagree on {label}",
                        "Check which is current. All sources are kept as written.",
                        [record for records_with_value in values.values() for record in records_with_value],
                        rule_id=f"{ingredient}:{field}",
                    )
                )

    active_classes: Dict[str, List[dict]] = {}
    for record in records:
        classes = {name for ingredient in _ingredients(record) for name in drugs.classes_of(ingredient)}
        for name in classes & {"nsaid", "ppi", "statin", "arb", "ace_inhibitor", "ssri", "sulfonylurea", "beta_blocker"}:
            active_classes.setdefault(name, []).append(record)
    for name, group in active_classes.items():
        if len({tuple(sorted(_ingredients(record))) for record in group}) > 1:
            shared = set.intersection(*(set(_ingredients(record)) for record in group))
            if not shared:
                findings.append(
                    _finding(
                        "same_class",
                        "moderate",
                        f"More than one medicine of the same type ({name.replace('_', ' ')})",
                        "Different ingredients with a similar action. This may be intended; please confirm.",
                        group,
                        rule_id=name,
                    )
                )
    return findings


def omission_findings(records: List[dict]) -> List[dict]:
    sources = {record["source"] for record in records if record["source"] != "reconciled"}
    if len(sources) < 2:
        return []
    findings = []
    for record in records:
        if record["source"] == "reconciled" or not _ingredients(record):
            continue
        ingredients = set(_ingredients(record))
        present_in = {other["source"] for other in records if ingredients & set(_ingredients(other))}
        missing = sources - present_in
        if missing:
            names = ", ".join(SOURCE_LABELS.get(source, source) for source in sorted(missing))
            findings.append(
                _finding(
                    "omission",
                    "moderate",
                    f"{record['written_name']} is missing from: {names}",
                    f"Listed in {SOURCE_LABELS.get(record['source'], record['source'])} only. Please confirm whether it is still taken.",
                    [record],
                    rule_id="omission",
                )
            )
    return findings


def stale_findings(records: List[dict], today: Optional[date] = None) -> List[dict]:
    today = today or date.today()
    return [
        _finding(
            "stale_entry",
            "moderate",
            f"{record['written_name']} is past its end date but still active",
            f"The record ended on {record['end_date']}.",
            [record],
            rule_id="stale",
        )
        for record in records
        if record["end_date"] and record["end_date"] < today.isoformat()
    ]


def unmatched_findings(records: List[dict]) -> List[dict]:
    return [
        _finding(
            "unmatched_drug",
            "high",
            f"Couldn't identify {record['written_name']}",
            "No interaction, allergy or duplicate checks ran for this medicine. Set its ingredients to include it.",
            [record],
            rule_id="unmatched",
        )
        for record in records
        if record["match_status"] == "unmatched"
    ]


def coverage(findings: List[dict], rules: List[dict], records: List[dict]) -> dict:
    """Says what was and wasn't checked. These words must never turn into reassurance."""
    approved_rules = sum(rule["status"] == "approved" for rule in rules)
    alerts = sum(finding["authority"] == "approved_rule" for finding in findings)
    unverified = sum(finding["authority"] == "unverified_rule" for finding in findings)
    unidentified = sum(record["match_status"] == "unmatched" and record["status"] == "active" for record in records)
    statements = []
    if alerts:
        statements.append({"state": "approved_rule_alert", "text": f"Possible issues raised by approved rules: {alerts}."})
    if unverified:
        statements.append(
            {
                "state": "unverified_rule_found",
                "text": f"Unverified rules matched: {unverified}. A pharmacist has not reviewed these, so they aren't clinical alerts.",
            }
        )
    if not approved_rules:
        statements.append(
            {
                "state": "no_approved_rules",
                "text": "No rule has been approved yet, so these medicines have not been checked against each other.",
            }
        )
    elif not alerts:
        statements.append(
            {
                "state": "no_approved_rule_matched",
                "text": "No approved rule matched. The approved set is limited, so this tells you nothing about whether this combination is suitable.",
            }
        )
    if unidentified:
        statements.append(
            {
                "state": "information_missing",
                "text": f"Medicines that couldn't be identified, so no rule checked them: {unidentified}.",
            }
        )
    return {
        "approved_rules_in_use": approved_rules,
        "approved_rule_alerts": alerts,
        "unverified_rule_matches": unverified,
        "unidentified_medicines": unidentified,
        "statements": statements,
    }


def review(records: List[dict], allergies: List[dict], rules: Optional[List[dict]] = None) -> List[dict]:
    """All findings for one patient's active medicines, most serious first."""
    active = [record for record in records if record["status"] == "active"]
    findings = (
        unmatched_findings(active)
        + allergy_findings(active, allergies, rules)
        + interaction_findings(active, rules)
        + discrepancy_findings(active)
        + omission_findings(active)
        + stale_findings(active)
    )
    order = {"high": 0, "moderate": 1, "info": 2}
    return sorted(findings, key=lambda finding: order[finding["severity"]])
