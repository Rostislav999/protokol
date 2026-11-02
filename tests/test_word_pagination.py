from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E
import pytest

from app.documents import TemplateError
from app.word_pagination import pagination_updates, W, NS

Q = lambda name: '{' + W + '}' + name


def footer(content):
    return f'<w:ftr xmlns:w="{W}"><w:p><w:pPr><w:jc w:val="right"/></w:pPr>{content}</w:p></w:ftr>'.encode()


def run(text):
    return f'<w:r><w:rPr><w:i/><w:sz w:val="18"/></w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


def structural(node):
    return node.tag, sorted(node.attrib.items()), node.text, [structural(c) for c in node]


def character_styles(root):
    result = []
    for node in root.findall('.//w:t', NS):
        properties = node.getparent().find('w:rPr', NS)
        style = structural(properties) if properties is not None else None
        result.extend((char, style) for char in node.text or '')
    return result


@pytest.mark.parametrize('content', [
    run('Видання 04 від 23.09.2026 Сторінка 1 з 1'),
    run('Видання 04 Стор') + run('інка 1') + run('2 з ') + run('24'),
    run('Page 2 of 12'),
])
def test_live_fields_keep_all_text_and_character_formatting(content):
    original = footer(content)
    updated = pagination_updates({'word/footer1.xml': original})['word/footer1.xml']
    before, after = E.fromstring(original), E.fromstring(updated)
    assert character_styles(after) == character_styles(before)
    assert structural(after.find('w:p/w:pPr', NS)) == structural(before.find('w:p/w:pPr', NS))
    fields = after.findall('.//w:fldSimple', NS)
    assert [field.get(Q('instr')) for field in fields] == ['PAGE', 'NUMPAGES']
    assert all(field.get(Q('dirty')) == 'true' for field in fields)
    for node in after.findall('.//w:t', NS):
        if node.text and (node.text[0].isspace() or node.text[-1].isspace()):
            assert node.get('{http://www.w3.org/XML/1998/namespace}space') == 'preserve'


@pytest.mark.parametrize('sample', sorted((Path(__file__).parents[1] / 'assets/assignments').glob('*.docx')), ids=lambda p: p.name)
def test_every_bundled_footer_keeps_its_text_and_styles(sample):
    with ZipFile(sample) as archive:
        source = archive.read('word/footer1.xml')
    updated = pagination_updates({'word/footer1.xml': source})['word/footer1.xml']
    assert character_styles(E.fromstring(source)) == character_styles(E.fromstring(updated))


def test_all_footer_variants_update_while_other_parts_are_untouched_and_repeatable():
    source = footer(run('Сторінка 1 з 1'))
    settings = f'<w:settings xmlns:w="{W}"><w:zoom w:percent="90"/><w:updateFields w:val="false"/></w:settings>'.encode()
    parts = {name:source for name in ('word/footer1.xml','word/footer2.xml','word/footer3.xml','word/header1.xml')}
    parts.update({'word/settings.xml': settings, 'word/document.xml': b'body', 'word/styles.xml': b'styles'})
    updates = pagination_updates(parts)
    assert set(updates) == set(parts) - {'word/document.xml','word/styles.xml'}
    root = E.fromstring(updates['word/settings.xml'])
    assert root.find('w:updateFields', NS).get(Q('val')) == 'true'
    assert root.find('w:zoom', NS).get(Q('percent')) == '90'
    assert pagination_updates(parts | updates) == {}


def test_existing_complex_page_field_is_preserved_and_static_total_is_replaced():
    complex_page = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                    '<w:r><w:instrText> PAGE </w:instrText></w:r>'
                    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + run('1') +
                    '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    source = footer(run('Сторінка ') + complex_page + run(' з 1'))
    result = E.fromstring(pagination_updates({'word/footer1.xml':source})['word/footer1.xml'])
    assert result.find('.//w:instrText',NS).text == ' PAGE '
    assert [n.get(Q('instr')) for n in result.findall('.//w:fldSimple',NS)] == ['NUMPAGES']
    assert len(result.findall('.//w:fldChar',NS)) == 3


def test_non_pagination_numbers_and_untrusted_xml_are_not_rewritten():
    source = footer(run('Видання 04 Введено в дію 23.09.2026 ФСУ 7.4/01'))
    assert pagination_updates({'word/footer1.xml':source}) == {}
    with pytest.raises(TemplateError):
        pagination_updates({'word/footer1.xml':b'<!DOCTYPE x><x/>'})
