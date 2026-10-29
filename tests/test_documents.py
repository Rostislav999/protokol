from io import BytesIO
from html import unescape
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import re
import xml.etree.ElementTree as ET

import pytest

from app.documents import (
    FIELD_LABELS,
    MISSING_VALUE,
    STOP_PHRASE,
    TemplateError,
    inspect_template,
    format_protocol_date,
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
        archive.writestr("word/header1.xml", f'<w:hdr xmlns:w="{NS["w"]}"><w:p><w:r><w:t>original header</w:t></w:r></w:p></w:hdr>'.encode())
    return result.getvalue()


def immutable_protocol_xml(xml):
    """Mask only text in the two user-authorized act/seal paragraphs."""
    def inspect_paragraph(match):
        raw = match.group()
        value = "".join(unescape(part.decode()) for part in re.findall(rb'<w:t(?:\s[^>]*)?>(.*?)</w:t>', raw, re.S))
        if re.match(r"\s*(?:Акт\s+відбору\s+зразків|Номер\s+пломби)\b", value):
            masked = re.sub(rb'(<w:t(?:\s[^>]*)?>).*?(</w:t>)', rb'\1\2', raw, flags=re.S)
            return re.sub(rb'\s+xml:space="preserve"', b'', masked)
        return raw
    return re.sub(rb'<w:p(?:\s[^>]*)?>.*?</w:p>', inspect_paragraph, xml, flags=re.S)


def immutable_number_part(xml):
    """Footer labels, page fields and layout must remain exactly the same."""
    def paragraph(match):
        raw = match.group()
        value = "".join(unescape(part.decode()) for part in re.findall(rb'<w:t(?:\s[^>]*)?>(.*?)</w:t>', raw, re.S))
        if value.startswith("Протокол випробувань №"):
            return re.sub(rb'(<w:t(?:\s[^>]*)?>).*?(</w:t>)', rb'\1\2', raw, flags=re.S)
        return raw
    return re.sub(rb'<w:p(?:\s[^>]*)?>.*?</w:p>', paragraph, xml, flags=re.S)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda p: p.name)
def test_all_supplied_templates_fill_cover_and_preserve_complete_suffix_and_parts(sample):
    source = sample.read_bytes()
    info = inspect_template(source)
    assert set(info["available_fields"]) == set(FIELD_LABELS)
    assert info["product_name"]
    fields = {key: f"НОВЕ_ЗНАЧЕННЯ_{key}" for key in FIELD_LABELS}
    fields["protocol_date"] = "2026-10-28"
    fields["act_date"] = "2026-10-20"
    output = render_protocol(source, fields)
    filled = inspect_template(output)["fields"]
    for key, value in fields.items():
        assert filled[key] == ("28 жовтня 2026 року" if key == "protocol_date" else
                               "20.10.2026" if key == "act_date" else value)

    original_xml, updated_xml = word_xml(source), word_xml(output)
    anchor = "Вищезазначена".encode()
    assert immutable_protocol_xml(original_xml)[original_xml.index(anchor):] == immutable_protocol_xml(updated_xml)[updated_xml.index(anchor):]
    original_prefix = text(source).split(STOP_PHRASE)[0]
    updated_prefix = text(output).split(STOP_PHRASE)[0]
    assert info["fields"]["protocol_number"] not in updated_prefix
    # All three duplicate cover numbers are replaced, not only the title.
    assert updated_prefix.count(fields["protocol_number"]) == original_prefix.count(info["fields"]["protocol_number"])
    with ZipFile(BytesIO(source)) as original, ZipFile(BytesIO(output)) as updated:
        assert original.namelist() == updated.namelist()
        for name in original.namelist():
            if re.fullmatch(r"word/footer\d+\.xml", name):
                assert immutable_number_part(original.read(name)) == immutable_number_part(updated.read(name))
                footer = ET.fromstring(updated.read(name))
                number_row = next("".join(p.itertext()) for p in footer.findall(".//w:p", NS)
                                  if "".join(p.itertext()).startswith("Протокол випробувань №"))
                assert number_row == "Протокол випробувань №" + fields["protocol_number"]
            elif name != "word/document.xml":
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
    before = immutable_protocol_xml(word_xml(source))
    after = immutable_protocol_xml(word_xml(output))
    assert before.split(anchor)[1] == after.split(anchor)[1]


@pytest.mark.parametrize("source,expected", [
    ("2026-10-29", "29 жовтня 2026 року"),
    ("29.10.2026", "29 жовтня 2026 року"),
    ("29/10/2026", "29 жовтня 2026 року"),
    ("«29» жовтня 2026 року", "29 жовтня 2026 року"),
    ("29 ЖОВТНЯ 2026 року.", "29 жовтня 2026 року"),
    ("09.01.2026", "9 січня 2026 року"),
    ("2028-02-29", "29 лютого 2028 року"),
])
def test_protocol_date_is_canonical_ukrainian_text(source, expected):
    assert format_protocol_date(source) == expected


@pytest.mark.parametrize("value", ["2026-02-29", "31.04.2026", "29/13/2026", "29 невідомого 2026 року", "2026-00-29"])
def test_all_protocol_date_input_formats_validate_calendar(value):
    with pytest.raises(TemplateError, match="дата протоколу"):
        format_protocol_date(value)


def with_parts(source, parts):
    output = BytesIO()
    with ZipFile(BytesIO(source)) as original, ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for entry in original.infolist():
            archive.writestr(entry, parts.get(entry.filename, original.read(entry.filename)))
        for name, data in parts.items():
            if name not in original.namelist():
                archive.writestr(name, data)
    return output.getvalue()


