"""Preserve the original Excel workflow while leaving compliance columns alone."""
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree as ET
import pytest

from app.protocol_excel import (
    FIELDS, NS, R, S, Record, cell_text, extract_text, make_template,
    read_docx, report_text, validate, write_excel, write_excel_bytes, xml,
)


TEXT = '''ПРОТОКОЛ ВИПРОБУВАНЬ №87-015-3000-1305-2
29 жовтня 2026 року
Замовник: ТОВ «Замовник»
Номер за Каталогом продуктів харчування: 12012.
Ідентифікаційний номер та назва зразка: №87-015-3000-1305-2 – Свинина тушкована.
Постачальник: ТОВ «Постачальник», код ЄДРПОУ: 00123456.
Виробник: ТОВ «Виробник», вул. Прикладна, 1.
Код ЄДРПОУ: 12345678.
Договір: №123.
Дата виготовлення: 19.09.2026.
Розмір партії, від якої відібрано зразок: 1 234,56 кг.
Вага зразка: 9 ж/б по 525 г (4,725 кг).
Висновок: Представлений зразок: №87-015-3000-1305-2, відповідає ДСТУ.
Відповідність описам Каталогу: Не відповідає.
'''


def uploaded_template(path, end=88, date1904=False, compliance=True):
    root = ET.Element(f"{{{S}}}worksheet", nsmap={None: S, "r": R, "x14": "urn:test:x14"})
    ET.SubElement(root, f"{{{S}}}dimension", ref="A1:Q56")
    data = ET.SubElement(root, f"{{{S}}}sheetData")
    header = ET.SubElement(data, f"{{{S}}}row", r="1")
    fields = FIELDS + ([("O", "Результат випробувань"), ("P", "Відповідність описам Каталогу")] if compliance else [])
    for column, value in [("A", "Службові дані")] + fields:
        cell = ET.SubElement(header, f"{{{S}}}c", r=column + "1", t="inlineStr")
        ET.SubElement(ET.SubElement(cell, f"{{{S}}}is"), f"{{{S}}}t").text = value
    row = ET.SubElement(data, f"{{{S}}}row", r="2")
    for column, value in [("A", "service-id"), ("B", "checksum"), ("C", "service-date"), ("D", "№old"),
                          *(([("O", "Не відповідає"), ("P", "Відповідає")]) if compliance else [])]:
        cell = ET.SubElement(row, f"{{{S}}}c", r=column + "2", t="inlineStr", s="0")
        ET.SubElement(ET.SubElement(cell, f"{{{S}}}is"), f"{{{S}}}t").text = value
    ET.SubElement(row, f"{{{S}}}c", r="E2", s="1")
    empty = ET.SubElement(data, f"{{{S}}}row", r="3", ht="25")
    ET.SubElement(empty, f"{{{S}}}c", r="G3", s="0")
    ext = ET.SubElement(root, f"{{{S}}}extLst")
    ET.SubElement(ext, "{urn:test:x14}dataValidation", sqref="O2:P1048576").text = "hiddenSheet!A2:B2"
    table_parts = ET.SubElement(root, f"{{{S}}}tableParts", count="1")
    ET.SubElement(table_parts, f"{{{S}}}tablePart", {f"{{{R}}}id": "rTable"})
    last = "P" if compliance else "N"
    parts = {
        "xl/workbook.xml": f'<workbook xmlns="{S}" xmlns:r="{R}"><workbookPr date1904="{int(date1904)}"/><sheets><sheet name="Шаблон" sheetId="1" r:id="rSheet"/></sheets></workbook>'.encode(),
        "xl/_rels/workbook.xml.rels": b'<Relationships><Relationship Id="rSheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": ET.tostring(root),
        "xl/worksheets/_rels/sheet1.xml.rels": b'<Relationships><Relationship Id="rTable" Target="../tables/table1.xml"/></Relationships>',
        "xl/tables/table1.xml": f'<table xmlns="{S}" ref="A1:{last}{end}"><autoFilter ref="A1:{last}{end}"/></table>'.encode(),
        "xl/worksheets/sheet2.xml": b'<hidden>unchanged metadata</hidden>',
        "xl/styles.xml": b'<styles>unchanged styles</styles>',
        "customXml/item1.xml": b'<extra>unchanged application metadata</extra>',
    }
    with ZipFile(path, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)


def test_exact_old_schema_d_to_n_excludes_both_compliance_columns():
    record = extract_text(TEXT, "protocol.docx")
    assert [column for column, _ in FIELDS] == list("DEFGHIJKLMN")
    assert set(record.values) == set("DEFGHIJKLMN")
    assert record.values["D"] == "№87-015-3000-1305-2"
    assert record.values["E"] == "29.10.2026"
    assert record.values["G"] == "Свинина тушкована"
    assert record.values["H"] == record.values["I"] == "19.09.2026"
    assert record.values["K"] == "1234.56"
    assert record.values["L"] == "0.525"
    assert record.values["M"] == "ТОВ «Постачальник», код ЄДРПОУ: 00123456"
    assert record.values["N"] == "12345678"
    assert not any("Каталогу не указано" in value for value in record.warnings)
    assert validate(record)["E"] == date(2026, 10, 29)


