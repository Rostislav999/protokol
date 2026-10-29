"""Act and physical seal IDs must remain distinct from protocol/catalog numbers."""
import pytest

from app.extraction import parse_act


@pytest.mark.parametrize("heading", ["АКТ № 87-010-1-247", "Акт No 87-010-1-247", "АКТ N 87-010-1-247"])
def test_act_number_and_act_date_are_read_only_from_act_heading(heading):
    text = heading + '''
відбору зразків продукції
«20» жовтня 2026 року
З метою постачання згідно контракту №FP-11532-VDK-26 від 18.08.2026.
Найменування продукції: Свинина тушкована
Ідентифікаційний номер: 87-010-3000-1305-2
Номер за каталогом: 12012
Номер пломби: 00004850
'''
    result = parse_act(text)
    assert result["common"]["act_number"] == "87-010-1-247"
    assert result["common"]["act_date"] == "2026-10-20"
    assert result["items"][0]["seal_number"] == "00004850"
    assert result["items"][0]["protocol_number"] == "87-010-3000-1305-2"
    assert result["items"][0]["catalog_number"] == "12012"


def test_missing_or_malformed_act_date_does_not_borrow_contract_date():
    for date in ("", '420" жовтня 2026 року', "31.02.2026"):
        result = parse_act('АКТ №87-010-1-247\n' + date + '\nЗ метою постачання за договором №FP-123-26 від 18.08.2026\n1. Свинина тушкована')
        assert result["common"]["act_number"] == "87-010-1-247"
        assert result["common"]["act_date"] == ""


def test_explicit_combined_act_label_separates_number_and_date():
    result = parse_act('Акт відбору зразків: №87-010-1-247 від 20.10.2026\nНайменування продукції: Свинина\nНомер пломби зразка: 00004850')
    assert result["common"]["act_number"] == "87-010-1-247"
    assert result["common"]["act_date"] == "2026-10-20"
    assert result["items"][0]["seal_number"] == "00004850"


def test_unlabelled_identifiers_and_numbers_do_not_become_act_or_seal_numbers():
    result = parse_act('Договір: №FP-123-26\nНайменування продукції: Свинина тушкована\nІдентифікаційний номер: 87-010-3000-1305-2\nНомер за каталогом: 12012\n00004850')
    assert result["common"]["act_number"] == ""
    assert result["common"]["seal_number"] == ""
    assert result["items"][0]["seal_number"] == ""


def test_shared_seal_is_separate_from_specific_item_seals():
    result = parse_act('''Номер пломби: 00010000
Найменування продукції: Свинина тушкована
Номер пломби: 00010001
Найменування продукції: Яловичина тушкована
Номер пломби: 00010002
''')
    assert result["common"]["seal_number"] == "00010000"
    assert [item["seal_number"] for item in result["items"]] == ["00010001", "00010002"]


def test_safe_package_columns_preserve_both_physical_ids_and_repeated_headers():
    header = "№ | Назва зразка | Номер за каталогом | Номер пломби зразка"
    result = parse_act(header + '\n1 | Свинина | 12012 | №1 00004850; №2 00004849\n' + header + '\n2 | Яловичина | 12013 | 00004852')
    assert len(result["items"]) == 2
    assert result["items"][0]["seal_number"] == "№1 00004850; №2 00004849"
    assert result["items"][1]["seal_number"] == "00004852"
    assert all(item["protocol_number"] == "" for item in result["items"])


def test_rotated_safe_package_ocr_is_retained_on_continuation_pages(monkeypatch):
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw
    import app.extraction as extraction

    image = Image.new("RGB", (1050, 740), "white")
    drawing = ImageDraw.Draw(image)
    xs = [75, 100, 300, 500, 700, 900, 950]
    ys = [400, 480, 580, 680]
    for x in xs:
        drawing.line((x, ys[0], x, ys[-1]), fill="black", width=2)
    for y in ys:
        drawing.line((xs[0], y, xs[-1], y), fill="black", width=2)
    values = iter([
        "№", "Назва харчового продукту", "Виробник", "Дата виготовлення", "Розмір партії", "vertical header",
        "Сейф-пакет №",
        "Свинина", "ТОВ А", "16.03.2026", "10 кг", "№1 00004850 №2 00004849",
        "Яловичина", "ТОВ Б", "17.03.2026", "20 кг", "№1 00004852 №2 00004851",
        "АКТ №87-010-1-247",
    ])
    monkeypatch.setattr(extraction, "_ocr_image", lambda *args, **kwargs: next(values))
    first = extraction._ocr_table(image, "ukr")
    assert first is not None
    assert first["header"]["rotations"] == {5: 90}
    assert parse_act(first["text"])["items"][0]["seal_number"] == "№1 00004850 №2 00004849"
    following = iter([
        "3", "Свинина", "ТОВ А", "18.03.2026", "30 кг", "rotated body",
        "№1 00004854 №2 00004853",
        "Яловичина", "ТОВ Б", "19.03.2026", "40 кг", "№1 00004856 №2 00004855",
        "Свинина", "ТОВ А", "20.03.2026", "50 кг", "№1 00004858 №2 00004857",
        "",
    ])
    monkeypatch.setattr(extraction, "_ocr_image", lambda *args, **kwargs: next(following))
    continued = extraction._ocr_table(image, "ukr", previous_header=first["header"])
    assert continued is not None
    items = parse_act(continued["text"])["items"]
    assert len(items) == 3
    assert [item["seal_number"] for item in items] == ["№1 00004854 №2 00004853", "№1 00004856 №2 00004855", "№1 00004858 №2 00004857"]


def test_targeted_date_recovery_requires_independently_recognized_same_month_and_year(monkeypatch):
    from PIL import Image
    import app.extraction as extraction
    image = Image.new("RGB", (1000, 500), "white")
    header = 'АКТ №87-010-1-247\n420" жовтня 2026 року\nЗ метою постачання за договором від 18.08.2026'
    def recognize(crop, language, psm):
        assert crop.size == (700, 100)
        assert psm == 6
        return '“20” жовтня 2026 року\nДоговір від 18.08.2026'
    monkeypatch.setattr(extraction, "_ocr_image", recognize)
    assert extraction._recover_act_date(image, header, "ukr") == "2026-10-20"


@pytest.mark.parametrize("recognized", [
    "Договір від 20 жовтня 2026 року", "18.08.2026", "20 жовтня 2025 року", "20 серпня 2026 року",
    "20 жовтня 2026 року\n21 жовтня 2026 року", "31 лютого 2026 року", "420 жовтня 2026 року",
])
def test_targeted_date_recovery_rejects_contract_dates_and_ambiguous_or_incompatible_results(monkeypatch, recognized):
    from PIL import Image
    import app.extraction as extraction
    monkeypatch.setattr(extraction, "_ocr_image", lambda *a, **k: recognized)
    header = 'АКТ №87-010-1-247\n420" жовтня 2026 року\nЗ метою постачання'
    assert extraction._recover_act_date(Image.new("RGB", (1000, 500)), header, "ukr") == ""
