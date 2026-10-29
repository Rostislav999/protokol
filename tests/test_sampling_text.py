"""Sampling grammar and catalog deduplication retain actual act facts."""
import pytest

from app.extraction import parse_act
from app.protocol_text import (
    SAMPLING_ENDING, clean_product_name, normalize_sampling_description, sampling_description_from_act,
)


def test_user_example_uses_instrumental_ranks_and_actual_company_and_unit():
    text = """Представники військової частини А3466:
Капітан Олег ДАНИЛОВ;
Старший лейтенант Дмитро МЕЛЬНИЧУК;
У присутності уповноваженої особи ТОВ «Приклад»:
Представник ТОВ «Приклад» Інна ПРИКЛАД;
Зразки харчових продуктів відібрано за адресою м. Київ, вул. Прикладна, 1.
Час відбору 14:00.
"""
    actual = sampling_description_from_act("м. Київ, вул. Прикладна, 1.", text)
    assert actual == (
        "м. Київ, вул. Прикладна, 1, представниками військової частини А3466 капітаном О. ДАНИЛОВ "
        "та старшим лейтенантом Д. МЕЛЬНИЧУК, в присутності представників ТОВ «Приклад», " + SAMPLING_ENDING
    )
    assert normalize_sampling_description(actual) == actual


def test_actual_scan_preamble_uses_rank_instead_of_repeating_position():
    text = """Представники військової частини A0873:
Ветеринарний лікар відділення ветеринарної інспекції військової частини А0873 старший лейтенант Артем КУЗЬМИЧ;
У присутності уповноваженої особи ТОВ «Гарна Страва»:
Уповноважена особа ТОВ «Гарна Страва» Ірина САМОЙЛЕНКО;
Комірник ТОВ «Гарна Страва» Лариса ПЕРМЯКОВА.
Зразки харчових продуктів відібрано за адресою м. Самар, вулиця Гідності, 115/14А
(логістичний склад ТОВ «Гарна Страва»).
Час відбору з 12:00 год.

№ | Найменування продукції | Номер за каталогом | Виробник | Дата виготовлення | Розмір партії
1 | Консерви «Свинина тушкована» 1/525 г. (12012) | 12012 | ТОВ «Виробник» | 16.03.2026 | 793,8 кг
"""
    result = parse_act(text)
    sampling = result["common"]["sampling_description"]
    assert sampling.startswith('м. Самар, вулиця Гідності, 115/14А (логістичний склад ТОВ «Гарна Страва»)')
    assert "представниками військової частини А0873 старшим лейтенантом А. КУЗЬМИЧ" in sampling
    assert 'в присутності представників ТОВ «Гарна Страва»' in sampling
    assert "Представники:" not in sampling
    assert "Ветеринарний лікар відділення" not in sampling
    assert sampling.endswith(SAMPLING_ENDING)
    assert result["items"][0]["product_name"] == "Консерви «Свинина тушкована»"
    assert result["items"][0]["catalog_number"] == "12012"


@pytest.mark.parametrize("rank,expected", [
    ("капітан", "капітаном"), ("старший лейтенант", "старшим лейтенантом"),
    ("майор", "майором"), ("підполковник", "підполковником"), ("лейтенант", "лейтенантом"),
])
def test_known_ranks_change_case_without_changing_surname(rank, expected):
    actual = normalize_sampling_description("представники військової частини А1234 " + rank + " О. ПРИКЛАД")
    assert f"представниками військової частини А1234 {expected} О. ПРИКЛАД" in actual


def test_already_correct_manual_description_keeps_facts_and_single_ending():
    description = 'Склад №2, представниками військової частини А3466 капітаном О. ДАНИЛОВ, в присутності представників ТОВ «Назва»'
    duplicated = description + ", " + SAMPLING_ENDING + " " + SAMPLING_ENDING
    actual = normalize_sampling_description(duplicated)
    assert actual == description + ", " + SAMPLING_ENDING
    assert normalize_sampling_description(actual) == actual
    assert normalize_sampling_description("") == ""


def test_legacy_representative_label_is_replaced_without_losing_military_unit():
    old = ("м. Самар. Представники: Ветеринарний лікар військової частини А0873 "
           "старший лейтенант Артем КУЗЬМИЧ.")
    actual = normalize_sampling_description(old)
    assert "представниками військової частини А0873 старшим лейтенантом А. КУЗЬМИЧ" in actual
    assert "Представники:" not in actual
    assert actual.endswith(SAMPLING_ENDING)


def test_missing_representative_details_are_not_invented():
    actual = sampling_description_from_act("м. Самар, склад 1", "Постачальник: ТОВ «Назва»")
    assert actual == "м. Самар, склад 1, " + SAMPLING_ENDING
    assert "представниками" not in actual
    assert "присутності" not in actual


@pytest.mark.parametrize("name,catalog,expected", [
    ("Свинина 1/525 г. (12012)", "12012", "Свинина"),
    ("Свинина ( 12012 ).", "№ 12012.", "Свинина"),
    ("Джем № за каталогом 12012", "12012", "Джем"),
    ("Джем, номер за каталогом: 12012", "12012", "Джем"),
    ("Джем [12012]", "12012", "Джем"),
    ("Свинина 525 г. (12013)", "12012", "Свинина (12013)"),
    ("Сіль ДСТУ 3583:2015 (1 ґатунок)", "12012", "Сіль ДСТУ 3583:2015 (1 ґатунок)"),
    ("Джем урожаю 2026 року (525 г)", "12012", "Джем урожаю 2026 року"),
    ("Паштет (12012)", "", "Паштет (12012)"),
    ("Паштет 12012", "12012", "Паштет 12012"),
])
def test_catalog_suffix_only_removed_when_exactly_known(name, catalog, expected):
    assert clean_product_name(name, catalog) == expected
