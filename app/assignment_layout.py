"""Remove layout-only overflow from task forms without shortening their contents."""
from copy import deepcopy
import re

from lxml import etree as E

from .word_pagination import NS, PAGE_COUNT, Q, _serialize, _xml

XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
PROTECTED = ('drawing', 'pict', 'object', 'sectPr', 'pBdr', 'fldSimple', 'fldChar',
             'instrText', 'numPr', 'sdt', 'ins', 'del', 'br')


def _text(node):
    return ''.join(node.xpath('.//w:t/text()', namespaces=NS))


def _blank(paragraph):
    return (paragraph.tag == Q('p') and not _text(paragraph).strip()
            and not any(paragraph.find('.//w:' + name, NS) is not None for name in PROTECTED))


def _property(parent, name, **attributes):
    node = parent.find('w:' + name, NS)
    if node is None:
        node = E.SubElement(parent, Q(name))
    for key, value in attributes.items():
        node.set(Q(key), str(value))
    return node


def _font(properties, half_points):
    _property(properties, 'sz', val=half_points)
    _property(properties, 'szCs', val=half_points)


def _compact_spacer(paragraph, points=2):
    old = paragraph.find('w:pPr', NS)
    properties = E.Element(Q('pPr'))
    if old is not None:
        style = old.find('w:pStyle', NS)
        if style is not None:
            properties.append(deepcopy(style))
        paragraph.remove(old)
    paragraph.insert(0, properties)
    for name in ('keepNext', 'keepLines', 'pageBreakBefore', 'widowControl', 'snapToGrid'):
        _property(properties, name, val='0')
    _property(properties, 'spacing', before=0, after=0, line=points * 20, lineRule='exact')
    _font(_property(properties, 'rPr'), points * 2)
    for properties in paragraph.findall('w:r/w:rPr', NS):
        _font(properties, points * 2)


def _compact_type(paragraph):
    for size in paragraph.xpath('.//w:sz | .//w:szCs', namespaces=NS):
        try:
            if int(size.get(Q('val'), '0')) > 22:
                size.set(Q('val'), '22')
        except ValueError:
            continue


def _body_layout(root):
    body = root.find('w:body', NS)
    if body is None:
        return
    for table in body.findall('w:tbl', NS):
        rows = table.findall('w:tr', NS)
        if not rows or not re.search(r'Показник|Метод.*випробув', _text(rows[0]), re.I):
            continue
        properties = table.find('w:tblPr', NS)
        if properties is not None:
            floating = properties.find('w:tblpPr', NS)
            if floating is not None:
                properties.remove(floating)
        for cell in table.findall('.//w:tc', NS):
            paragraphs = cell.findall('w:p', NS)
            for paragraph in paragraphs:
                _compact_type(paragraph)
            if any(_text(paragraph).strip() for paragraph in paragraphs):
                for paragraph in paragraphs:
                    if _blank(paragraph):
                        _compact_spacer(paragraph)
    previous_text = ''
    for paragraph in body.findall('w:p', NS):
        # A line made of 100+ underscores wraps at the right margin. A border
        # gives the same writing line at the actual paragraph width.
        text = _text(paragraph)
        issuer_slot = bool(re.match(r'\s*(?:Сформулював\s+та\s+видав\s+завдання|Видав)\s*:', previous_text, re.I))
        if (re.fullmatch(r'[\s_]{12,}', text) and '_' in text and not issuer_slot
                and not any(paragraph.find('.//w:' + name, NS) is not None for name in PROTECTED)):
            for node in paragraph.findall('.//w:t', NS):
                node.text = ''
            properties = paragraph.find('w:pPr', NS)
            if properties is None:
                properties = E.Element(Q('pPr')); paragraph.insert(0, properties)
            border = _property(properties, 'pBdr')
            _property(border, 'bottom', val='single', sz=4, space=1, color='auto')
        if text.strip():
            previous_text = text
    # Retain a real signature line; discard only empty trailing paragraphs.
    table_tail = None
    while len(body):
        index = len(body) - 2 if body[-1].tag == Q('sectPr') else len(body) - 1
        if index < 0 or not _blank(body[index]):
            break
        if index > 0 and body[index - 1].tag == Q('tbl'):
            # Word requires a paragraph after the last table. Keep it at 1 pt
            # rather than letting Word reinsert a full-height blank line.
            table_tail = body[index]
            break
        body.remove(body[index])
    writing_space = False
    for block in body:
        if block.tag != Q('p'):
            writing_space = False
            continue
        if _blank(block):
            if block is table_tail:
                _compact_spacer(block, points=1)
            elif writing_space:
                writing_space = False
            else:
                _compact_spacer(block)
        else:
            writing_space = bool(re.search(r'Представник\s+відділення|^Отримав\s*:', _text(block), re.I))
            # Use readable 11 pt instead of oversized sample/signature text;
            # font family, bold/italic and already smaller text are preserved.
            _compact_type(block)


