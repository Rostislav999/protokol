"""Cover letters must retain the supplied Word stationery and paragraph layout."""
from io import BytesIO
from copy import deepcopy
from zipfile import ZipFile

from lxml import etree as E
import pytest

from app.cover_letters import ASSETS, LetterBatch, LetterContent, preview, render_letter
from app.protocol_excel import W


NS = {'w': W}


def document(data):
    with ZipFile(BytesIO(data)) as archive:
        return E.fromstring(archive.read('word/document.xml'))


def text(node):
    return ''.join(node.xpath('.//w:t/text()', namespaces=NS))


def structure(node):
    if node is None:
        return None
    return node.tag, sorted(node.attrib.items()), node.text, [structure(c) for c in node]


def without_pagination(node):
    node = deepcopy(node)
    for flag in node.xpath('.//w:keepNext | .//w:keepLines', namespaces=NS):
        flag.getparent().remove(flag)
    return node


def batch(count=3, **changes):
    return LetterBatch(records=[
        dict(number=f'87-015-2920-{2000+i}-2', date='02.11.2026',
             supplier='ТОВ «Нова фірма & партнери»', code='015', pages=i+1)
        for i in range(count)
    ], confirmed=True, **changes)


@pytest.mark.parametrize('general', [True, False])
def test_retains_page_stamp_and_all_other_docx_parts(general):
    template = (ASSETS / ('general.docx' if general else 'company.docx')).read_bytes()
    payload = batch()
    result = render_letter(payload.records, payload, general)
    original, rendered = document(template), document(result)
    assert structure(rendered.find('w:body/w:sectPr', NS)) == structure(original.find('w:body/w:sectPr', NS))
    # Preserve the positioned stamp and the original emblem when filling letters.
    stamp, original_stamp = rendered.find('w:body/w:tbl', NS), original.find('w:body/w:tbl', NS)
    for tag in ('tblPr', 'tblGrid', 'trPr', 'tcPr', 'pPr', 'drawing'):
        assert [structure(n) for n in stamp.findall('.//w:' + tag, NS)] == [
            structure(n) for n in original_stamp.findall('.//w:' + tag, NS)
        ]
    assert 'Код ЄДРПОУ 26614722' in text(stamp)
    assert 'E-mail: a0873@mod.gov.ua' in text(stamp)
    assert '«_____»____________________20____' in text(stamp)
    assert '№____________________________' in text(stamp)
    with ZipFile(BytesIO(template)) as before, ZipFile(BytesIO(result)) as after:
        assert after.namelist() == before.namelist()
        for name in before.namelist():
            if name != 'word/document.xml':
                assert after.read(name) == before.read(name), name


@pytest.mark.parametrize('general', [True, False])
def test_stamp_uses_reference_line_order_and_one_explicit_blue(general):
    payload = batch()
    rendered = document(render_letter(payload.records, payload, general))
    stamp = rendered.find('w:body/w:tbl', NS)
    assert stamp.find('.//w:tbl', NS) is None
    assert stamp.find('w:tblPr/w:tblpPr', NS).get('{' + W + '}vertAnchor') == 'page'
    assert stamp.find('w:tblPr/w:tblW', NS).attrib == {
        '{' + W + '}w': '4370', '{' + W + '}type': 'dxa',
    }
    assert [text(p) for p in stamp.findall('.//w:p', NS) if text(p)] == [
        'МІНІСТЕРСТВО ОБОРОНИ', 'УКРАЇНИ', 'ВІЙСЬКОВА ЧАСТИНА А0873',
        'E-mail: a0873@mod.gov.ua', 'Код ЄДРПОУ 26614722',
        '«_____»____________________20____', '№____________________________',
    ]
    for run in stamp.findall('.//w:r', NS):
        if text(run):
            assert run.find('w:rPr/w:color', NS).attrib == {'{' + W + '}val': '2E75B6'}
    assert all(color.attrib == {'{' + W + '}val': '2E75B6'}
               for color in stamp.findall('.//w:color', NS))
    drawing = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    duotone = stamp.find('.//a:duotone', drawing)
    assert [E.QName(node).localname for node in duotone] == ['srgbClr', 'srgbClr']
    assert [node.attrib for node in duotone] == [{'val': '2E75B6'}, {'val': 'FFFFFF'}]
    assert all(len(node) == 0 for node in duotone)  # No theme shade/tint overrides.


