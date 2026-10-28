import json
from pathlib import Path

import pytest

from app.assignment_matching import normalize_standard, rank_assignments, standards_match
from app.extraction import _header_field, parse_act

ROOT = Path(__file__).resolve().parents[1]


def entry(id, name, standard, department="vet", **values):
    return {"id": id, "name": id, "product_name": name, "standard": standard,
            "department": department, **values}


@pytest.mark.parametrize("left,right", [
    ("ДСТУ 4450:2005.", "ДСТУ4450:2005"),
    ("DSTU4450:2005", "ДСТУ 4450:2005"),
    ("GOST30425-97", "ГОСТ 30425-97"),
    ("ТУ У 10.1-35327262-011:2023.", "ТУУ10.1–35327262–011:2023"),
    ("ТУУ 10.1- 35327262- 011:2023", "ТУ У 10.1-35327262-011:2023"),
    ("TY Y10.2-32674729-001:2012", "ТУ У 10.2-32674729-001:2012"),
    ("GN 6.6.1.1-130-2006", "ГН6.6.1.1-130-2006"),
    ("ГОСТ 30425-97; ДСТУ 4450:2005", "ДСТУ 4450:2005, ГОСТ30425-97"),
])
def test_standard_spacing_dash_prefix_and_order_equivalence(left, right):
    assert standards_match(left, right)


@pytest.mark.parametrize("left,right", [
    ("ДСТУ 4450:2005", "ДСТУ 4449:2005"),
    ("ДСТУ 4450:2005", "ДСТУ 4450:2025"),
    ("ДСТУ 7352:2013", "ТУ У 10.1-35327262-011:2023"),
    ("ТУ У 10.2-32674729-001:2012", "ТУ У 10.2-38695524-001:2013"),
    ("ДСТУ 4450:2005 зі зміною №2", "ДСТУ 4450:2005"),
    ("ДСТУ 4450:2005", "4450"),
    ("", ""),
])
def test_standard_numbers_versions_and_unconfirmed_short_codes_remain_distinct(left, right):
    assert not standards_match(left, right)


def test_same_named_pate_with_different_normative_document_requires_review():
    templates = [
        entry("dstu", "Паштет м’ясний", "ДСТУ 7352:2013"),
        entry("tu", "Паштет м’ясний", "ТУ У 10.1-35327262-011:2023"),
        entry("liver", "Паштет печінковий", "ДСТУ 7050:2009"),
    ]
    result = rank_assignments("Паштет м’ясний", "ТУУ10.1-35327262-011:2023", templates)
    assert [m["id"] for m in result["departments"]["vet"]] == ["tu"]
    assert [m["id"] for m in result["review_departments"]["vet"]] == ["dstu"]
    assert result["review_departments"]["vet"][0]["requires_review"]
    assert not result["departments"]["bacteriology"]


def test_missing_standard_has_only_review_candidates_and_never_guess():
    result = rank_assignments("Свинина тушкована", "", [entry("x", "Свинина тушкована", "ДСТУ 4450:2005")])
    assert not result["departments"]["vet"]
    assert result["warnings"]
    assert result["review_departments"]["vet"][0]["standard_match"] is False


def test_same_standard_cannot_replace_meat_and_species_or_pate_organs():
    templates = [entry("beef", "Яловичина тушкована", "ДСТУ 4450:2005"),
                 entry("pate", "Паштет м’ясний", "ДСТУ 4450:2005")]
    result = rank_assignments("Свинина тушкована", "ДСТУ 4450:2005", templates)
    assert not any(result["departments"].values())
    assert not any(result["review_departments"].values())


def test_explicit_cereals_potatoes_scope_is_limited_to_that_ready_group():
    template = entry("group", "Картопля зі свининою (готова друга страва)", "ТУ У 10.8-44195795-003:2023",
                     product_scope="ready_second_cereals_potatoes")
    for name in ("Каша гречана з яловичиною (готова друга страва)", "Картопля з куркою (готова друга страва)"):
        assert rank_assignments(name, template["standard"], [template])["departments"]["vet"]
    for name in ("Квасоля з куркою (готова друга страва)", "Консерви м’ясорослинні. Каша гречана з яловичиною"):
        assert not rank_assignments(name, template["standard"], [template])["departments"]["vet"]


