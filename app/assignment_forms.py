"""Create editable task forms using the department's existing Word stationery."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import re
from zipfile import ZipFile

from lxml import etree

from .assignments_documents import FIELD_LABELS, _label, inspect_assignment_template
from .documents import TemplateError
from .library import ROOT
from .word_pagination import pagination_updates

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
BASE_FILENAMES = {'vet': 'все.docx', 'bacteriology': 'бак.docx', 'chemistry': 'хім токс.docx'}


def _text(element):
    return ''.join(element.xpath('.//w:t/text()', namespaces=NS))


def _set_paragraph(paragraph, value):
    properties = paragraph.find('w:pPr', NS)
    run_properties = paragraph.find('.//w:rPr', NS)
    run_properties = deepcopy(run_properties) if run_properties is not None else None
    for child in list(paragraph):
        if child is not properties:
            paragraph.remove(child)
    run = etree.SubElement(paragraph, '{' + W + '}r')
    if run_properties is not None:
        run.append(run_properties)
    node = etree.SubElement(run, '{' + W + '}t')
    node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    node.text = value


def _set_cell(cell, value):
    paragraphs = cell.findall('w:p', NS)
    model = deepcopy(paragraphs[0]) if paragraphs else etree.Element('{' + W + '}p')
    for child in list(cell):
        if child.tag != '{' + W + '}tcPr':
            cell.remove(child)
    for line in value.splitlines() or ['']:
        paragraph = deepcopy(model)
        _set_paragraph(paragraph, line)
        cell.append(paragraph)


def create_assignment_form(values: dict) -> bytes:
    """Keep headings, margins, table styles and footers; remove historical facts."""
    department = values['department']
    if department not in BASE_FILENAMES:
        raise TemplateError('Оберіть відділення для бланка завдання.')
    # XML control characters are rejected even when this function is used
    # outside the HTTP API. User text is always written as text, never XML.
    for value in [values.get(key, '') for key in ('product_name', 'standard', 'sample_weight')]:
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
            raise TemplateError('Текст бланка містить неприпустимі символи.')
    manifest = json.loads((ROOT / 'assets/assignments.json').read_text(encoding='utf-8'))
    base = next(item for item in manifest if item['department'] == department
                and item['filename'] == BASE_FILENAMES[department])
    source = (ROOT / 'assets/assignments' / base['asset']).read_bytes()
    with ZipFile(BytesIO(source)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
        body = root.find('w:body', NS)
        for paragraph in body.findall('w:p', NS):
            label = _label(_text(paragraph))
            if label:
                key = label[0]
                value = values.get(key, '').strip() if key in values else ''
                _set_paragraph(paragraph, FIELD_LABELS[key] + ': ' + value)
        paragraphs = body.findall('w:p', NS)
        for index, paragraph in enumerate(paragraphs):
            if re.match(r'\s*Сформулював\s+та\s+видав\s+завдання\s*:', _text(paragraph), re.I):
                if index + 1 < len(paragraphs):
                    _set_paragraph(paragraphs[index + 1], '____________________                         ____________________')
        table = next((table for table in body.findall('w:tbl', NS)
                      if 'Показник' in _text(table)), None)
        if table is None:
            raise TemplateError('У бланку відділення не знайдено таблицю показників.')
        rows = table.findall('w:tr', NS)
        model = next(row for row in rows[1:] if len(row.findall('w:tc', NS)) == 2)
        model = deepcopy(model)
        for row in rows[1:]:
            table.remove(row)
        for item in values['indicators']:
            row = deepcopy(model)
            cells = row.findall('w:tc', NS)
            indicator = item['indicator'].strip()
            if item.get('notes', '').strip():
                indicator += ('\n' if indicator else '') + 'Примітка: ' + item['notes'].strip()
            try:
                _set_cell(cells[0], indicator)
                _set_cell(cells[1], item['method'].strip())
            except ValueError as exc:
                raise TemplateError('Текст показника містить неприпустимі символи.') from exc
            table.append(row)
        xml = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
        output = BytesIO()
        parts = {member.filename: archive.read(member) for member in archive.infolist()}
        parts['word/document.xml'] = xml
        updates = pagination_updates(parts)
        with ZipFile(output, 'w') as target:
            target.comment = archive.comment
            for member in archive.infolist():
                target.writestr(member, updates.get(member.filename, parts[member.filename]))
    data = output.getvalue()
    inspect_assignment_template(data)
    return data
