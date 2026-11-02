"""Turn printed page counts into Word fields without changing footer styling."""
from copy import deepcopy
import re

from lxml import etree as E

from .documents import TemplateError

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
Q = lambda name: '{' + W + '}' + name
PAGE_COUNT = re.compile(r'\b(?:Сторінка|Страница|Page)\s+(?P<page>\d+)\s+(?:з|із|из|of)\s+(?P<total>\d+)\b', re.I)


def _xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise TemplateError('XML із DTD або зовнішніми сутностями не підтримується.')
    try:
        return E.fromstring(data, E.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    except E.XMLSyntaxError as exc:
        raise TemplateError('Пошкоджений XML колонтитула Word.') from exc


def _serialize(root):
    return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _replace_number(paragraph, start, end, instruction):
    # Recompute offsets after each replacement: both digits can share one run.
    offset = 0
    targets = []
    for node in paragraph.findall('.//w:t', NS):
        value = node.text or ''
        left, right = max(0, start - offset), min(len(value), end - offset)
        if left < right:
            targets.append((node, left, right))
        offset += len(value)
    node, left, right = targets[0]
    # Preserve existing simple or complex field results instead of nesting fields.
    if node.xpath('ancestor::w:fldSimple', namespaces=NS):
        return False
    field_depth = 0
    for element in paragraph.iter():
        if element is node:
            break
        if element.tag == Q('fldChar'):
            if element.get(Q('fldCharType')) == 'begin':
                field_depth += 1
            elif element.get(Q('fldCharType')) == 'end':
                field_depth = max(0, field_depth - 1)
    if field_depth:
        return False
    value = node.text
    cached = ''.join((n.text or '')[a:b] for n, a, b in targets)
    for other, a, b in targets[1:]:
        other.text = (other.text or '')[:a] + (other.text or '')[b:]
    run = node.getparent()
    parent = run.getparent()
    index = parent.index(run)
    child_index = run.index(node)
    before, after = deepcopy(run), deepcopy(run)
    for child in list(before)[child_index + 1:]:
        before.remove(child)
    before[child_index].text = value[:left]
    for child in list(after)[:child_index]:
        if child.tag != Q('rPr'):
            after.remove(child)
    after.find('w:t', NS).text = value[right:]
    for text in (before[child_index], after.find('w:t', NS)):
        if text.text[:1].isspace() or text.text[-1:].isspace():
            text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    field = E.Element(Q('fldSimple'), {Q('instr'): instruction, Q('dirty'): 'true'})
    result = E.SubElement(field, Q('r'))
    properties = run.find('w:rPr', NS)
    if properties is not None:
        result.append(deepcopy(properties))
    E.SubElement(result, Q('t')).text = cached
    parent.remove(run)
    for i, replacement in enumerate((before, field, after)):
        parent.insert(index + i, replacement)
    return True


def pagination_updates(parts):
    """Return only changed header/footer/settings parts; other bytes stay intact."""
    updates = {}
    numbered = False
    for name, data in parts.items():
        if not re.fullmatch(r'word/(?:footer|header)[^/]*\.xml', name):
            continue
        root = _xml(data)
        changed = False
        for paragraph in root.findall('.//w:p', NS):
            codes = ' '.join(paragraph.xpath('.//w:instrText/text() | .//w:fldSimple/@w:instr', namespaces=NS))
            if re.search(r'\bPAGE\b', codes, re.I) and re.search(r'\bNUMPAGES\b', codes, re.I):
                numbered = True
                continue
            text = ''.join(paragraph.xpath('.//w:t/text()', namespaces=NS))
            for match in reversed(list(PAGE_COUNT.finditer(text))):
                for key, code in (('total', 'NUMPAGES'), ('page', 'PAGE')):
                    changed |= _replace_number(paragraph, *match.span(key), code)
                    numbered = True
        if changed:
            updates[name] = _serialize(root)
    if numbered and 'word/settings.xml' in parts:
        settings = _xml(parts['word/settings.xml'])
        update = settings.find('w:updateFields', NS)
        if update is None:
            update = E.SubElement(settings, Q('updateFields'))
        if update.get(Q('val')) not in ('true', '1', 'on'):
            update.set(Q('val'), 'true')
            updates['word/settings.xml'] = _serialize(settings)
    return updates