def test_supplier_name_is_retained_if_code_missing_and_labeled_fop_code_supported():
    record = extract_text(TEXT.replace("код ЄДРПОУ: 00123456.", "телефон 0123456789."))
    assert record.values["M"] == "ТОВ «Постачальник», телефон 0123456789."
    assert any("Постачальник" in warning for warning in record.warnings)
    record = extract_text(TEXT.replace("00123456", "0012345678"))
    assert record.values["M"] == "ТОВ «Постачальник», код ЄДРПОУ: 0012345678"


def test_missing_markers_do_not_export_historical_or_placeholder_supplier_values():
    record = extract_text(TEXT.replace('ТОВ «Постачальник», код ЄДРПОУ: 00123456.', '[НЕ ЗАПОВНЕНО].'))
    assert record.values["M"] == ""


def test_expiry_labeled_inside_manufacturing_line_does_not_become_manufacturing_date():
    record = extract_text(TEXT.replace("19.09.2026.", "вжити до: 25.07.2028."))
    assert record.values["I"] == record.values["H"] == ""
    assert record.values["J"] == "25.07.2028"


@pytest.mark.parametrize("value,column", [("31.02.2026", "E"), ("nan", "K"), ("-1", "L"), ("компанія", "N")])
def test_invalid_dates_masses_and_manufacturer_codes_are_rejected(value, column):
    record = extract_text(TEXT)
    record.values[column] = value
    with pytest.raises(ValueError):
        validate(record)


def test_extra_compliance_values_are_never_validated_or_written(tmp_path):
    record = extract_text(TEXT)
    record.values.update(O="невірне значення", P="=HYPERLINK(\"https://example.com\")")
    assert not {"O", "P"} & validate(record).keys()
    source, target = tmp_path / "source.xlsx", tmp_path / "result.xlsx"
    uploaded_template(source)
    report = write_excel(source, target, [record])
    assert not {"O", "P"} & report["added"][0]["values"].keys()
    assert "Відповідність описам Каталогу" not in report_text(report)
    with ZipFile(target) as archive:
        root = xml(archive.read("xl/worksheets/sheet1.xml"))
        assert root.find(".//s:c[@r='O3']", NS) is None
        assert root.find(".//s:c[@r='P3']", NS) is None


def test_update_preserves_existing_compliance_abc_hidden_and_metadata_parts(tmp_path):
    source, target = tmp_path / "source.xlsx", tmp_path / "result.xlsx"
    uploaded_template(source)
    original_bytes = source.read_bytes()
    record = extract_text(TEXT)
    record.values["D"] = "№old"
    report = write_excel(source, target, [record], on_duplicate="update")
    assert report["updated"][0]["row"] == 2
    assert source.read_bytes() == original_bytes
    with ZipFile(source) as before, ZipFile(target) as after:
        old, new = xml(before.read("xl/worksheets/sheet1.xml")), xml(after.read("xl/worksheets/sheet1.xml"))
        for column in "ABCOP":
            xpath = f".//s:c[@r='{column}2']"
            assert ET.tostring(old.find(xpath, NS)) == ET.tostring(new.find(xpath, NS))
        assert ET.tostring(old.find("s:extLst", NS)) == ET.tostring(new.find("s:extLst", NS))
        for name in before.namelist():
            if name != "xl/worksheets/sheet1.xml":
                assert before.read(name) == after.read(name), name


@pytest.mark.parametrize("compliance,last", [(True, "P"), (False, "N")])
def test_append_expands_tables_with_original_column_boundary(tmp_path, compliance, last):
    source, target = tmp_path / "source.xlsx", tmp_path / "result.xlsx"
    uploaded_template(source, end=2, compliance=compliance)
    report = write_excel(source, target, [extract_text(TEXT)])
    assert report["added"][0]["row"] == 3
    with ZipFile(target) as archive:
        table = xml(archive.read("xl/tables/table1.xml"))
        assert table.get("ref") == f"A1:{last}3"
        assert table.find("s:autoFilter", NS).get("ref") == f"A1:{last}3"


