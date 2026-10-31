import json
from datetime import date

import pytest

from backend.amrguard import antibiogram
from backend.tests.conftest import verified_organization

BASE = "/api/addons/amrguard"
HEADER = "patient_ref,isolate_ref,specimen,organism,antibiotic,mic,zone_mm,interpretation,standard,standard_version,collected_on,laboratory"


def row(number, interpretation="S", organism="E. coli", antibiotic="CIP", patient=None, collected="2026-03-01", standard="CLSI", version="M100-Ed36"):
    return (
        f"P{patient if patient is not None else number},I{number}-{antibiotic},Urine,{organism},{antibiotic},0.25,,"
        f"{interpretation},{standard},{version},{collected},Central Lab"
    )


def csv_of(*rows):
    return "\n".join([HEADER, *rows]).encode()


def result(isolate, patient, interpretation="S", organism="Escherichia coli", antibiotic="CIP", collected="2026-03-01", standard="CLSI", version="M100"):
    return {
        "isolate_ref": isolate, "patient_ref": patient, "organism": organism, "antibiotic": antibiotic,
        "interpretation": interpretation, "collected_on": collected, "standard": standard,
        "standard_version": version, "laboratory": "Central Lab",
    }


def isolates(count, resistant=0, **fields):
    return [result(f"I{n}", f"P{n}", "R" if n < resistant else "S", **fields) for n in range(count)]


def test_only_the_first_isolate_per_patient_counts():
    results = [
        result("A", "P1", "S", collected="2026-01-01"),
        result("B", "P1", "R", collected="2026-02-01"),
        result("C", "P2", "S", collected="2026-01-05"),
    ]
    kept, dropped = antibiogram.first_isolates(results)
    assert {row["isolate_ref"] for row in kept} == {"A", "C"}
    assert dropped == 1


def test_a_repeat_of_another_organism_is_kept():
    kept, _ = antibiogram.first_isolates([result("A", "P1"), result("B", "P1", organism="Klebsiella pneumoniae")])
    assert len(kept) == 2


def test_percent_susceptible_counts_intermediate_as_not_susceptible():
    results = isolates(28) + [result("X", "PX", "I"), result("Y", "PY", "R")]
    entry = antibiogram.build(results)["table"][0]
    cell = entry["antibiotics"][0]
    assert cell["tested"] == 30
    assert cell["percent_susceptible"] == round(100 * 28 / 30, 1)


def test_too_few_isolates_give_no_percentage():
    built = antibiogram.build(isolates(29))
    assert built["table"][0]["reported"] is False
    assert built["table"][0]["antibiotics"] == []
    assert "below 30" in built["quality"]["warnings"][0]


def test_exactly_the_minimum_is_reported():
    assert antibiogram.build(isolates(30))["table"][0]["reported"] is True


def test_a_drug_tested_on_too_few_isolates_is_withheld():
    results = isolates(30) + isolates(5, antibiotic="MEM")
    cells = {cell["code"]: cell for cell in antibiogram.build(results)["table"][0]["antibiotics"]}
    assert cells["CIP"]["percent_susceptible"] == 100
    assert cells["MEM"]["percent_susceptible"] is None
    assert "fewer than 30" in cells["MEM"]["note"]


def test_mixed_standards_are_warned_about():
    results = isolates(30) + [result("Z", "PZ", version="M100-new")]
    assert any("more than one" in warning.lower() for warning in antibiogram.build(results)["quality"]["warnings"])


def test_output_never_contains_patient_references():
    assert "P1" not in json.dumps(antibiogram.build(isolates(40))["table"])
    assert "patient_ref" not in json.dumps(antibiogram.build(isolates(40)))


def test_trend_uses_the_same_minimum():
    results = isolates(30, collected="2026-02-01") + [
        result("Q2", "QP", collected="2026-05-01"),
    ]
    points = antibiogram.trend(results, "Escherichia coli", "CIP")
    assert points[0] == {"quarter": "2026-Q1", "tested": 30, "percent_susceptible": 100}
    assert points[1]["percent_susceptible"] is None


@pytest.mark.parametrize(
    "bad, problem",
    [
        ({"organism": "Unknownia bacterium"}, "Unknown organism"),
        ({"antibiotic": "ZZZ"}, "Unknown antibiotic"),
        ({"interpretation": "X"}, "Interpretation"),
        ({"standard": "MADEUP"}, "testing standard"),
        ({"standard_version": ""}, "standard_version is empty"),
        ({"collected_on": "12/03/2026"}, "collected_on"),
        ({"collected_on": "2099-01-01"}, "future"),
        ({"mic": "", "zone_mm": ""}, "MIC"),
    ],
)
def test_bad_rows_are_rejected_with_a_reason(bad, problem):
    good = dict(zip(HEADER.split(","), row(1).split(",")))
    parsed, problems = antibiogram.validate_row({**good, **bad}, today=date(2026, 10, 31))
    assert parsed is None
    assert any(problem in item for item in problems)


def test_aliases_become_full_organism_names():
    parsed, _ = antibiogram.validate_row(dict(zip(HEADER.split(","), row(1).split(","))))
    assert parsed["organism"] == "Escherichia coli"


def test_a_row_repeated_in_a_file_is_rejected():
    good, rejected, total = antibiogram.parse_csv(csv_of(row(1), row(1)))
    assert (len(good), len(rejected), total) == (1, 1, 2)


def test_missing_columns_are_named():
    with pytest.raises(ValueError, match="organism"):
        antibiogram.parse_csv(b"patient_ref,isolate_ref\nP1,I1\n")


def test_a_non_utf8_file_is_refused():
    with pytest.raises(ValueError, match="UTF-8"):
        antibiogram.parse_csv(HEADER.encode() + b"\n\xff\xfe")


