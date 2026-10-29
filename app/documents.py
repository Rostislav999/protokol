"""Fill protocol cover fields without reserializing the original Word XML.

Cover fields and explicitly requested act/seal values are patched. Repeated
protocol numbers in headers/footers are synchronized; other XML stays intact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from html import unescape
import re
from xml.parsers import expat
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZipFile

STOP_PHRASE = "Вищезазначена інформація надана замовником."
MISSING_VALUE = "[НЕ ЗАПОВНЕНО]"
FIELD_LABELS = {
    "protocol_number": "Номер протоколу / ідентифікаційний номер",
    "protocol_date": "Дата протоколу",
    "customer": "Замовник",
    "catalog_number": "Номер за Каталогом продуктів харчування",
    "product_name": "Назва зразка",
    "sampling_description": "Адреса та обставини відбору зразків",
    "manufacturer": "Виробник",
    "supplier": "Постачальник",
    "contract": "Договір",
    "manufacture_date": "Дата виготовлення / вжити до",
    "batch_size": "Розмір партії",
    "act_number": "Номер акта відбору зразків",
    "act_date": "Дата акта відбору зразків",
    "seal_number": "Номер пломби зразка",
    "sample_weight": "Вага зразка",
}
_LEGACY_LABELS = {
    "customer": "Замовник:",
    "catalog_number": "Номер за Каталогом продуктів харчування:",
    "sampling_description": "Зразки харчових продуктів відібрано за адресою:",
    "manufacturer": "Виробник:",
    "supplier": "Постачальник:",
    "contract": "Договір:",
    "manufacture_date": "Дата виготовлення:",
    "batch_size": "Розмір партії, від якої відібрано зразок:",
}
_IDENTIFICATION = "Ідентифікаційний номер та назва зразка:"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML_SPACE = "http://www.w3.org/XML/1998/namespace}space"
_MARKER_RE = re.compile(r"Вищезазначена\s+інформація\s+надана\s+замовником\.", re.I)
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_TITLE_RE = re.compile(r"^\s*ПРОТОКОЛ\s+ВИПРОБУВАНЬ\s*№?\s*(.+?)\s*$", re.I)
_HISTORICAL_REVISION_RE = re.compile(
    r"^\s*Цей\s+протокол\s+випробувань\s+(?:виданий|видано|оформлений)\s+на\s+заміну\b", re.I
)
_DATE_RE = re.compile(r"(?:«?\d{1,2}»?\s+\w+\s+\d{4}\s+року|\d{2}[./]\d{2}[./]\d{4}|\d{4}-\d{2}-\d{2})\s*\.?$", re.I)
_MONTHS = ("січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня")
_ADMIN_FIELDS = {"act_number", "act_date", "seal_number", "sample_weight"}
_PART_NUMBER_RE = re.compile(
    r"\bПротокол(?:у)?\s+випробувань\s*№\s*(?P<number>[\w]+(?:[-/.‐‑‒–—−][\w]+)*)", re.I,
)


class TemplateError(ValueError):
    """The supplied document cannot safely serve as a protocol template."""


@dataclass
class _Text:
    start: int
    end: int = 0
    text: str = ""
    tag_start: int = 0
    tag_end: int = 0
    preserve_space: bool = False
    empty: bool = False


@dataclass
class _Paragraph:
    nodes: list[_Text] = field(default_factory=list)
    limit: int = 0
    in_table: bool = False

    @property
    def text(self) -> str:
        return "".join(n.text for n in self.nodes)

    @property
    def editable(self) -> str:
        return self.text[: self.limit]


@dataclass
class _Document:
    data: bytes
    xml: bytes
    paragraphs: list[_Paragraph]
    fields: dict[str, str]
    available: set[str]


def _tag_end(xml: bytes, start: int) -> int:
    """Find a tag's end while allowing '>' inside quoted attribute values."""
    quote = None
    for i in range(start, len(xml)):
        b = xml[i]
        if quote:
            if b == quote:
                quote = None
        elif b in (34, 39):
            quote = b
        elif b == 62:
            return i + 1
    raise TemplateError("Пошкоджений XML у Word-документі.")


