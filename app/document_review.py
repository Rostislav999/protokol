"""Color inherited Word text red and explicitly filled text black.

Patch original XML fragments to retain namespace declarations, field codes,
drawings and run formatting. Mixed runs are split only at trust boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
import re
from xml.parsers import expat

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class ReviewColorError(ValueError):
    pass


@dataclass
class _Element:
    name: str
    qname: bytes
    start: int
    opening_end: int
    empty: bool
    closing_start: int = 0
    end: int = 0
    children: list = field(default_factory=list)
    text: str = ""
    ordinal: int | None = None


def _tag_end(xml: bytes, start: int) -> int:
    quote = None
    for index in range(start, len(xml)):
        char = xml[index]
        if quote:
            if char == quote:
                quote = None
        elif char in (34, 39):
            quote = char
        elif char == 62:
            return index + 1
    raise ReviewColorError("Пошкоджений XML Word-документа.")


def _parse(xml: bytes):
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ReviewColorError("XML із DTD або зовнішніми сутностями не підтримується.")
    parser = expat.ParserCreate(namespace_separator="}")
    stack, roots, texts = [], [], []

    def start(name, attrs):
        offset = parser.CurrentByteIndex
        end = _tag_end(xml, offset)
        opening = xml[offset:end]
        qname = re.match(br"<([^\s/>]+)", opening).group(1)
        node = _Element(name, qname, offset, end, opening.rstrip().endswith(b"/>"))
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
        if name == W + "}t":
            node.ordinal = len(texts)
            texts.append(node)

    def chars(value):
        if stack and stack[-1].name == W + "}t":
            stack[-1].text += value

    def end(name):
        node = stack.pop()
        node.closing_start = node.opening_end if node.empty else parser.CurrentByteIndex
        node.end = node.opening_end if node.empty else _tag_end(xml, node.closing_start)

    parser.StartElementHandler, parser.CharacterDataHandler, parser.EndElementHandler = start, chars, end
    try:
        parser.Parse(xml, True)
    except expat.ExpatError as exc:
        raise ReviewColorError("Не вдалося прочитати XML для позначення відомостей.") from exc
    return roots, texts


def _offsets(raw: bytes, expected: str) -> list[int]:
    offsets, decoded, position = [0], [], 0
    for match in re.finditer(r"&[^;]+;|\r\n|\r|[\s\S]", raw.decode("utf-8")):
        token = match.group()
        char = unescape(token) if token.startswith("&") else token.replace("\r\n", "\n").replace("\r", "\n")
        if len(char) != 1:
            raise ReviewColorError("Непідтримуваний текстовий XML-вузол.")
        decoded.append(char)
        position += len(token.encode("utf-8"))
        offsets.append(position)
    if "".join(decoded) != expected:
        raise ReviewColorError("Не вдалося зберегти текст без змін під час виділення кольором.")
    return offsets


def color_review_xml(xml: bytes, trusted: dict[int, list[tuple[int, int]]] | None = None) -> bytes:
    """Use w:t ordinals and decoded character ranges of the updated XML.

    Every supplied range is trusted even when its new value equals a template
    value. All remaining visible text is inherited and receives red formatting.
    """
    roots, texts = _parse(xml)
    ranges = {}
    for ordinal, spans in (trusted or {}).items():
        if not isinstance(ordinal, int) or not 0 <= ordinal < len(texts):
            raise ReviewColorError("Некоректний номер текстового поля для позначення.")
        merged = []
        for start, end in sorted(spans):
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(texts[ordinal].text):
                raise ReviewColorError("Некоректні межі заповненого тексту.")
            if start == end:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        ranges[ordinal] = merged

    def raw(node):
        return xml[node.start:node.end]

    def properties(node, prefix: bytes, color: bytes) -> bytes:
        if prefix:
            color_tag = b"<" + prefix + b'color ' + prefix + b'val="' + color + b'"/>'
        else:
            color_tag = b'<color xmlns:review="' + W.encode() + b'" review:val="' + color + b'"/>'
        if node is None:
            return b"<" + prefix + b"rPr>" + color_tag + b"</" + prefix + b"rPr>"
        opening = xml[node.start:node.opening_end]
        closing = b"</" + node.qname + b">" if node.empty else xml[node.closing_start:node.end]
        if node.empty:
            return opening.rstrip()[:-2] + b">" + color_tag + closing
        pieces, cursor, inserted = [opening], node.opening_end, False
        for child in node.children:
            pieces.append(xml[cursor:child.start])
            if child.name == W + "}color":
                if not inserted:
                    pieces.append(color_tag)
                    inserted = True
            else:
                pieces.append(raw(child))
            cursor = child.end
        pieces.append(xml[cursor:node.closing_start])
        if not inserted:
            pieces.append(color_tag)
        pieces.append(closing)
        return b"".join(pieces)

    def text_parts(node):
        if not node.text:
            return [(b"FF0000", raw(node))]
        spans = ranges.get(node.ordinal, [])
        parts, cursor = [], 0
        for start, end in spans:
            if start > cursor:
                parts.append((cursor, start, b"FF0000"))
            parts.append((start, end, b"000000"))
            cursor = end
        if cursor < len(node.text):
            parts.append((cursor, len(node.text), b"FF0000"))
        opening = xml[node.start:node.opening_end]
        closing = xml[node.closing_start:node.end]
        content = xml[node.opening_end:node.closing_start]
        offsets = _offsets(content, node.text)
        result = []
        for start, end, color in parts:
            tag = opening
            segment = node.text[start:end]
            if segment and (segment[0].isspace() or segment[-1].isspace()):
                if b"xml:space=" in tag:
                    tag = re.sub(br"xml:space\s*=\s*(['\"]).*?\1", b'xml:space="preserve"', tag)
                else:
                    tag = tag[:-1] + b' xml:space="preserve">'
            result.append((color, tag + content[offsets[start]:offsets[end]] + closing))
        return result

    def render(node):
        if node.name != W + "}r":
            cursor, pieces = node.start, []
            for child in node.children:
                pieces.extend((xml[cursor:child.start], render(child)))
                cursor = child.end
            pieces.append(xml[cursor:node.end])
            return b"".join(pieces)
        if node.empty or not node.children:
            return raw(node)
        prefix = node.qname.rsplit(b":", 1)[0] + b":" if b":" in node.qname else b""
        rpr = next((child for child in node.children if child.name == W + "}rPr"), None)
        fragments, cursor = [], node.opening_end
        for child in node.children:
            gap = xml[cursor:child.start]
            if gap:
                fragments.append((None, gap))
            if child is not rpr:
                fragments.extend(text_parts(child) if child.name == W + "}t" else [(b"FF0000", render(child))])
            cursor = child.end
        if cursor < node.closing_start:
            fragments.append((None, xml[cursor:node.closing_start]))
        groups, pending = [], b""
        for color, fragment in fragments:
            if color is None:
                pending += fragment
            elif groups and groups[-1][0] == color:
                groups[-1][1].append(pending + fragment)
                pending = b""
            else:
                groups.append((color, [pending + fragment]))
                pending = b""
        if not groups:
            groups = [(b"FF0000", [pending])]
        elif pending:
            groups[-1][1].append(pending)
        opening, closing = xml[node.start:node.opening_end], xml[node.closing_start:node.end]
        return b"".join(opening + properties(rpr, prefix, color) + b"".join(content) + closing for color, content in groups)

    cursor, pieces = 0, []
    for root in roots:
        pieces.extend((xml[cursor:root.start], render(root)))
        cursor = root.end
    pieces.append(xml[cursor:])
    result = b"".join(pieces)
    _, updated_texts = _parse(result)
    if "".join(node.text for node in texts) != "".join(node.text for node in updated_texts):
        raise ReviewColorError("Під час позначення кольором змінився текст документа.")
    return result
