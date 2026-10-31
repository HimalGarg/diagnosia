import pytest

from backend.prescription import medicines
from backend.prescription.medicines import MedicineList, check_name, normalize

ROWS = [
    {"name": "Augmentin 625 Duo Tablet", "short_composition1": "Amoxycillin (500mg)", "short_composition2": " Clavulanic Acid (125mg)"},
    {"name": "Pantop-D Capsule", "short_composition1": "Domperidone (30mg)", "short_composition2": "Pantoprazole (40mg)"},
    {"name": "Dolo 650 Tablet", "short_composition1": "Paracetamol (650mg)", "short_composition2": ""},
]


@pytest.fixture
def medicine_list():
    return MedicineList.from_rows(ROWS)


@pytest.mark.parametrize(
    "written, expected",
    [
        ("Tab. Augmentin 625", "augmentin"),
        ("Augmentin 625 Duo Tablet", "augmentin duo"),
        ("Cap Pantop-D", "pantop d"),
        ("Syp. Crocin 120mg/5ml", "crocin"),
        ("Inj Amoxycillin (500mg)", "amoxycillin"),
    ],
)
def test_names_are_reduced_to_the_brand(written, expected):
    assert normalize(written) == expected


@pytest.mark.parametrize(
    "written, contains",
    [
        ("Augmentin 625 Duo", "Amoxycillin + Clavulanic Acid"),
        ("Tab Augmentin", "Amoxycillin + Clavulanic Acid"),
        ("pantop-d", "Domperidone + Pantoprazole"),
        ("Dolo 650", "Paracetamol"),
    ],
)
def test_known_brands_are_found_with_their_ingredients(medicine_list, written, contains):
    match = medicine_list.check(written)
    assert match.status == "found"
    assert match.contains == contains


def test_generic_names_are_found(medicine_list):
    assert medicine_list.check("Paracetamol").status == "found"


def test_a_misspelled_name_is_suggested_and_not_replaced(medicine_list):
    match = medicine_list.check("Augmentn")
    assert match.status == "close"
    assert match.name == "Augmentin"


@pytest.mark.parametrize("written", ["Xyzzol", "Zzzzzz 500", "", "Tab", "12-34"])
def test_unknown_names_are_not_found(medicine_list, written):
    assert medicine_list.check(written).status == "not_found"


def test_the_check_is_skipped_when_the_list_is_missing(tmp_path, monkeypatch):
    medicines._load.cache_clear()
    monkeypatch.setattr(medicines, "DATA_FILE", tmp_path / "missing.csv")
    assert check_name("Dolo 650") == {"status": "unchecked", "name": None, "contains": None}
    medicines._load.cache_clear()