def _read_xml(xml: bytes, *, require_marker: bool = True) -> list[_Paragraph]:
    # Expat supplies byte offsets into the *original* XML, avoiding a rewrite of
    # namespaces, fields, tables, signatures or runs outside the edited ranges.
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise TemplateError("XML із DTD або зовнішніми сутностями не підтримується.")
    parser = expat.ParserCreate(namespace_separator="}")
    paragraphs: list[_Paragraph] = []
    stack: list[_Paragraph] = []
    current: _Text | None = None
    table_depth = 0

    def start(name, attrs):
        nonlocal current, table_depth
        if name == _W_NS + "}tc":
            table_depth += 1
        elif name == _W_NS + "}p":
            para = _Paragraph(in_table=table_depth > 0)
            paragraphs.append(para)
            stack.append(para)
        elif name == _W_NS + "}t" and stack:
            offset = parser.CurrentByteIndex
            end = _tag_end(xml, offset)
            current = _Text(start=end, tag_start=offset, tag_end=end,
                            preserve_space=attrs.get(_XML_SPACE) == "preserve",
                            empty=xml[offset:end].rstrip().endswith(b"/>"))
            stack[-1].nodes.append(current)

    def chars(value):
        if current is not None:
            current.text += value

    def end(name):
        nonlocal current, table_depth
        if name == _W_NS + "}t" and current is not None:
            current.end = current.start if current.empty else parser.CurrentByteIndex
            current = None
        elif name == _W_NS + "}p" and stack:
            stack.pop()
        elif name == _W_NS + "}tc":
            table_depth -= 1

    parser.StartElementHandler = start
    parser.CharacterDataHandler = chars
    parser.EndElementHandler = end
    try:
        parser.Parse(xml, True)
    except expat.ExpatError as exc:
        raise TemplateError("Не вдалося прочитати XML Word-документа.") from exc
    combined = "\n".join(p.text for p in paragraphs)
    marker = _MARKER_RE.search(combined)
    if not marker and require_marker:
        raise TemplateError(f"У шаблоні відсутня контрольна фраза: «{STOP_PHRASE}».")
    position = 0
    for para in paragraphs:
        para.limit = (max(0, min(len(para.text), marker.start() - position))
                      if marker and require_marker else len(para.text))
        position += len(para.text) + 1
    return paragraphs


def _legacy_parts(text: str) -> tuple[str, int, int] | None:
    for key, label in _LEGACY_LABELS.items():
        match = re.match(r"\s*" + re.escape(label) + r"\s*", text, re.I)
        if match:
            return key, match.end(), max(match.end(), len(text.rstrip()))
    return None


def _administrative_parts(text: str) -> list[tuple[str, int, int]]:
    """Recognize only the existing act/seal rows, including after the marker."""
    act = re.match(r"\s*Акт\s+відбору\s+зразків\s*(?::\s*(?:№\s*)?|№\s*)", text, re.I)
    if act:
        end = len(text.rstrip(". \t"))
        separator = re.search(r"\s+від\s+", text[act.end():end], re.I)
        if separator:
            start = act.end() + separator.start()
            return [("act_number", act.end(), start),
                    ("act_date", act.end() + separator.end(), end)]
        return [("act_number", act.end(), max(act.end(), end))]
    seal = re.match(r"\s*Номер\s+пломби(?:\s+зразка)?\s*:\s*", text, re.I)
    if seal:
        return [("seal_number", seal.end(), max(seal.end(), len(text.rstrip(". \t"))))]
    weight = re.match(r"\s*(?:Вага|Маса)\s+зразка\s*:\s*", text, re.I)
    if weight:
        return [("sample_weight", weight.end(), max(weight.end(), len(text.rstrip(". \t"))))]
    return []


def _sample_table_parts(para: _Paragraph) -> list[tuple[str, int, int]]:
    """Only the sample identity row inside a table, retaining its separator."""
    if not para.in_table:
        return []
    match = re.match(r"\s*№\s*(?P<number>[^\s–—]+)\s*(?:[–—]|\s-\s)\s*(?P<name>\S(?:.*?\S)?)\s*$", para.text)
    if not match:
        return []
    return [("protocol_number", match.start("number"), match.end("number")),
            ("product_name", match.start("name"), match.end("name"))]


def _purpose_parts(text: str) -> list[tuple[str, int, int]]:
    label = re.match(r"\s*Мета\s+випробувань\s+зразка\s*:\s*.*?\bа\s+саме\s*:\s*", text, re.I)
    if not label:
        return []
    tail = re.search(r"\s+на\s+відповідність\b", text[label.end():], re.I)
    return [("product_name", label.end(), label.end() + tail.start())] if tail else []