def _run(paragraph, text, properties):
    run = E.SubElement(paragraph, Q('r'))
    run.append(deepcopy(properties))
    node = E.SubElement(run, Q('t')); node.set(XML_SPACE, 'preserve'); node.text = text
    return run


def _footer_layout(root, width):
    for paragraph in root.findall('.//w:p', NS):
        if any(paragraph.find('.//w:' + name, NS) is not None for name in ('drawing', 'pict', 'object', 'sdt')):
            continue
        instructions = paragraph.xpath('.//w:instrText/text() | .//w:fldSimple/@w:instr', namespaces=NS)
        if any(code.strip() and not re.match(r'\s*(?:PAGE|NUMPAGES)\b', code, re.I) for code in instructions):
            continue
        text = ''.join(element.text or '' if element.tag == Q('t') else '\t'
                       for element in paragraph.iter() if element.tag in (Q('t'), Q('tab')))
        match = PAGE_COUNT.search(text)
        if match is None or text[match.end():].strip():
            continue
        label = re.match(r'\S+', match.group()).group()
        connector = re.search(r'\d+\s+(з|із|из|of)\s+\d+', match.group(), re.I).group(1)
        metadata = re.sub(r'\s+', ' ', text[:match.start()]).strip()
        source_properties = paragraph.find('.//w:rPr', NS)
        font = deepcopy(source_properties) if source_properties is not None else E.Element(Q('rPr'))
        _font(font, 16)
        properties = E.Element(Q('pPr'))
        _property(properties, 'keepNext', val='0')
        _property(properties, 'keepLines', val='1')
        _property(properties, 'snapToGrid', val='0')
        _property(_property(properties, 'tabs'), 'tab', val='right', pos=width)
        _property(properties, 'spacing', before=0, after=0, line=200, lineRule='exact')
        _property(properties, 'ind', left=0, right=0, firstLine=0)
        _property(properties, 'jc', val='left')
        mark = _property(properties, 'rPr'); mark.extend(deepcopy(list(font)))
        paragraph[:] = [properties]
        if metadata:
            _run(paragraph, metadata, font)
        tab = E.SubElement(paragraph, Q('r')); tab.append(deepcopy(font)); E.SubElement(tab, Q('tab'))
        # NBSPs keep the complete page counter together. PAGE/NUMPAGES remain
        # live Word fields so longer, meaningful documents keep honest counts.
        _run(paragraph, label + '\u00a0', font)
        for key, instruction in (('page', 'PAGE'), ('total', 'NUMPAGES')):
            field = E.SubElement(paragraph, Q('fldSimple'), {Q('instr'): instruction, Q('dirty'): 'true'})
            _run(field, match.group(key), font)
            if key == 'page':
                _run(paragraph, '\u00a0' + connector + '\u00a0', font)


def assignment_layout_updates(parts):
    """Normalize generated assignments and blank forms, leaving originals intact."""
    if 'word/document.xml' not in parts:
        return {}
    root = _xml(parts['word/document.xml'])
    section = root.find('w:body/w:sectPr', NS)
    width = 9638  # A4 portrait with the supplied form's left/right margins.
    if section is not None:
        size, margins = section.find('w:pgSz', NS), section.find('w:pgMar', NS)
        if size is not None and margins is not None:
            width = max(1000, int(size.get(Q('w'), '11906'))
                        - int(margins.get(Q('left'), '1701')) - int(margins.get(Q('right'), '567')))
    _body_layout(root)
    updates = {'word/document.xml': _serialize(root)}
    for name, data in parts.items():
        if re.fullmatch(r'word/footer[^/]*\.xml', name):
            footer = _xml(data)
            _footer_layout(footer, width)
            updates[name] = _serialize(footer)
    return updates
