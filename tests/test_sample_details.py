"""Physical sample count/mass are separate from names, batch sizes and bag labels."""
import pytest

from app.extraction import parse_act
from app.protocol_text import clean_product_name
from app.sample_details import first_seal_number, sample_weight_value


@pytest.mark.parametrize("source,expected", [
    ("№1 00004850; №2 00004849", "00004850"),
    ("№ 1: Н00004850 № 2: Н00004849", "00004850"),
    ("00004149", "00004149"), ("Н89941790", "89941790"),
    ("№00004149", "00004149"), ("№1", ""), ("", ""),
    ("№2 00004849", "00004849"), ("00004850 / 00004849", "00004850"),
])
def test_first_seal_contains_only_first_id_digits_and_keeps_zeroes(source, expected):
    assert first_seal_number(source) == expected
    assert first_seal_number(expected) == expected


@pytest.mark.parametrize("weight,count,name,expected", [
    ("4.725 кг", "9 (ж/б)", "Свинина 1/525 г.", "9 ж/б по 525 г (4,725 кг)."),
    ("3,900 кг", "12 ж/б", "Паштет 1/325 г.", "12 ж/б по 325 г (3,9 кг)."),
    ("", "9 ж/б", "Свинина 1/525 г.", "9 ж/б по 525 г (4,725 кг)."),
    ("4,725 кг", "9 ж/б", "Свинина тушкована", "9 ж/б по 525 г (4,725 кг)."),
    ("4,725 кг", "", "Свинина 1/525 г.", "4,725 кг."),
    ("5 кг", "9 ж/б", "Свинина 1/525 г.", "5 кг (9 ж/б)."),
    ("", "", "Свинина 1/525 г.", ""),
    ("9 ж/б по 525 г (4,725 кг).", "", "", "9 ж/б по 525 г (4,725 кг)."),
    ("4.725 кі", "9 (ж/б) 1", "Свинина 1/525 г.", "9 ж/б по 525 г (4,725 кг)."),
    ("3,575 кг", "1 (ж/бу", "Паштет 1/325 r.", "11 ж/б по 325 г (3,575 кг)."),
    ("3,575 кг", "1 ж/б", "Паштет 1/325 г.", "3,575 кг (1 ж/б)."),
])
def test_weight_statement_uses_only_explicit_sample_facts(weight, count, name, expected):
    assert sample_weight_value(weight, count, name) == expected


def test_act_weight_and_count_are_used_before_pack_weight_is_removed_from_name():
    parsed = parse_act('''АКТ №87-010-1-247
«20» жовтня 2026 року
№ | Найменування продукції | Загальна вага відібраного зразка | Кількість одиниць продукту у одному відібрано зразки | Маса партії, від якої відібрано зразок | Номер за каталогом | Сейф-пакет №
1 | Консерви «Свинина тушкована» 1/525 г. (12012) | 4.725 кг | 9 (ж/б) | 793.8 кг | 12012 | №1 00004850 №2 00004849
2 | Консерви «Паштет м’ясний» 1/325 г. (12008) | 3.9 кг | 12 (ж/б) | 1967.875 кг | 12008 | №1 00004852 №2 00004851
''')
    pork, pate = parsed["items"]
    assert pork["product_name"] == "Консерви «Свинина тушкована»"
    assert pork["sample_weight"] == "9 ж/б по 525 г (4,725 кг)."
    assert pork["batch_size"] == "793.8 кг"
    assert pork["seal_number"] == "00004850"
    assert pate["product_name"] == "Консерви «Паштет м’ясний»"
    assert pate["sample_weight"] == "12 ж/б по 325 г (3,9 кг)."
    assert pate["batch_size"] == "1967.875 кг"


def test_batch_weight_never_becomes_sample_weight():
    item = parse_act("Найменування продукції: Свинина 1/525 г\nРозмір партії: 793,8 кг")["items"][0]
    assert item["product_name"] == "Свинина"
    assert item["batch_size"] == "793,8 кг"
    assert item["sample_weight"] == ""


@pytest.mark.parametrize("name,expected", [
    ("Каша «5 злаків» 1/325 г", "Каша «5 злаків»"),
    ("Свинина 525 г", "Свинина"), ("Паштет 0,325 кг", "Паштет"),
    ("Джем (20 г)", "Джем"), ("Свинина маса нетто 525 г", "Свинина"),
    ("Сіль ДСТУ 3583:2015 (1 ґатунок)", "Сіль ДСТУ 3583:2015 (1 ґатунок)"),
    ("Продукт «500 г сили» 525 г", "Продукт «500 г сили»"),
    ("Свинина тушкована.", "Свинина тушкована."),
    ("Паштет 1/325 r.", "Паштет"),
])
def test_unit_pack_weight_is_removed_without_touching_product_identity(name, expected):
    assert clean_product_name(name, "") == expected
    assert clean_product_name(expected, "") == expected