def _conclusion_parts(text: str) -> list[tuple[str, int, int]]:
    prefix = re.match(r"\s*Висновок\s*:\s*Представлений\s+зразок\s*:\s*№?\s*", text, re.I)
    if not prefix:
        return []
    batch = re.search(r"\s*,?\s*відібраний\s+від\s+партії\s*:\s*", text[prefix.end():], re.I)
    if not batch:
        return []
    number_end = prefix.end() + batch.start()
    number_end = len(text[:number_end].rstrip(" ,"))
    batch_start = prefix.end() + batch.end()
    tail = re.search(r"\s+за\s+вище\s+вказаними\b", text[batch_start:], re.I)
    if not tail:
        return []
    batch_end = batch_start + tail.start()
    # The comma and spacing introduce the preserved assessment, not the batch.
    batch_end = len(text[:batch_end].rstrip(" ,"))
    return [("protocol_number", prefix.end(), number_end),
            ("batch_size", batch_start, batch_end)]


def _identification_parts(text: str) -> tuple[int, int, str, str] | None:
    match = re.match(r"\s*" + re.escape(_IDENTIFICATION) + r"\s*", text, re.I)
    if not match:
        return None
    rest = text[match.end():]
    # Number-internal hyphens are never a separator; Ukrainian protocol
    # examples use an en dash, sometimes without a space after the number.
    values = re.match(r"№?\s*(.*?)\s*(?:[–—]|\s-\s)\s*(.+?)\s*$", rest)
    if not values:
        raise TemplateError("Не розпізнано номер і назву в рядку ідентифікації зразка.")
    return match.end(), len(text.rstrip()), values.group(1).strip(), values.group(2).strip()


def _load(data: bytes) -> _Document:
    try:
        with ZipFile(BytesIO(data)) as archive:
            if len(archive.infolist()) > 3000 or sum(i.file_size for i in archive.infolist()) > 80 * 1024 * 1024:
                raise TemplateError("Word-документ надто великий після розпакування (максимум 80 МБ).")
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise TemplateError("Word-документ містить дублікати ZIP-частин.")
            xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
        raise TemplateError("Завантажте справжній файл Word у форматі .docx.") from exc
    paragraphs = _read_xml(xml)
    fields: dict[str, str] = {}
    available: set[str] = set()
    title_seen = False
    date_found = False
    for para in paragraphs:
        for key, a, b in _administrative_parts(para.text):
            available.add(key)
            if not _PLACEHOLDER.search(para.text[a:b]):
                fields[key] = para.text[a:b]
        for placeholder in _PLACEHOLDER.finditer(para.text):
            if placeholder.group(1) in _ADMIN_FIELDS:
                available.add(placeholder.group(1))
        text = para.editable
        if not text:
            continue
        for placeholder in _PLACEHOLDER.finditer(text):
            key = placeholder.group(1)
            if key not in FIELD_LABELS:
                raise TemplateError(f"Невідоме поле шаблону: {{{{{key}}}}}.")
            available.add(key)
        parts = _legacy_parts(text)
        if parts:
            key, a, b = parts
            available.add(key)
            if not _PLACEHOLDER.search(text[a:b]):
                fields[key] = text[a:b]
        ident = _identification_parts(text)
        if ident:
            _, _, number, name = ident
            available.update(("protocol_number", "product_name"))
            if not _PLACEHOLDER.search(number):
                fields["protocol_number"] = number
            if not _PLACEHOLDER.search(name):
                fields["product_name"] = name
        title = _TITLE_RE.search(text)
        if title:
            title_seen = True
            available.add("protocol_number")
            if not _PLACEHOLDER.search(title.group(1)):
                fields.setdefault("protocol_number", title.group(1).lstrip("№").strip())
        elif title_seen and not date_found and _DATE_RE.fullmatch(text.strip()):
            fields["protocol_date"] = text.strip()
            available.add("protocol_date")
            date_found = True
    required = {"protocol_number", "product_name"}
    if not required <= available:
        missing = ", ".join(FIELD_LABELS[k] for k in sorted(required - available))
        raise TemplateError(f"У шаблоні не знайдені обов’язкові поля: {missing}. Використайте підписи з прикладів або {{{{protocol_number}}}} і {{{{product_name}}}}.")
    return _Document(data=data, xml=xml, paragraphs=paragraphs, fields=fields, available=available)