def test_bacteriology_user_approved_all_ready_second_scope_covers_legumes_grains_and_potatoes():
    template = entry("bak-group", "Квасоля з куркою (готова друга страва)", "ТУ У 10.8-44195795-003:2023",
                     department="bacteriology", product_scope="ready_second_all")
    for name in ("Каша гречана з яловичиною (готова друга страва)",
                 "Картопля з куркою (готова друга страва)", "Квасоля з куркою (готова друга страва)"):
        assert rank_assignments(name, template["standard"], [template])["departments"]["bacteriology"]
    assert not rank_assignments("Консерви м’ясорослинні. Каша гречана з яловичиною", template["standard"], [template])["departments"]["bacteriology"]


def test_first_course_and_jam_generic_scope_need_explicit_metadata():
    borscht = entry("borscht", "Борщ з яловичиною (готова перша страва)", "ТУ У 10.8-44195795-003:2023")
    assert not rank_assignments("Суп зі свининою (готова перша страва)", borscht["standard"], [borscht])["departments"]["vet"]
    borscht["product_scope"] = "first_course"
    assert rank_assignments("Суп зі свининою (готова перша страва)", borscht["standard"], [borscht])["departments"]["vet"]
    jam = entry("jam", "Джем яблучний", "ДСТУ 4900:2007", product_scope="jam")
    assert rank_assignments("Джем абрикосовий", jam["standard"], [jam])["departments"]["vet"]


def test_cod_liver_department_standards_are_not_false_equivalents():
    catalog = "Описи продуктів за Каталогом продуктів харчування, затвердженим наказом Міністерства оборони України від 15.11.2019 № 591."
    templates = [entry("vet", "Печінка тріски", catalog),
                 entry("chem", "Печінка тріски", catalog + " ГН 6.6.1.1-130-2006", department="chemistry"),
                 entry("bak", "Печінка тріски", "ГОСТ 30425-97", department="bacteriology")]
    result = rank_assignments("Печінка тріски", catalog, templates)
    assert [m["id"] for m in result["departments"]["vet"]] == ["vet"]
    assert not result["departments"]["chemistry"]
    assert not result["departments"]["bacteriology"]
    assert result["review_departments"]["chemistry"] and result["review_departments"]["bacteriology"]


def test_real_uploaded_catalog_60_forms_match_their_own_declared_product_and_standard():
    templates = json.loads((ROOT / "assets/assignments.json").read_text())
    assert len(templates) == 60
    for template in templates:
        result = rank_assignments(template["product_name"], template["standard"], [template])
        assert any(m["id"] == template["id"] for m in result["departments"][template["department"]]), template["filename"]


def test_actual_catalog_norm_document_filters_same_species_sprat_forms():
    templates = json.loads((ROOT / "assets/assignments.json").read_text())
    result = rank_assignments("Шпроти в олії", "ТУ У 10.2-32674729-001:2012", templates)
    for department in ("vet", "bacteriology", "chemistry"):
        strict = result["departments"][department]
        assert strict
        assert all("32674729" in m["standard"] for m in strict)
        assert all("38695524" not in m["standard"] for m in strict)


def test_act_conformance_standard_is_read_separately_from_test_methods():
    text = """№ | Найменування продукції | Підлягає дослідженню на відповідність | Метод, що використано при відбиранні зразка
1 | Паштет м’ясний | ТУ У 10.1-35327262-011:2023 | ДСТУ 8448:2015
2 | Свинина тушкована | ДСТУ 4450:2005 | ГОСТ 30425-97
"""
    items = parse_act(text)["items"]
    assert items[0]["standard"] == "ТУ У 10.1-35327262-011:2023"
    assert items[1]["standard"] == "ДСТУ 4450:2005"
    assert _header_field("Позначення НД на метод випробувань") is None
    assert _header_field("Підлягає\nдослідженню\nна вілповіл-\nність") == "standard"
    assert _header_field("Підлягає\nдослідженню за\nпоказниками") is None
    labeled = parse_act("Назва зразка: Кілька\nВипробування на відповідність НД: ТУ У 10.2-32427900-001:2015")
    assert labeled["items"][0]["standard"] == "ТУ У 10.2-32427900-001:2015"