def test_headers_and_every_footer_number_are_updated_across_runs_without_page_field_changes():
    source = docx('<w:p><w:r><w:t>ПРОТОКОЛ ВИПРОБУВАНЬ №87-010-3000-1305-2</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>29.10.2026</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>{{product_name}}</w:t></w:r></w:p>'
                  f'<w:p><w:r><w:t>{STOP_PHRASE}</w:t></w:r></w:p>')
    footer_prefix = (f'<w:ftr xmlns:w="{NS["w"]}"><w:p><w:r><w:rPr><w:b/></w:rPr>'
                     '<w:t>Протокол випро</w:t></w:r><w:r><w:rPr><w:i/></w:rPr>'
                     '<w:t>бувань №87-010-0555-</w:t></w:r><w:r><w:t>0555-2</w:t></w:r></w:p>')
    footer_suffix = '<w:p><w:r><w:t>Видання 14, 07.04.2026, сторінка </w:t></w:r><w:fldSimple w:instr="PAGE"><w:r><w:t>3</w:t></w:r></w:fldSimple></w:p></w:ftr>'
    footer = (footer_prefix + footer_suffix).encode()
    header = (f'<w:hdr xmlns:w="{NS["w"]}"><w:p><w:r><w:t>Номер: {{{{protocol_</w:t></w:r>'
              '<w:r><w:t>number}}</w:t></w:r></w:p></w:hdr>').encode()
    source = with_parts(source, {"word/footer1.xml": footer, "word/footer2.xml": footer,
                                 "word/header1.xml": header, "word/header2.xml": header})
    output = render_protocol(source, {"protocol_number": "87-015-4000-1400-2", "product_name": "Новий продукт", "protocol_date": "29.10.2026"})
    with ZipFile(BytesIO(output)) as archive:
        for name in ("word/footer1.xml", "word/footer2.xml"):
            updated = archive.read(name)
            assert updated.endswith(footer_suffix.encode())
            assert '<w:rPr><w:b/></w:rPr>'.encode() in updated
            assert '<w:rPr><w:i/></w:rPr>'.encode() in updated
            paragraphs = ET.fromstring(updated).findall('.//w:p', NS)
            assert ''.join(paragraphs[0].itertext()) == 'Протокол випробувань №87-015-4000-1400-2'
        for name in ("word/header1.xml", "word/header2.xml"):
            assert ''.join(ET.fromstring(archive.read(name)).itertext()) == 'Номер: 87-015-4000-1400-2'


def test_only_existing_act_and_seal_rows_change_after_marker_and_leading_zeros_survive():
    source = docx('<w:p><w:r><w:t>{{protocol_number}} {{product_name}}</w:t></w:r></w:p>'
                  f'<w:p><w:r><w:t>{STOP_PHRASE}</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>Акт відбору зразків: №87-010-1-100 від 01.01.2020.</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>Номер пломби: Н00000001.</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>Результат 123; дата надходження 02.01.2020; {{manufacturer}}</w:t></w:r></w:p>')
    result = render_protocol(source, {"protocol_number": "NEW-123", "product_name": "Тест", "act_number": "№87-015-1-247",
                                     "act_date": "2026-10-20", "seal_number": "Н00004149"})
    assert 'Акт відбору зразків: №87-015-1-247 від 20.10.2026.' in text(result)
    assert 'Номер пломби: Н00004149.' in text(result)
    assert 'Результат 123; дата надходження 02.01.2020; {{manufacturer}}' in text(result)
    assert text(source).count('Акт відбору зразків:') == text(result).count('Акт відбору зразків:') == 1
    assert text(source).count('Номер пломби:') == text(result).count('Номер пломби:') == 1
    assert immutable_protocol_xml(word_xml(source)).split(STOP_PHRASE.encode())[1] == immutable_protocol_xml(word_xml(result)).split(STOP_PHRASE.encode())[1]


def test_optional_administrative_placeholders_after_marker_fill_only_requested_fields():
    source = docx('<w:p><w:r><w:t>{{protocol_number}} {{product_name}}</w:t></w:r></w:p>'
                  f'<w:p><w:r><w:t>{STOP_PHRASE}</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>{{act_number}} від {{act_date}}; {{seal_number}}; {{manufacturer}}</w:t></w:r></w:p>')
    result = render_protocol(source, {"protocol_number": "NEW", "product_name": "Тест", "act_number": "А-001",
                                     "act_date": "20.10.2026", "seal_number": "00001", "manufacturer": "Новий виробник"})
    assert 'А-001 від 20.10.2026; 00001; {{manufacturer}}' in text(result)


def test_administrative_placeholder_after_marker_in_same_paragraph_is_filled_without_touching_results():
    source = docx('<w:p><w:r><w:t>{{protocol_number}} {{product_name}}</w:t></w:r></w:p>'
                  f'<w:p><w:r><w:t>Вступ {STOP_PHRASE} Пломба {{{{seal_number}}}}; {{{{manufacturer}}}} &amp; &#x41;.</w:t></w:r></w:p>')
    result = render_protocol(source, {"protocol_number": "NEW", "product_name": "Тест", "seal_number": "00001", "manufacturer": "Не переносити"})
    assert f'Вступ {STOP_PHRASE} Пломба 00001; {{{{manufacturer}}}} &amp; &#x41;.'.encode() in word_xml(result)


def test_multiple_seal_identifiers_keep_their_numbered_labels_and_leading_zeros():
    sample = SAMPLES[0].read_bytes()
    seals = "№1 00004850; №2 00004849"
    result = render_protocol(sample, {"seal_number": seals})
    assert inspect_template(result)["fields"]["seal_number"] == seals
    assert "Номер пломби: " + seals in text(result)