def inspect_template(data: bytes) -> dict:
    """Validate a template and report sample data and supported cover fields.

    Returned sample values describe an existing document only; callers should
    never silently copy these historical facts into a new protocol.
    """
    doc = _load(data)
    return {
        "product_name": doc.fields.get("product_name", ""),
        "fields": doc.fields,
        "available_fields": [key for key in FIELD_LABELS if key in doc.available],
        "missing_fields": [key for key in FIELD_LABELS if key not in doc.available],
        "stop_phrase": STOP_PHRASE,
    }


def _parse_date(value: str, label: str) -> date:
    value = value.strip().rstrip(".").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    numeric = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", value)
    words = re.fullmatch(r"[«\"']?(\d{1,2})[»\"']?\s+(\w+)\s+(\d{4})\s+року", value, re.I)
    try:
        if numeric:
            day, month, year = map(int, numeric.groups())
            return date(year, month, day)
        if words and words.group(2).lower() in _MONTHS:
            return date(int(words.group(3)), _MONTHS.index(words.group(2).lower()) + 1, int(words.group(1)))
    except ValueError:
        pass
    raise TemplateError(f"Некоректна дата {label}. Укажіть день, місяць і рік, наприклад 29.10.2026.")


def format_protocol_date(value: str) -> str:
    """Render the requested Ukrainian date format independently of system locale."""
    day = _parse_date(str(value), "протоколу")
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year} року"


def _value(fields: dict, key: str) -> str:
    value = str(fields.get(key) or "").strip()
    if not value:
        return MISSING_VALUE
    if len(value) > 15000:
        raise TemplateError(f"Поле «{FIELD_LABELS[key]}» задовге.")
    if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        raise TemplateError(f"Поле «{FIELD_LABELS[key]}» містить недопустимі символи.")
    # A protocol cover field is a paragraph; preserve the original paragraph
    # structure by representing pasted line breaks as ordinary spaces.
    value = re.sub(r"[\r\n\t]+", " ", value)
    if key in {"protocol_number", "act_number"}:
        value = value.lstrip("№").strip()
    if key == "seal_number":
        from .sample_details import first_seal_number
        return first_seal_number(value) or MISSING_VALUE
    if key == "protocol_date":
        return format_protocol_date(value)
    if key == "act_date":
        return _parse_date(value, "акта відбору зразків").strftime("%d.%m.%Y")
    return value


def _trust_replacement(trusted: dict[int, list[tuple[int, int]]], node: _Text,
                       start: int, end: int, replacement: str) -> None:
    """Keep trusted coordinates correct while ranges are applied right-to-left."""
    delta = len(replacement) - (end - start)
    ranges = []
    for left, right in trusted.get(id(node), []):
        if left < start:
            ranges.append((left, min(right, start)))
        if right > end:
            ranges.append((max(left, end) + delta, right + delta))
    if replacement:
        ranges.append((start, start + len(replacement)))
    trusted[id(node)] = sorted((left, right) for left, right in ranges if right > left)


def _replace_range(para: _Paragraph, values: dict[int, str], start: int, end: int, replacement: str,
                   trusted: dict[int, list[tuple[int, int]]] | None = None) -> None:
    """Replace a paragraph character range while retaining every original run."""
    offset = 0
    inserted = False
    for node in para.nodes:
        node_start, node_end = offset, offset + len(node.text)
        offset = node_end
        if node_end <= start or node_start >= end:
            continue
        old = values.get(id(node), node.text)
        a, b = max(start - node_start, 0), min(end - node_start, len(node.text))
        # Apply ranges right-to-left so original coordinates remain valid.
        inserted_text = replacement if not inserted else ""
        values[id(node)] = old[:a] + inserted_text + old[b:]
        if trusted is not None:
            _trust_replacement(trusted, node, a, b, inserted_text)
        inserted = True
    if not inserted and start == end:
        # Empty original label value: add into the preceding text node.
        offset = 0
        for node in para.nodes:
            offset += len(node.text)
            if offset >= start and not node.empty:
                at = start - (offset - len(node.text))
                old = values.get(id(node), node.text)
                values[id(node)] = old[:at] + replacement + old[at:]
                if trusted is not None:
                    _trust_replacement(trusted, node, at, at, replacement)
                return


