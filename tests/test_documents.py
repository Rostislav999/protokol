from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

import pytest

from app.documents import (
    FIELD_LABELS,
    MISSING_VALUE,
    STOP_PHRASE,
    TemplateError,
    inspect_template,
    render_protocol,
)

SAMPLES = sorted((Path(__file__).resolve().parents[1] / "assets" / "templates").glob("*.docx"))
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def word_xml(data):
    with ZipFile(BytesIO(data)) as archive:
        return archive.read("word/document.xml")


def text(data):
    root = ET.fromstring(word_xml(data))
    return "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", NS))


def docx(body):
    result = BytesIO()
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>'
           f'<w:document xmlns:w="{NS["w"]}"><w:body>{body}</w:body></w:document>').encode()
    with ZipFile(result, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr("word/styles.xml", b"original styles")
        archive.writestr("word/header1.xml", b"original header")
    return result.getvalue()


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda p: p.name)
def test_all_supplied_templates_fill_cover_and_preserve_complete_suffix_and_parts(sample):
    source = sample.read_bytes()
    info = inspect_template(source)
    assert set(info["available_fields"]) == set(FIELD_LABELS)
    assert info["product_name"]
    fields = {key: f"НОВЕ_ЗНАЧЕННЯ_{key}" for key in FIELD_LABELS}
    fields["protocol_date"] = "2026-10-28"
    output = render_protocol(source, fields)
    filled = inspect_template(output)["fields"]
    for key, value in fields.items():
        assert filled[key] == ("«28» жовтня 2026 року" if key == "protocol_date" else value)

    original_xml, updated_xml = word_xml(source), word_xml(output)
    anchor = "Вищезазначена".encode()
    assert original_xml[original_xml.index(anchor):] == updated_xml[updated_xml.index(anchor):]
    original_prefix = text(source).split(STOP_PHRASE)[0]
    updated_prefix = text(output).split(STOP_PHRASE)[0]
    assert info["fields"]["protocol_number"] not in updated_prefix
    # All three duplicate cover numbers are replaced, not only the title.
    assert updated_prefix.count(fields["protocol_number"]) == original_prefix.count(info["fields"]["protocol_number"])
    with ZipFile(BytesIO(source)) as original, ZipFile(BytesIO(output)) as updated:
        assert original.namelist() == updated.namelist()
        for name in original.namelist():
            if name != "word/document.xml":
                assert original.read(name) == updated.read(name)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda p: p.name)
def test_missing_act_data_never_reuses_customer_manufacturer_dates_or_other_sample_facts(sample):
    output = render_protocol(sample.read_bytes(), {})
    assert set(inspect_template(output)["fields"].values()) == {MISSING_VALUE}


def test_fixture_library_contains_all_twenty_two_user_templates():
    assert len(SAMPLES) == 22


def test_split_placeholders_preserve_original_runs_and_suffix_in_same_run():
    source = docx(
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>{{protocol_</w:t></w:r>'
        '<w:r><w:rPr><w:i/></w:rPr><w:t>number}}</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>{{product_name}} Вищезазначена інформація надана замовником. '
        'НЕДОТОРКАНИЙ ТЕКСТ &amp; &#x41;.</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{{manufacturer}} — старі результати</w:t>'
        '</w:r></w:p></w:tc></w:tr></w:tbl>'
    )
    output = render_protocol(source, {"protocol_number": "NEW-123", "product_name": "Сіль & цукор"})
    result = word_xml(output)
    assert "Сіль &amp; цукор".encode() in result
    assert b"<w:rPr><w:b/></w:rPr>" in result
    assert b"<w:rPr><w:i/></w:rPr>" in result
    assert b"{{manufacturer}}" in result
    anchor = "Вищезазначена".encode()
    assert word_xml(source).split(anchor)[1] == result.split(anchor)[1]
    assert "NEW-123" in text(output)


def test_placeholder_only_template_and_xml_escape_text():
    source = docx('<w:p><w:r><w:t>{{protocol_number}}</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>{{product_name}}</w:t></w:r></w:p>'
                  f'<w:p><w:r><w:t>{STOP_PHRASE}</w:t></w:r></w:p>')
    assert inspect_template(source)["product_name"] == ""
    output = render_protocol(source, {"product_name": '<Новий продукт> & "тест"', "protocol_number": "№123"})
    assert '<Новий продукт> & "тест"' in text(output)
    assert "123" in text(output)


@pytest.mark.parametrize("source", [b"not a zip", docx('<w:p><w:r><w:t>Без контрольної фрази</w:t></w:r></w:p>')])
def test_invalid_docx_and_absent_boundary_are_rejected(source):
    with pytest.raises(TemplateError):
        inspect_template(source)


def test_missing_product_and_number_fields_are_rejected():
    with pytest.raises(TemplateError, match="обов’язкові"):
        inspect_template(docx(f'<w:p><w:r><w:t>{STOP_PHRASE}</w:t></w:r></w:p>'))


def test_unknown_placeholder_is_rejected_instead_of_leaving_unfilled_template():
    source = docx('<w:p><w:r><w:t>{{protocol_number}} {{product_name}} {{wrong_key}}</w:t></w:r></w:p>'
                  f'<w:p><w:r><w:t>{STOP_PHRASE}</w:t></w:r></w:p>')
    with pytest.raises(TemplateError, match="Невідоме поле"):
        inspect_template(source)


def test_invalid_calendar_date_is_rejected():
    with pytest.raises(TemplateError, match="дата"):
        render_protocol(SAMPLES[0].read_bytes(), {"protocol_date": "2026-02-31"})


def test_revised_sample_814_does_not_invalidate_historical_reports_in_new_draft():
    sample = next(path for path in SAMPLES if path.name.startswith("814 "))
    source = sample.read_bytes()
    original = text(source).split(STOP_PHRASE)[0]
    assert "виданий на заміну" in original
    assert "недійсним" in original
    output = render_protocol(source, {"protocol_number": "НОВИЙ-814", "product_name": "Паштет новий"})
    updated = text(output).split(STOP_PHRASE)[0]
    assert updated.count("НОВИЙ-814") == 3
    assert "виданий на заміну" not in updated
    assert "недійсним" not in updated
    assert "87-012-0814-0814" not in updated
    # Clear only the text: keep the original paragraph/run structures intact.
    root_before = ET.fromstring(word_xml(source))
    root_after = ET.fromstring(word_xml(output))
    for tag in ("p", "r", "rPr", "tbl"):
        assert len(root_before.findall(f".//w:{tag}", NS)) == len(root_after.findall(f".//w:{tag}", NS))
    anchor = "Вищезазначена".encode()
    assert word_xml(source).split(anchor)[1] == word_xml(output).split(anchor)[1]
