"""Real page counts and content preservation for compact assignment printing."""
from collections import Counter
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata
from zipfile import ZipFile

from lxml import etree as E
import pytest

from app.assignment_forms import create_assignment_form
from app.assignment_layout import assignment_layout_updates
from app.assignments_documents import _label, inspect_assignment_template, render_assignment
from app.word_pagination import NS, Q

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'assets/assignments.json').read_text(encoding='utf-8'))
SOFFICE = shutil.which('soffice') or shutil.which('libreoffice')


def parts(data):
    with ZipFile(BytesIO(data)) as z:
        return {member.filename: z.read(member) for member in z.infolist()}


def filled(sample):
    source = (ROOT / 'assets/assignments' / sample['asset']).read_bytes()
    info = inspect_assignment_template(source)
    return render_assignment(source, {**{key: info[key] for key in ('product_name', 'standard', 'sample_weight')},
                                      'sample_number': '1501', 'issue_date': '2026-11-11'})


def text(element):
    return ''.join(element.xpath('.//w:t/text()', namespaces=NS))


def fixed_rows(root):
    return [[text(c) for c in row.findall('w:tc', NS)] for row in root.findall('.//w:tr', NS)
            if row.findall('w:tc', NS) and _label(text(row.findall('w:tc', NS)[0])) is None]


@pytest.mark.parametrize('sample', MANIFEST, ids=lambda sample: sample['filename'])
def test_all_forms_keep_every_research_row_and_use_one_unbreakable_footer_counter(sample):
    source = parts((ROOT / 'assets/assignments' / sample['asset']).read_bytes())
    output = parts(filled(sample))
    before, after = E.fromstring(source['word/document.xml']), E.fromstring(output['word/document.xml'])
    assert E.tostring(before.find('w:body/w:sectPr', NS)) == E.tostring(after.find('w:body/w:sectPr', NS))
    assert len(before.findall('.//w:tr', NS)) == len(after.findall('.//w:tr', NS))
    assert fixed_rows(before) == fixed_rows(after)
    for table in after.findall('w:body/w:tbl', NS):
        rows = table.findall('w:tr', NS)
        if rows and re.search(r'Показник|Метод.*випробув', text(rows[0]), re.I):
            assert table.find('w:tblPr/w:tblpPr', NS) is None
    paragraphs = after.findall('w:body/w:p', NS)
    if paragraphs:
        last = paragraphs[-1]
        assert (text(last).strip() or last.find('w:pPr/w:pBdr', NS) is not None
                or (last.getprevious().tag == Q('tbl')
                    and last.find('w:pPr/w:spacing', NS).get(Q('line')) == '20'))
    footer = E.fromstring(output['word/footer1.xml'])
    assert footer.xpath('.//w:fldSimple/@w:instr', namespaces=NS) == ['PAGE', 'NUMPAGES']
    assert 'Сторінка\u00a0' in text(footer) and '\u00a0з\u00a0' in text(footer)
    assert footer.find('.//w:pPr/w:spacing', NS).get(Q('after')) == '0'
    assert all(size == '16' for size in footer.xpath('.//w:sz/@w:val', namespaces=NS))
    assert not footer.findall('.//w:br', NS)


def test_final_table_keeps_only_a_minimal_required_paragraph_and_layout_is_idempotent():
    source = f'''<w:document xmlns:w="{NS['w']}"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Зміст</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p/><w:p/><w:sectPr/></w:body></w:document>'''.encode()
    original = {'word/document.xml': source}
    updates = assignment_layout_updates(original)
    body = E.fromstring(updates['word/document.xml']).find('w:body', NS)
    assert [E.QName(child).localname for child in body] == ['tbl', 'p', 'sectPr']
    assert body[1].find('w:pPr/w:spacing', NS).get(Q('line')) == '20'
    assert body[1].find('w:pPr/w:rPr/w:sz', NS).get(Q('val')) == '2'
    assert assignment_layout_updates({**original, **updates}) == updates