def _review_colors(xml: bytes, paragraphs: list[_Paragraph], trusted: dict[int, list[tuple[int, int]]]) -> bytes:
    from .document_review import ReviewColorError, color_review_xml
    nodes = sorted((node for para in paragraphs for node in para.nodes), key=lambda node: node.start)
    ordinals = {index: trusted[id(node)] for index, node in enumerate(nodes) if id(node) in trusted}
    try:
        return color_review_xml(xml, ordinals)
    except ReviewColorError as exc:
        raise TemplateError("Не вдалося позначити неперевірені відомості червоним: " + str(exc)) from exc


def _text_offsets(raw: bytes, expected: str) -> list[int]:
    """Map parsed character offsets to the original escaped UTF-8 byte offsets.

    Keeping an unchanged suffix in its original lexical form matters when a
    placeholder and the protected marker happen to share a single Word run.
    """
    source = raw.decode("utf-8")
    offsets = [0]
    decoded = []
    position = 0
    for match in re.finditer(r"&[^;]+;|\r\n|\r|[\s\S]", source):
        token = match.group(0)
        value = unescape(token) if token.startswith("&") else token.replace("\r\n", "\n").replace("\r", "\n")
        position += len(token.encode("utf-8"))
        if len(value) != 1:
            raise TemplateError("Непідтримуване кодування текстового XML-вузла.")
        decoded.append(value)
        offsets.append(position)
    if "".join(decoded) != expected:
        raise TemplateError("Не вдалося безпечно змінити текстовий XML-вузол.")
    return offsets


