from xml.etree import ElementTree as ET

import pytest

from app.document_review import ReviewColorError, color_review_xml

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def word(body):
    return (f'<w:document xmlns:w="{W}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14">'
            '<w:body><w:p>' + body + '</w:p></w:body></w:document>').encode()


def runs(xml):
    return [("".join(run.find("w:t", NS).itertext()) if run.find("w:t", NS) is not None else "",
             run.find("w:rPr/w:color", NS).get("{" + W + "}val"), run)
            for run in ET.fromstring(xml).findall(".//w:r", NS)]


def test_mixed_label_and_filled_value_get_separate_colors_without_losing_font():
    source = word('<w:r w:rsidR="00112233"><w:rPr><w:rFonts w:ascii="Times New Roman"/>'
                  '<w:b/><w:color w:val="123456" w:themeColor="accent1" w:themeTint="AA"/>'
                  '<w:sz w:val="24"/></w:rPr><w:t>Замовник: ТОВ Тест</w:t></w:r>')
    output = color_review_xml(source, {0: [(10, 18)]})
    values = runs(output)
    assert [(text, color) for text, color, _ in values] == [("Замовник: ", "FF0000"), ("ТОВ Тест", "000000")]
    for _, _, run in values:
        assert run.find("w:rPr/w:rFonts", NS).get("{" + W + "}ascii") == "Times New Roman"
        assert run.find("w:rPr/w:b", NS) is not None
        assert run.find("w:rPr/w:sz", NS).get("{" + W + "}val") == "24"
        assert run.get("{" + W + "}rsidR") == "00112233"
    assert b'themeColor' not in output and b'themeTint' not in output
    assert b'mc:Ignorable="w14"' in output
    assert b'xmlns:w14=' in output


def test_multiple_texts_field_codes_tabs_and_page_counters_remain_in_order():
    field = b'<w:fldChar w:fldCharType="begin"/><w:instrText xml:space="preserve"> PAGE </w:instrText><w:fldChar w:fldCharType="end"/>'
    source = word('<w:r><w:rPr><w:i/></w:rPr><w:t>До </w:t><w:tab/><w:t>НОВЕ після</w:t>' + field.decode() + '</w:r>')
    output = color_review_xml(source, {1: [(0, 4)]})
    assert field in output
    assert output.count(b'<w:tab/>') == 1
    assert [(text, color) for text, color, _ in runs(output) if text] == [
        ("До ", "FF0000"), ("НОВЕ", "000000"), (" після", "FF0000")]
    assert "".join(ET.fromstring(output).itertext()) == "".join(ET.fromstring(source).itertext())


def test_escaped_text_and_character_entities_retain_original_lexical_form():
    source = word('<w:r><w:t>Сіль &amp; цукор &#x41; НОВЕ</w:t></w:r>')
    text = "Сіль & цукор A НОВЕ"
    output = color_review_xml(source, {0: [(len(text)-4, len(text))]})
    assert b'&amp;' in output and b'&#x41;' in output
    assert [(value, color) for value, color, _ in runs(output)] == [("Сіль & цукор A ", "FF0000"), ("НОВЕ", "000000")]


def test_replacement_equal_to_template_text_is_trusted_when_explicitly_supplied():
    source = word('<w:r><w:t>ЗАЛИШЕНО</w:t></w:r><w:r><w:t>НОВЕ</w:t></w:r>')
    output = color_review_xml(source, {1: [(0, 4)]})
    assert [(value, color) for value, color, _ in runs(output)] == [("ЗАЛИШЕНО", "FF0000"), ("НОВЕ", "000000")]


def test_custom_prefixes_and_default_word_namespace_are_supported():
    source = f'<document xmlns="{W}"><body><p><r><rPr/><t>старе нове</t></r></p></body></document>'.encode()
    output = color_review_xml(source, {0: [(6, 10)]})
    root = ET.fromstring(output)
    values = root.findall('.//w:color', NS)
    assert [value.get('{' + W + '}val') for value in values] == ['FF0000', '000000']
    assert "".join(root.itertext()) == "старе нове"


def test_nested_drawing_run_and_bookmarks_do_not_disappear_or_duplicate():
    source = word('<w:bookmarkStart w:id="7" w:name="example"/><w:r><w:drawing><w:txbxContent>'
                  '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>НОВЕ</w:t></w:r></w:p>'
                  '</w:txbxContent></w:drawing></w:r><w:bookmarkEnd w:id="7"/>')
    output = color_review_xml(source, {0: [(0, 4)]})
    root = ET.fromstring(output)
    assert len(root.findall('.//w:drawing', NS)) == 1
    assert len(root.findall('.//w:bookmarkStart', NS)) == 1
    assert len(root.findall('.//w:bookmarkEnd', NS)) == 1
    inner = root.find('.//w:txbxContent/w:p/w:r', NS)
    assert inner.find('w:rPr/w:color', NS).get('{' + W + '}val') == '000000'


@pytest.mark.parametrize('trusted', [{9: [(0, 1)]}, {0: [(-1, 1)]}, {0: [(0, 100)]}])
def test_invalid_trusted_ranges_fail_without_silently_changing_highlighting(trusted):
    with pytest.raises(ReviewColorError):
        color_review_xml(word('<w:r><w:t>Текст</w:t></w:r>'), trusted)


@pytest.mark.parametrize('data', [b'<not closed', b'<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><a/>'])
def test_invalid_xml_and_external_entities_are_rejected(data):
    with pytest.raises(ReviewColorError):
        color_review_xml(data, {})