def test_empty_tail_removed_but_signature_rule_and_custom_footer_drawing_preserved():
    source = f'''<w:document xmlns:w="{NS['w']}"><w:body><w:p><w:r><w:t>Дата видачі завдання: 11.11.2026</w:t></w:r></w:p><w:p><w:r><w:t>______________________________________________</w:t></w:r></w:p><w:p/><w:p/><w:sectPr/></w:body></w:document>'''.encode()
    footer = f'''<w:ftr xmlns:w="{NS['w']}"><w:p><w:r><w:drawing/></w:r><w:r><w:t>Сторінка 1 з 1</w:t></w:r></w:p></w:ftr>'''.encode()
    updates = assignment_layout_updates({'word/document.xml': source, 'word/footer1.xml': footer})
    body = E.fromstring(updates['word/document.xml']).find('w:body', NS)
    assert len(body.findall('w:p', NS)) == 2
    assert body[1].find('w:pPr/w:pBdr/w:bottom', NS) is not None
    assert not text(body[1]).strip()
    assert E.fromstring(updates['word/footer1.xml']).find('.//w:drawing', NS) is not None


def words(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('\u00ad', '')
    value = re.sub(r'(?<=\w)\s*[-‐‑‒–]\s*(?=\w)', '', value)
    return Counter(re.findall(r'\w+', value))


@pytest.mark.skipif(not SOFFICE, reason='Requires a real LibreOffice renderer for DOCX page counts')
def test_rendered_sixty_forms_fit_one_page_and_long_forms_keep_all_content(tmp_path):
    import fitz
    inputs, outputs = tmp_path / 'docx', tmp_path / 'pdf'
    inputs.mkdir(); outputs.mkdir()
    for index, sample in enumerate(MANIFEST):
        (inputs / f'{index:02}.docx').write_bytes(filled(sample))
    for department in ('vet', 'bacteriology', 'chemistry'):
        blank = create_assignment_form({'department': department, 'indicators': [
            {'indicator': '', 'method': '', 'notes': ''} for _ in range(3)]})
        (inputs / f'blank-{department}.docx').write_bytes(blank)
    long = create_assignment_form({'department': 'bacteriology', 'product_name': 'Довге завдання',
                                  'standard': 'НД з акта', 'indicators': [
        {'indicator': f'Показник {index}: контроль якості та безпечності нового продукту',
         'method': f'Метод {index}: ДСТУ 1234:2026', 'notes': 'Додаткова примітка'} for index in range(80)]})
    (inputs / 'long.docx').write_bytes(render_assignment(long, {
        'product_name': 'Довге завдання', 'standard': 'НД з акта', 'sample_number': '1501',
        'sample_weight': '3,5 кг', 'issue_date': '2026-11-11'}))
    result = subprocess.run([SOFFICE, '-env:UserInstallation=' + (tmp_path / 'profile').as_uri(),
                             '--headless', '--convert-to', 'pdf', '--outdir', str(outputs),
                             *map(str, sorted(inputs.glob('*.docx')))], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list(outputs.glob('*.pdf'))) == len(MANIFEST) + 4
    for path in sorted(inputs.glob('*.docx')):
        with fitz.open(outputs / (path.stem + '.pdf')) as pdf:
            if path.stem == 'long':
                assert len(pdf) > 1
                assert 'Показник 79' in pdf[-1].get_text()
            else:
                assert len(pdf) == 1, path.name
            actual = words('\n'.join(page.get_text() for page in pdf))
            document = E.fromstring(parts(path.read_bytes())['word/document.xml'])
            expected = words('\n'.join(text(p) for p in document.findall('.//w:p', NS)))
            assert not expected - actual, (path.name, expected - actual)
            for page_number, page in enumerate(pdf, 1):
                lines = [line for block in page.get_text('dict')['blocks'] if 'lines' in block for line in block['lines']
                         if 'Сторінка' in ''.join(span['text'] for span in line['spans'])]
                assert len(lines) == 1
                caption = ''.join(span['text'] for span in lines[0]['spans'])
                assert re.search(rf'Сторінка\s+{page_number}\s+з\s+{len(pdf)}\b', caption), (path.name, caption)
                assert lines[0]['bbox'][2] <= page.rect.width