def render_protocol(data: bytes, fields: dict[str, str]) -> bytes:
    """Return a complete DOCX, replacing only editable cover data.

    Empty/missing inputs are explicitly marked [НЕ ЗАПОВНЕНО]. Existing
    Laboratory results stay untouched. The existing act/seal rows after the
    marker and repeated numbers in headers/footers are explicit exceptions.
    """
    doc = _load(data)
    replacements = {key: _value(fields, key) for key in FIELD_LABELS}
    old_number = doc.fields.get("protocol_number", "")
    values: dict[int, str] = {}
    trusted: dict[int, list[tuple[int, int]]] = {}
    title_seen = False
    date_changed = False
    for para in doc.paragraphs:
        text = para.editable
        administrative = _administrative_parts(para.text)
        repeated = _sample_table_parts(para) or _purpose_parts(para.text) or _conclusion_parts(para.text)
        admin_placeholders = [placeholder for placeholder in _PLACEHOLDER.finditer(para.text)
                              if placeholder.group(1) in _ADMIN_FIELDS]
        if not text and not administrative and not repeated and not admin_placeholders:
            continue
        edits: list[tuple[int, int, str]] = []
        parts = _legacy_parts(text)
        ident = _identification_parts(text)
        placeholders = list(_PLACEHOLDER.finditer(text))
        title = _TITLE_RE.search(text)
        if administrative:
            for key, a, b in administrative:
                replacement = replacements[key]
                # Existing weight rows already own their final sentence dot.
                # A formatted input may include the same dot for readability.
                if key == "sample_weight" and para.text[b:].lstrip().startswith("."):
                    replacement = replacement.rstrip(". ")
                edits.append((a, b, replacement))
        elif repeated:
            edits.extend((a, b, replacements[key].rstrip(". ") if key == "batch_size" else replacements[key])
                         for key, a, b in repeated)
        elif not text:
            edits.extend((m.start(), m.end(), replacements[m.group(1)]) for m in admin_placeholders)
        elif _HISTORICAL_REVISION_RE.match(text):
            # An original sample may be a replacement for a previous report.
            # A new act supplies no authorization to invalidate that historical
            # report. Clear this administrative fact, retaining its paragraph
            # and run layout, rather than carrying it into every new protocol.
            edits.append((0, len(text), ""))
        elif placeholders:
            edits.extend((m.start(), m.end(), replacements[m.group(1)]) for m in placeholders)
        elif parts:
            key, a, b = parts
            edits.append((a, b, replacements[key]))
        elif ident:
            a, b, _, _ = ident
            edits.append((a, b, f"№{replacements['protocol_number']} – {replacements['product_name']}"))
        elif title:
            edits.append((title.start(1), title.end(1), replacements["protocol_number"]))
        elif title_seen and not date_changed and _DATE_RE.fullmatch(text.strip()):
            a = len(text) - len(text.lstrip())
            edits.append((a, len(text.rstrip()), replacements["protocol_date"]))
            date_changed = True
        elif old_number:
            # This also handles the small duplicate protocol number on the
            # cover, without touching numbers in the immutable test section.
            edits.extend((m.start(), m.end(), replacements["protocol_number"])
                         for m in re.finditer(re.escape(old_number), text))
        if title:
            title_seen = True
        if text and not administrative and not repeated:
            edits.extend((m.start(), m.end(), replacements[m.group(1)]) for m in admin_placeholders
                         if m.start() >= para.limit)
        for a, b, replacement in sorted(edits, reverse=True):
            _replace_range(para, values, a, b, replacement, trusted)

    xml = _apply_text_values(doc.xml, doc.paragraphs, values)
    # Parse once more to ensure user text did not produce an invalid XML part.
    _read_xml(xml)
    xml = _review_colors(xml, doc.paragraphs, trusted)
    output = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(output, "w") as target:
        target.comment = source.comment
        for info in source.infolist():
            if info.filename == "word/document.xml":
                content = xml
            elif re.fullmatch(r"word/(?:header|footer)[^/]*\.xml", info.filename):
                content = _render_repeated_number(source.read(info.filename), replacements["protocol_number"], old_number)
            elif info.filename in {"word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}:
                content = source.read(info.filename)
                note_paragraphs = _read_xml(content, require_marker=False)
                if any(node.text for paragraph in note_paragraphs for node in paragraph.nodes):
                    content = _review_colors(content, note_paragraphs, {})
            else:
                content = source.read(info.filename)
            target.writestr(info, content)
    return output.getvalue()


def _apply_text_values(xml: bytes, paragraphs: list[_Paragraph], values: dict[int, str]) -> bytes:
    """Patch the smallest changed text ranges without reserializing XML."""
    byte_edits: list[tuple[int, int, bytes]] = []
    for para in paragraphs:
        for node in para.nodes:
            value = values.get(id(node))
            if value is None or value == node.text:
                continue
            if node.empty:
                raise TemplateError("Неможливо безпечно заповнити порожній XML-вузол шаблону.")
            # Change the smallest differing text range. In particular, do not
            # normalize entity spelling in an immutable suffix of this run.
            left = 0
            while left < min(len(node.text), len(value)) and node.text[left] == value[left]:
                left += 1
            right = 0
            while (right < min(len(node.text) - left, len(value) - left)
                   and node.text[len(node.text) - right - 1] == value[len(value) - right - 1]):
                right += 1
            offsets = _text_offsets(xml[node.start:node.end], node.text)
            old_end = len(node.text) - right
            new_end = len(value) - right
            byte_edits.append((node.start + offsets[left], node.start + offsets[old_end],
                               escape(value[left:new_end]).encode("utf-8")))
            if value and (value[0].isspace() or value[-1].isspace()) and not node.preserve_space:
                # Spaces at run boundaries would otherwise be discarded by
                # Word after a value is moved into the first original run.
                opening = xml[node.tag_start:node.tag_end]
                if b"xml:space=" in opening:
                    updated = re.sub(br"xml:space\s*=\s*(['\"]).*?\1", b'xml:space="preserve"', opening)
                else:
                    updated = opening[:-1] + b' xml:space="preserve">'
                byte_edits.append((node.tag_start, node.tag_end, updated))
    for a, b, replacement in sorted(byte_edits, reverse=True):
        xml = xml[:a] + replacement + xml[b:]
    return xml


def _render_repeated_number(xml: bytes, replacement: str, old_number: str) -> bytes:
    paragraphs = _read_xml(xml, require_marker=False)
    values: dict[int, str] = {}
    trusted: dict[int, list[tuple[int, int]]] = {}
    for para in paragraphs:
        edits: list[tuple[int, int, str]] = []

        def add(start: int, end: int):
            if not any(start < b and end > a for a, b, _ in edits):
                edits.append((start, end, replacement))

        for placeholder in _PLACEHOLDER.finditer(para.text):
            if placeholder.group(1) == "protocol_number":
                add(placeholder.start(), placeholder.end())
        for match in _PART_NUMBER_RE.finditer(para.text):
            add(match.start("number"), match.end("number"))
        if old_number:
            for match in re.finditer(r"(?<![\w-])" + re.escape(old_number) + r"(?![\w-])", para.text):
                add(match.start(), match.end())
        for start, end, value in sorted(edits, reverse=True):
            _replace_range(para, values, start, end, value, trusted)
    result = _apply_text_values(xml, paragraphs, values)
    _read_xml(result, require_marker=False)
    return _review_colors(result, paragraphs, trusted)