@pytest.mark.parametrize('general', [True, False])
def test_keeps_paragraph_spacing_fonts_and_blank_lines(general):
    original = document((ASSETS / ('general.docx' if general else 'company.docx')).read_bytes())
    payload = batch(contact='Ірина Приклад 00 000')
    rendered = document(render_letter(payload.records, payload, general))
    # Keep internal blank lines; trailing empty paragraphs are removed to avoid
    # an extra blank page when the content ends at the bottom of the sheet.
    def fixed_paragraphs(root):
        paragraphs = [p for p in root.findall('w:body/w:p', NS)
                      if not text(p).startswith('Додаток:') and not text(p)[:1].isdigit()]
        while paragraphs and not text(paragraphs[-1]):
            paragraphs.pop()
        return paragraphs
    before, after = fixed_paragraphs(original), fixed_paragraphs(rendered)
    assert len(after) == len(before)
    rank = 'полковник' if general else 'майор'
    for p, q in zip(before, after):
        if text(p).startswith(rank):
            continue  # Signature padding is deliberately replaced by a tab.
        assert structure(without_pagination(q.find('w:pPr', NS))) == structure(p.find('w:pPr', NS))
        assert [structure(r) for r in q.findall('.//w:rPr', NS)] == [
            structure(r) for r in p.findall('.//w:rPr', NS)
        ]
        if not text(p):
            assert structure(q) == structure(p)
    signature = next(p for p in before if text(p).startswith(rank))
    filled = next(p for p in after if text(p).startswith(rank))
    properties = without_pagination(filled.find('w:pPr', NS))
    properties.remove(properties.find('w:tabs', NS))
    assert structure(properties) == structure(signature.find('w:pPr', NS))
    original_runs, filled_runs = signature.findall('w:r', NS), filled.findall('w:r', NS)
    assert [structure(r.find('w:rPr', NS)) for r in filled_runs] == [
        structure(r.find('w:rPr', NS)) for r in (original_runs[0], original_runs[-1])
    ]
    assert filled.find('w:pPr/w:keepLines', NS) is not None
    position = after[after.index(filled) - 1]
    assert position.find('w:pPr/w:keepLines', NS) is not None
    assert position.find('w:pPr/w:keepNext', NS) is not None
    assert 'Ірина Приклад 00 000' in text(rendered)
    assert 'ГРАНД КОНСАЛТ' not in text(rendered)
    assert 'Катерина Сержант-Міщенко' not in text(rendered)
    assert '42 029' not in text(rendered)


@pytest.mark.parametrize('general', [True, False])
@pytest.mark.parametrize('contact', ['', 'Ірина Приклад 00 000'])
def test_letter_ends_at_signature_or_contact_without_empty_page_paragraphs(general, contact):
    payload = batch(12, contact=contact)
    rendered = document(render_letter(payload.records, payload, general))
    paragraphs = rendered.findall('w:body/w:p', NS)
    expected = contact or (payload.general_signer if general else payload.company_signer)
    last_text = ''.join('\t' if node.tag == '{' + W + '}tab' else node.text or ''
                        for node in paragraphs[-1].iter()
                        if node.tag in ('{' + W + '}t', '{' + W + '}tab'))
    assert ' '.join(last_text.split()) == ' '.join(expected.split())
    assert rendered.find('w:body', NS)[-1].tag == '{' + W + '}sectPr'


@pytest.mark.parametrize('general', [True, False])
@pytest.mark.parametrize('saved_edit', [False, True])
def test_default_signature_uses_a_right_tab_and_unbroken_name(general, saved_edit):
    payload = batch()
    content = None
    if saved_edit:
        letter = preview(payload)['letters'][0 if general else 1]
        # A previously saved draft may retain the template's space padding.
        content = LetterContent.model_validate({
            **letter['content'], 'signer': letter['content']['signer'].replace(' ', '     '),
        })
    rendered = document(render_letter(payload.records, payload, general, content))
    rank, name = ('полковник', 'Олександр\u00a0КАЛЮЖНИЙ') if general else ('майор', 'Ростислав\u00a0АДРІАНОВ')
    signature = next(p for p in rendered.findall('w:body/w:p', NS) if text(p).startswith(rank))
    assert [text(r) for r in signature.findall('w:r', NS)] == [rank, name]
    assert len(signature.findall('.//w:r/w:tab', NS)) == 1
    assert signature.find('w:pPr/w:tabs/w:tab', NS).attrib == {
        '{' + W + '}val': 'right', '{' + W + '}pos': '9638',
    }
    assert signature.find('.//w:br', NS) is None