@pytest.fixture
def hospital(person):
    admin = person("doctor", "Anil", "Shah")
    organization_id = verified_organization(admin, "City Hospital")
    steward = person("doctor", "Divya", "Rao")
    micro = person("doctor", "Omar", "Ali")
    admin.post(f"/api/organizations/{organization_id}/members", json={"email": steward.email, "role": "steward"})
    admin.post(f"/api/organizations/{organization_id}/members", json={"email": micro.email, "role": "microbiologist"})
    return organization_id, steward, micro


def upload(client, organization_id, data):
    return client.post(f"{BASE}/organizations/{organization_id}/imports", files={"file": ("results.csv", data, "text/csv")})


def many(count=35):
    return csv_of(*[row(number, "R" if number < 7 else "S") for number in range(count)])


def test_import_reports_rejected_rows_and_keeps_the_good_ones(hospital):
    organization_id, steward, _ = hospital
    body = upload(steward, organization_id, csv_of(row(1), row(2, interpretation="Maybe"))).json()
    assert body["accepted"] == 1
    assert body["rejected_count"] == 1
    assert body["rejected"][0]["line"] == 3


def test_importing_the_same_file_twice_adds_nothing(hospital):
    organization_id, steward, _ = hospital
    upload(steward, organization_id, csv_of(row(1)))
    assert upload(steward, organization_id, csv_of(row(1))).json()["accepted"] == 0


@pytest.mark.parametrize("role", ["patient", "doctor"])
def test_outsiders_are_refused(person, hospital, role):
    organization_id, _, _ = hospital
    outsider = person(role)
    assert upload(outsider, organization_id, csv_of(row(1))).status_code == 403
    assert outsider.get(f"{BASE}/organizations/{organization_id}/culture-queue").status_code == 403


def test_ordinary_staff_are_refused(person, hospital):
    organization_id, steward, _ = hospital
    nurse = person("doctor")
    admin_row = steward.get("/api/organizations", params={"mine": True}).json()[0]
    assert admin_row["my_role"] == "steward"
    assert upload(nurse, organization_id, csv_of(row(1))).status_code == 403


def test_a_second_person_must_approve_an_antibiogram(hospital):
    organization_id, steward, micro = hospital
    upload(steward, organization_id, many())
    run = steward.post(f"{BASE}/organizations/{organization_id}/antibiograms", json={"period_start": "2026-01-01", "period_end": "2026-12-31"}).json()
    assert run["status"] == "draft"
    assert run["output"][0]["antibiotics"][0]["percent_susceptible"] == 80
    assert steward.post(f"{BASE}/antibiograms/{run['id']}/approve").status_code == 403
    approved = micro.post(f"{BASE}/antibiograms/{run['id']}/approve").json()
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == micro.id


def test_the_minimum_cannot_be_lowered(hospital):
    organization_id, steward, _ = hospital
    response = steward.post(
        f"{BASE}/organizations/{organization_id}/antibiograms",
        json={"period_start": "2026-01-01", "period_end": "2026-12-31", "min_isolates": 5},
    )
    assert response.status_code == 422


def test_each_organization_sees_only_its_own_data(person, hospital):
    organization_id, steward, _ = hospital
    upload(steward, organization_id, many())
    other = person("doctor")
    theirs = verified_organization(other, "Other")
    response = other.post(f"{BASE}/organizations/{theirs}/antibiograms", json={"period_start": "2026-01-01", "period_end": "2026-12-31"})
    assert response.status_code == 422
    assert other.get(f"{BASE}/organizations/{organization_id}/antibiograms").status_code == 403


def test_culture_queue_moves_through_its_states(hospital):
    organization_id, steward, _ = hospital
    upload(steward, organization_id, csv_of(row(1)))
    culture = steward.get(f"{BASE}/organizations/{organization_id}/culture-queue").json()[0]
    assert culture["state"] == "positive"
    states = [steward.post(f"{BASE}/culture-reviews/{culture['id']}/advance").json()["state"] for _ in range(3)]
    assert states == ["reviewed", "acknowledged", "actioned"]
    assert steward.post(f"{BASE}/culture-reviews/{culture['id']}/advance").status_code == 409


def test_stewardship_reviews_record_a_persons_judgement(hospital):
    organization_id, steward, _ = hospital
    review = steward.post(
        f"{BASE}/organizations/{organization_id}/stewardship-reviews",
        json={"patient_ref": "P1", "intervention": "duration_review", "finding": "Day 9 of therapy", "recommendation": "Discuss duration"},
    ).json()
    assert review["reviewer_id"] == steward.id
    updated = steward.patch(f"{BASE}/stewardship-reviews/{review['id']}", json={"disposition": "accepted"}).json()
    assert updated["disposition"] == "accepted"


def test_guidelines_are_versioned_with_an_owner(hospital):
    organization_id, steward, _ = hospital
    steward.post(
        f"{BASE}/organizations/{organization_id}/guidelines",
        json={"title": "Empiric therapy guide", "issuer": "City Hospital", "version": "3", "effective_date": "2026-01-01", "clinical_owner": "Dr Rao"},
    )
    assert steward.get(f"{BASE}/organizations/{organization_id}/guidelines").json()[0]["clinical_owner"] == "Dr Rao"


def test_no_route_recommends_or_prescribes():
    from backend.amrguard.routes import router

    words = ("recommend", "prescribe", "suggest", "empiric", "dose", "chat")
    paths = " ".join(route.path.lower() for route in router.routes)
    assert not any(word in paths for word in words)


def test_the_code_makes_no_ai_calls():
    from pathlib import Path

    import backend.amrguard.routes as routes

    for path in Path(routes.__file__).parent.glob("*.py"):
        text = path.read_text()
        assert "backend.llm" not in text and "import llm" not in text
