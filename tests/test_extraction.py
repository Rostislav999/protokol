import io
import zipfile

import pytest

from app.extraction import MAX_BYTES, extract_text, parse_act
from app.protocol_text import SAMPLING_ENDING


def test_labeled_items_keep_distinct_batch_facts_and_common_fields():
    text = """Замовник: ТОВ «Приклад»
Постачальник: ТОВ «Поставка»
Договір: №FP-123-26 від 01.01.2026
Місце відбору: м. Самар, вул. Прикладна, 1

Найменування продукції: Консерви «Свинина тушкована»
Ідентифікаційний номер: 87-010-100-1-2
Виробник: ТОВ «Виробник»
вул. Харчова, 2, Україна
Дата виготовлення: 16.03.2026
Розмір партії: 793,8 кг

Найменування продукції: Консерви «Свинина тушкована»
Ідентифікаційний номер: 87-010-100-2-2
Виробник: ТОВ «Інший виробник»
Дата виготовлення: 05.02.2026
Розмір партії: 794,8 кг
"""
    result = parse_act(text)
    assert result["common"]["customer"] == 'ТОВ «Приклад»'
    assert result["common"]["sampling_description"] == "м. Самар, вул. Прикладна, 1, " + SAMPLING_ENDING
    assert len(result["items"]) == 2
    first, second = result["items"]
    assert first["protocol_number"] == "87-010-100-1-2"
    assert "вул. Харчова" in first["manufacturer"]
    assert first["manufacture_date"] == "16.03.2026"
    assert second["batch_size"] == "794,8 кг"
    assert first["product_name"] == second["product_name"]


def test_table_with_repeated_headers_preserves_every_row():
    header = "№ | Найменування продукції | Ідентифікаційний номер | Номер за каталогом | Виробник | Дата виготовлення | Розмір партії"
    text = header + "\n1 | Свинина тушкована | | 12012 | Виробник А | 16.03.2026 | 793,8 кг\n"
    text += "2 | Свинина тушкована | | 12012 | Виробник А | 05.02.2026 | 793,8 кг\n\n" + header
    text += "\n3 | Яловичина тушкована | | 12013 | Виробник Б | 07.01.2026 | 7144,2 кг\n"
    result = parse_act(text)
    assert len(result["items"]) == 3
    assert result["items"][1]["manufacture_date"] == "05.02.2026"
    assert result["items"][2]["catalog_number"] == "12013"
    assert all(item["protocol_number"] == "" for item in result["items"])


def test_protocol_identification_line_and_pending_number():
    result = parse_act("Ідентифікаційний номер: 87-1-2\nНазва зразка: Кілька у томатному соусі\nВиробник: Приклад")
    assert result["items"][0]["protocol_number"] == "87-1-2"
    result = parse_act("Ідентифікаційний номер та назва зразка: №87-1-2 – Тунець консервований")
    assert result["items"][0]["product_name"] == "Тунець консервований"
    assert result["items"][0]["protocol_number"] == "87-1-2"


def test_narrative_act_metadata_without_guessing_customer_or_protocol_date():
    text = """АКТ №87-010-1-247
Відповідно до умов Державного контракту (договору)
№FP-11532-VDK-26 від 18.08.2026р, постачальник ТОВ «Поставка», код ЄДРПОУ 12345678, за кодом постачальника №010
проведено відбір зразків харчових продуктів.
Представники військової частини А0000:
Ветеринарний лікар Іван ПРИКЛАД;
У присутності уповноваженої особи.
Зразки харчових продуктів відібрано за адресою Дніпропетровська область,
м. Самар, вулиця Прикладна, 1 (логістичний склад ТОВ «Поставка»).
Час відбору зразків з 12:00 год.

№ | Найменування продукції | Виробник | Дата виготовлення | Маса партії
1 | Свинина тушкована | ТОВ «Виробник» | 16.03.2026 | 793,8 кг
"""
    result = parse_act(text)
    assert result["common"]["supplier"] == 'ТОВ «Поставка», код ЄДРПОУ 12345678'
    assert result["common"]["contract"] == "№FP-11532-VDK-26 від 18.08.2026"
    assert "вулиця Прикладна" in result["common"]["sampling_description"]
    assert "Іван ПРИКЛАД" in result["common"]["sampling_description"]
    assert result["common"]["customer"] == ""
    assert result["items"][0]["protocol_number"] == ""