@pytest.mark.parametrize('general', [True, False])
@pytest.mark.parametrize('count', [1, 9, 12, 100])
def test_variable_attachment_list_uses_template_indents_and_fonts(general, count):
    original = document((ASSETS / ('general.docx' if general else 'company.docx')).read_bytes())
    payload = batch(count, include_excel=False)
    rendered = document(render_letter(payload.records, payload, general))
    prototypes = [p for p in original.findall('w:body/w:p', NS) if 'Протокол випробувань №' in text(p)]
    attachments = [p for p in rendered.findall('w:body/w:p', NS) if 'Протокол випробувань №' in text(p)]
    assert len(attachments) == count
    for i, p in enumerate(attachments):
        source = prototypes[0 if i == 0 else 1]
        assert structure(p.find('w:pPr', NS)) == structure(source.find('w:pPr', NS))
        assert [structure(r) for r in p.findall('w:r/w:rPr', NS)] == [
            structure(r) for r in source.findall('w:r/w:rPr', NS)
        ]
        assert payload.records[i].number in text(p)
        assert f'на {i+1} арк.' in text(p)
        assert text(p).startswith(('Додаток: ' if i == 0 else '') + f'{i+1}. ')
    assert '87-024-2948-' not in text(rendered)
    assert 'Інформація про харчові продукти, що не відповідають' not in text(rendered)
    assert 'Зведена таблиця' not in text(rendered)
    # Cloned paragraphs must not duplicate Word paragraph/bookmark identifiers.
    ids = rendered.xpath('//@w14:paraId', namespaces={'w14': 'http://schemas.microsoft.com/office/word/2010/wordml'})
    assert len(ids) == len(set(ids))
    starts = rendered.xpath('.//w:bookmarkStart/@w:id', namespaces=NS)
    ends = rendered.xpath('.//w:bookmarkEnd/@w:id', namespaces=NS)
    assert sorted(starts) == sorted(ends)
    assert len(starts) == len(set(starts))


def test_extra_attachments_and_custom_signers_keep_their_layout():
    payload = batch(12, extra_attachments='Інший документ на 2 арк.\nЩе один документ на 1 арк.',
                    general_position='Начальник установи', general_signer='підполковник Ірина ПРИКЛАД',
                    company_position='Виконувач обов’язків начальника',
                    company_signer='старший лейтенант Іван ПРИКЛАД')
    for general in (True, False):
        original = document((ASSETS / ('general.docx' if general else 'company.docx')).read_bytes())
        rendered = document(render_letter(payload.records, payload, general))
        paragraphs = rendered.findall('w:body/w:p', NS)
        rank, name = ('підполковник', 'Ірина ПРИКЛАД') if general else ('старший лейтенант', 'Іван ПРИКЛАД')
        signature = next(p for p in paragraphs if text(p).startswith(rank))
        old_rank = 'полковник' if general else 'майор'
        old_signature = next(p for p in original.findall('w:body/w:p', NS) if text(p).startswith(old_rank))
        runs = signature.findall('w:r', NS)
        assert text(runs[0]) == rank
        assert text(runs[1]) == name.replace(' ', '\u00a0')
        assert len(signature.findall('.//w:r/w:tab', NS)) == 1
        assert signature.find('w:pPr/w:tabs/w:tab', NS).attrib == {
            '{'+W+'}val': 'right', '{'+W+'}pos': '9638',
        }
        properties = signature.find('w:pPr', NS)
        properties.remove(properties.find('w:tabs', NS))
        assert structure(without_pagination(properties)) == structure(old_signature.find('w:pPr', NS))
        if general:
            additions = [p for p in paragraphs if text(p).startswith(('13. ', '14. ', '15. '))]
            assert len(additions) == 3
            assert 'Інший документ' in text(additions[0])
            assert 'Ще один документ' in text(additions[1])
            assert 'Зведена таблиця' in text(additions[2])
            prototypes = [p for p in original.findall('w:body/w:p', NS) if text(p).startswith(('10. ', '11. '))]
            for p, source in zip(additions, [prototypes[0], prototypes[0], prototypes[1]]):
                assert structure(p.find('w:pPr', NS)) == structure(source.find('w:pPr', NS))
        else:
            assert 'Інший документ' not in text(rendered)
            assert 'Зведена таблиця' not in text(rendered)