def test_new_workbook_ends_n_dates_and_numbers_are_typed_and_text_is_not_formula():
    record = extract_text(TEXT)
    record.values["G"] = '=HYPERLINK("https://example.com")'
    data, report = write_excel_bytes(None, [record])
    assert report["added"][0]["row"] == 2
    with ZipFile(BytesIO(data)) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        root = xml(archive.read("xl/worksheets/sheet1.xml"))
        assert root.find("s:dimension", NS).get("ref") == "A1:N2"
        assert not root.findall(".//s:f", NS)
        assert cell_text(root.find(".//s:c[@r='G2']", NS), []) == record.values["G"]
        assert root.find(".//s:c[@r='G2']", NS).get("t") == "inlineStr"
        assert cell_text(root.find(".//s:c[@r='L2']", NS), []) == "0.525"
        assert root.find(".//s:c[@r='E2']", NS).get("s") == "1"
        assert cell_text(root.find(".//s:c[@r='E2']", NS), []) == str((date(2026, 10, 29)-date(1899, 12, 30)).days)
        assert cell_text(root.find(".//s:c[@r='M2']", NS), []) == "00123456"
        assert root.find(".//s:c[@r='O1']", NS) is None
        assert root.find(".//s:c[@r='P1']", NS) is None


def test_1904_dates_duplicate_skip_update_and_unsourced_retention(tmp_path):
    source, target, again = tmp_path / "source.xlsx", tmp_path / "result.xlsx", tmp_path / "again.xlsx"
    uploaded_template(source, date1904=True)
    record = extract_text(TEXT)
    write_excel(source, target, [record])
    with ZipFile(target) as archive:
        root = xml(archive.read("xl/worksheets/sheet1.xml"))
        assert cell_text(root.find(".//s:c[@r='E3']", NS), []) == str((date(2026, 10, 29)-date(1904, 1, 1)).days)
    with pytest.raises(ValueError, match="Все номера"):
        write_excel(target, again, [record])
    record.values["M"] = ""
    report = write_excel(target, again, [record], on_duplicate="update")
    assert report["updated"][0]["retained"] == ["M"]


def test_duplicate_inputs_and_existing_output_are_rejected(tmp_path):
    source, target = tmp_path / "source.xlsx", tmp_path / "result.xlsx"
    make_template(source)
    record = extract_text(TEXT)
    with pytest.raises(ValueError, match="повторяются"):
        write_excel(source, target, [record, record])
    target.write_bytes(b"existing user file")
    with pytest.raises(ValueError):
        write_excel(source, target, [record])
    assert target.read_bytes() == b"existing user file"


@pytest.mark.parametrize("data", [b"not a ZIP", b"", b"PK broken"])
def test_invalid_uploaded_archives_are_rejected(data):
    with pytest.raises(ValueError):
        read_docx(data)
    with pytest.raises(ValueError):
        write_excel_bytes(data, [Record("test", {"D": "№1"})])


def test_xml_external_entities_and_wrong_document_types_are_rejected():
    with pytest.raises(ValueError):
        xml(b'<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><a>&secret;</a>')
    with pytest.raises(ValueError):
        extract_text("АКТ №87-010-1-247\n1. Свинина")


SAMPLES = sorted((Path(__file__).resolve().parents[1] / "assets/templates").glob("*.docx"))


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda path: path.name)
def test_original_protocols_use_body_title_and_preserve_supplier_name(sample):
    record = read_docx(sample.read_bytes(), sample.name)
    assert record.source == sample.name
    assert record.values["D"].startswith("№87-")
    assert record.values["G"]
    assert len(record.values["M"]) > 8
    assert not {"O", "P"} & record.values.keys()


def test_colored_generated_protocol_extracts_net_mass_from_weight_not_total():
    from app.documents import render_protocol
    source = next(path for path in SAMPLES if path.name.startswith("1053 ")).read_bytes()
    generated = render_protocol(source, {
        "protocol_number": "87-010-3000-1400-2", "protocol_date": "2026-10-29",
        "product_name": "Свинина тушкована", "supplier": "ТОВ «Тест», код ЄДРПОУ: 00123456",
        "manufacture_date": "19.09.2026", "batch_size": "1234,56 кг",
        "sample_weight": "9 ж/б по 525 г (4,725 кг).",
    })
    record = read_docx(generated, "generated.docx")
    assert record.values["D"] == "№87-010-3000-1400-2"
    assert record.values["G"] == "Свинина тушкована"
    assert record.values["L"] == "0.525"
    assert record.values["M"] == "ТОВ «Тест», код ЄДРПОУ: 00123456"


@pytest.mark.parametrize("supplier,expected", [
    ("ТОВ «Тест», код ЄДРПОУ: 00123456", "00123456"),
    ("00001234", "00001234"),
    ("код ЄДРПОУ: 0012345678", "0012345678"),
    ("ТОВ «Тест», телефон 0123456789.", ""),
    ("код ЄДРПОУ: 00123456; код ЄДРПОУ: 87654321", ""),
])
def test_supplier_export_contains_only_code_as_text(supplier, expected):
    record = extract_text(TEXT)
    record.values["M"] = supplier
    data, _ = write_excel_bytes(None, [record])
    with ZipFile(BytesIO(data)) as archive:
        root = xml(archive.read("xl/worksheets/sheet1.xml"))
        cell = root.find(".//s:c[@r='M2']", NS)
        assert cell_text(cell, []) == expected
        if expected:
            assert cell.get("t") == "inlineStr"