def test_numbered_names_do_not_guess_quantities_as_identifiers():
    result = parse_act("1. Свинина тушкована\n2. Суп гречаний зі свининою")
    assert len(result["items"]) == 2
    assert result["items"][0]["protocol_number"] == ""
    assert result["items"][0]["batch_size"] == ""


def test_empty_and_unrecognized_act_never_invents_items():
    assert parse_act("")["items"] == []
    result = parse_act("АКТ №123\nПідпис комісії\n12.01.2026")
    assert result["items"] == []
    assert any("Не вдалося" in warning for warning in result["warnings"])


def test_text_file_and_rejected_uploads():
    result = extract_text("Цукор білий".encode(), "акт.txt")
    assert result["method"] == "text"
    assert result["text"] == "Цукор білий"
    for data, name in [(b"", "a.pdf"), (b"bad", "bad.pdf"), (b"bad", "script.exe"), (b"x" * (MAX_BYTES + 1), "large.txt")]:
        with pytest.raises(ValueError):
            extract_text(data, name)


def test_docx_tables_stay_tabular_and_bad_docx_errors():
    xml = b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Product</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Date</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", xml)
    assert extract_text(output.getvalue(), "act.docx")["text"] == "Product | Date"
    with pytest.raises(ValueError):
        extract_text(b"bad zip", "act.docx")


def test_native_pdf_text_extraction_does_not_require_ocr(monkeypatch):
    import fitz
    import app.extraction as extraction
    monkeypatch.setattr(extraction, "_ocr_languages", lambda: (_ for _ in ()).throw(AssertionError("OCR should not run")))
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "This native PDF contains enough text for reliable native extraction without OCR.")
    data = document.tobytes()
    document.close()
    result = extract_text(data, "native.pdf")
    assert result["method"] == "pdf-text"
    assert result["pages"] == 1
    assert "native extraction" in result["text"]


def test_ruled_table_detection_keeps_cells_and_continues_on_next_page(monkeypatch):
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw
    import app.extraction as extraction
    image = Image.new("RGB", (1050, 740), "white")
    drawing = ImageDraw.Draw(image)
    xs = [75, 98, 187, 323, 391, 441, 501, 569, 661, 729, 806, 899, 969, 1005]
    ys = [400, 476, 576, 676]
    for x in xs:
        drawing.line((x, ys[0], x, ys[-1]), fill="black", width=2)
    for y in ys:
        drawing.line((xs[0], y, xs[-1], y), fill="black", width=2)
    values = iter([
        "№", "Назва харчового продукту", "Підприємство виробник", "Дата виготовлення", "Вага зразка", "Кількість одиниць", "Маса партії", "Метод", "Температура", "Норматив", "Показники", "Документи", "Пакет",
        "Свинина тушкована (12012)", "Виробник: ТОВ А", "16.03.2026", "793.8 кі", "00001",
        "Яловичина тушкована (12013)", "Виробник: ТОВ Б", "07.01.2026", "7144.2 кг", "00002",
        "АКТ №1",
    ])
    monkeypatch.setattr(extraction, "_ocr_image", lambda *args, **kwargs: next(values))
    # High-DPI source must not break detection of thin table lines.
    result = extraction._ocr_table(image.resize((2100, 1480)), "ukr")
    assert result is not None
    assert result["rows"] == 2
    parsed = parse_act(result["text"])
    assert len(parsed["items"]) == 2
    assert parsed["items"][0]["manufacturer"] == "ТОВ А"
    assert parsed["items"][0]["batch_size"] == "793.8 кг"
    assert parsed["items"][1]["manufacture_date"] == "07.01.2026"
    assert parsed["items"][0]["seal_number"] == "00001"
    continuation_values = iter([
        "3", "Свинина тушкована (12012)", "ТОВ А", "11.02.2026", "4 кг", "9", "7938.0 кг", "Метод 1", "+15", "ДСТУ", "Органолептичні", "Декларація", "0001",
        "Паштет м'ясний", "ТОВ Б", "15.10.2026", "1967.875 кг", "00002",
        "Паштет печінковий", "ТОВ Б", "13.10.2026", "2000.05 кг", "00003",
        "",
    ])
    monkeypatch.setattr(extraction, "_ocr_image", lambda *args, **kwargs: next(continuation_values))
    continuation = extraction._ocr_table(image, "ukr", previous_header=result["header"])
    assert continuation is not None
    assert continuation["rows"] == 3
    continued_items = parse_act(continuation["text"])["items"]
    assert continued_items[0]["manufacture_date"] == "11.02.2026"
    assert continued_items[2]["batch_size"] == "2000.05 кг"
