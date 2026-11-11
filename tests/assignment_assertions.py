"""Compare content and styles while allowing the requested compact page layout."""
import re
import xml.etree.ElementTree as ET

from app.assignments_documents import _load

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}


def footer_text(root):
    pieces = []
    for element in root.iter():
        if element.tag == '{' + W + '}t':
            pieces.append(element.text or '')
        elif element.tag == '{' + W + '}tab':
            pieces.append(' ')
    return re.sub(r'\s+', ' ', ''.join(pieces)).strip()


def static_structure(data, include_issuer=False):
    loaded = _load(data)
    targets = loaded.targets + (loaded.issuer_targets if include_issuer else [])
    editable = {id(paragraph) for target in targets for paragraph in target.paragraphs}
    indexes = {i for i, paragraph in enumerate(loaded.paragraphs) if id(paragraph) in editable}
    root = ET.fromstring(loaded.xml)
    paragraphs = list(root.findall('.//w:p', NS))
    protected = {id(paragraphs[i]) for i in indexes}
    # Empty spacers and signature rules may change their geometry; all real
    # methods, limits, names, table rows/cells and their run styles still match.
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag == '{' + W + '}tblpPr':
                parent.remove(child)
            elif child.tag == '{' + W + '}p' and id(child) not in protected:
                text = ''.join(child.itertext())
                has_object = any(child.find('.//w:' + name, NS) is not None
                                 for name in ('drawing', 'pict', 'object', 'sectPr', 'fldSimple'))
                if not has_object and (not text.strip() or re.fullmatch(r'[\s_]+', text)):
                    parent.remove(child)
    # Type size is the other intentional change; font family and emphasis stay.
    for paragraph in root.findall('.//w:p', NS):
        for properties in paragraph.findall('.//w:rPr', NS):
            for size in list(properties):
                if size.tag in {'{' + W + '}sz', '{' + W + '}szCs'}:
                    properties.remove(size)
    for i in indexes:
        for text in paragraphs[i].findall('.//w:t', NS):
            text.text = ''
            text.attrib.pop('{http://www.w3.org/XML/1998/namespace}space', None)
    return ET.tostring(root)
