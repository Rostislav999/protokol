"""Fill assignment forms while retaining their original Word layout and methods.

Both supplied layouts are supported: labelled paragraphs and label/value table
rows. Five sample fields and an explicitly selected issuing person are editable.
Page counts in headers/footers become live Word fields; unrelated parts stay intact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
import re
from xml.parsers import expat
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZipFile

from .documents import MISSING_VALUE, TemplateError, _Paragraph, _Text, _replace_range, _tag_end, _text_offsets
from .word_pagination import pagination_updates

FIELD_LABELS = {
    "sample_number": "Реєстраційний/ідентифікаційний номер зразка",
    "product_name": "Назва зразка",
    "sample_weight": "Вага та кількість зразка",
    "standard": "Випробування на відповідність НД",
    "issue_date": "Дата видачі завдання",
}
_LABELS = {
    "sample_number": r"Реєстраційний\s*/\s*ідентифікаційний\s+номер\s+зразка",
    "product_name": r"Назва\s+зразка",
    "sample_weight": r"Вага\s*(?:та\s+кількість|\(\s*кількість\s*\))\s+зразка",
    "standard": r"Випробування\s+на\s+відповідність\s+НД",
    "issue_date": r"Дата\s+видачі\s+завдання",
}
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_ISSUER_FIELDS = {"issuer_name", "issuer_position"}
_ISSUER_LABEL = re.compile(r"\s*(?:Сформулював\s+та\s+видав\s+завдання|Видав)\s*:\s*", re.I)
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class _Para(_Paragraph):
    tag_start: int = 0
    tag_end: int = 0
    close_start: int = 0
    empty: bool = False


@dataclass
class _Cell:
    paragraphs: list[_Para] = field(default_factory=list)


@dataclass
class _Target:
    key: str
    paragraphs: list[_Para]
    start: int = 0
    end: int | None = None
    separator: str = " "


@dataclass
class _Assignment:
    xml: bytes
    paragraphs: list[_Para]
    targets: list[_Target]
    fields: dict[str, str]
    department: str
    issuer_targets: list[_Target] = field(default_factory=list)


def _parse(xml: bytes) -> tuple[list[_Para], list[list[_Cell]]]:
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise TemplateError("XML із DTD або зовнішніми сутностями не підтримується.")
    parser = expat.ParserCreate(namespace_separator="}")
    paragraphs: list[_Para] = []
    rows: list[list[_Cell]] = []
    para_stack: list[_Para] = []
    row_stack: list[list[_Cell]] = []
    cell_stack: list[_Cell] = []
    current: _Text | None = None

    def start(name, attrs):
        nonlocal current
        offset = parser.CurrentByteIndex
        if name == _W + "tr":
            row: list[_Cell] = []
            rows.append(row)
            row_stack.append(row)
        elif name == _W + "tc":
            cell = _Cell()
            if row_stack:
                row_stack[-1].append(cell)
            cell_stack.append(cell)
        elif name == _W + "p":
            end = _tag_end(xml, offset)
            para = _Para(tag_start=offset, tag_end=end, empty=xml[offset:end].rstrip().endswith(b"/>"))
            paragraphs.append(para)
            para_stack.append(para)
            if cell_stack:
                cell_stack[-1].paragraphs.append(para)
        elif name == _W + "t" and para_stack:
            end = _tag_end(xml, offset)
            current = _Text(start=end, tag_start=offset, tag_end=end,
                            preserve_space=attrs.get("http://www.w3.org/XML/1998/namespace}space") == "preserve",
                            empty=xml[offset:end].rstrip().endswith(b"/>"))
            para_stack[-1].nodes.append(current)

    def chars(value):
        if current is not None:
            current.text += value

    def end(name):
        nonlocal current
        if name == _W + "t" and current is not None:
            current.end = current.start if current.empty else parser.CurrentByteIndex
            current = None
        elif name == _W + "p" and para_stack:
            para_stack[-1].close_start = parser.CurrentByteIndex
            para_stack.pop()
        elif name == _W + "tc" and cell_stack:
            cell_stack.pop()
        elif name == _W + "tr" and row_stack:
            row_stack.pop()

    parser.StartElementHandler = start
    parser.CharacterDataHandler = chars
    parser.EndElementHandler = end
    try:
        parser.Parse(xml, True)
    except expat.ExpatError as exc:
        raise TemplateError("Пошкоджений XML Word-шаблону завдання.") from exc
    return paragraphs, rows


def _label(text: str):
    for key, pattern in _LABELS.items():
        match = re.match(r"\s*" + pattern + r"\s*(?::\s*|$)", text, re.I)
        if match:
            return key, match.end()
    return None


def _clean_sample_value(key: str, value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value.rstrip(". ") if key in {"sample_number", "issue_date", "standard", "sample_weight"} else value


def _load(data: bytes) -> _Assignment:
    try:
        with ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 3000 or sum(i.file_size for i in members) > 80 * 1024 * 1024:
                raise TemplateError("Word-шаблон завдання надто великий після розпакування.")
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise TemplateError("Word-шаблон містить дублікати ZIP-частин.")
            xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
        raise TemplateError("Завантажте справжній Word-шаблон завдання у форматі .docx.") from exc
    paragraphs, rows = _parse(xml)
    targets: list[_Target] = []
    issuer_targets: list[_Target] = []
    fields: dict[str, str] = {}
    table_labels: set[int] = set()
    for row in rows:
        if len(row) < 2:
            continue
        label_text = " ".join(p.text.strip() for p in row[0].paragraphs).strip()
        match = _label(label_text)
        if match and not label_text[match[1]:].strip():
            key, _ = match
            value_paragraphs = row[1].paragraphs
            if not value_paragraphs:
                raise TemplateError(f"У таблиці немає комірки для поля «{FIELD_LABELS[key]}».")
            targets.append(_Target(key, value_paragraphs))
            fields[key] = _clean_sample_value(key, " ".join(p.text for p in value_paragraphs))
            table_labels.update(id(p) for p in row[0].paragraphs)

    targeted_paras = {id(p) for t in targets for p in t.paragraphs}
    for para in paragraphs:
        if id(para) in table_labels or id(para) in targeted_paras:
            continue
        placeholders = list(_PLACEHOLDER.finditer(para.text))
        for placeholder in placeholders:
            key = placeholder.group(1)
            if key in _ISSUER_FIELDS:
                issuer_targets.append(_Target(key, [para], placeholder.start(), placeholder.end()))
                continue
            if key not in FIELD_LABELS:
                raise TemplateError(f"Невідоме поле шаблону завдання: {{{{{key}}}}}.")
            targets.append(_Target(key, [para], placeholder.start(), placeholder.end()))
            fields.setdefault(key, "")
        if placeholders:
            continue
        match = _label(para.text)
        if match:
            key, start = match
            targets.append(_Target(key, [para], start, max(start, len(para.text.rstrip()))))
            fields[key] = _clean_sample_value(key, para.text[start:])

    # Placeholder values inside a mapped table cell describe fields, not facts.
    for target in list(targets):
        if target.end is not None:
            continue
        for para in target.paragraphs:
            for placeholder in _PLACEHOLDER.finditer(para.text):
                if placeholder.group(1) != target.key:
                    raise TemplateError("Метка в комірці завдання не відповідає назві поля.")
                fields[target.key] = ""
    required = {"sample_number", "product_name", "standard", "issue_date"}
    found = {target.key for target in targets}
    if not required <= found:
        missing = ", ".join(FIELD_LABELS[key] for key in sorted(required - found))
        raise TemplateError("У шаблоні завдання відсутні поля: " + missing)

    # The issuing person is distinct from the department's receiving officer.
    # Supplied forms either put rank/name after the label on a separate line,
    # or use an inline "Видав:" paragraph. Keep their original spacing and runs.
    explicit_issuer_paras = {id(p) for target in issuer_targets for p in target.paragraphs}
    for index, para in enumerate(paragraphs):
        match = _ISSUER_LABEL.match(para.text)
        if not match or id(para) in explicit_issuer_paras:
            continue
        value_para, start = para, match.end()
        if not para.text[start:].strip():
            value_para = next((candidate for candidate in paragraphs[index + 1:]
                               if candidate.text.strip()), None)
            if value_para is None or re.match(r"\s*(?:Представник\s+відділення|Отримав|Дата\s+видачі)", value_para.text, re.I):
                continue
            start = len(value_para.text) - len(value_para.text.lstrip())
        if id(value_para) in explicit_issuer_paras:
            continue
        end = len(value_para.text.rstrip())
        gap = re.search(r"[ \t]{2,}", value_para.text[start:end])
        issuer_targets.append(_Target("issuer_signature", [value_para], start, end,
                                      gap.group() if gap else " "))

    header = "\n".join(p.text for p in paragraphs[:12]).lower()
    if "бактеріологіч" in header:
        department = "bacteriology"
    elif "хімікотоксиколог" in header or "хіміко-токсиколог" in header:
        department = "chemistry"
    elif "ветеринарно-санітар" in header:
        department = "vet"
    else:
        department = ""
    return _Assignment(xml, paragraphs, targets, fields, department, issuer_targets)


def inspect_assignment_template(data: bytes) -> dict:
    """Report the form's department, sample name and normative document."""
    doc = _load(data)
    return {"department": doc.department, "product_name": doc.fields.get("product_name", ""),
            "standard": doc.fields.get("standard", ""), "sample_weight": doc.fields.get("sample_weight", ""),
            "fields": doc.fields, "available_fields": [key for key in FIELD_LABELS if key in doc.fields]}


def _value(fields: dict, key: str, doc: _Assignment) -> str:
    # Normative documents define the fixed methods in a selected form. Reuse
    # this structural template value only when the caller does not override it.
    source = fields.get(key, doc.fields.get(key, "") if key == "standard" else "")
    value = str(source or "").strip()
    if not value:
        return MISSING_VALUE
    if len(value) > 15000 or any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        raise TemplateError(f"Некоректне значення поля «{FIELD_LABELS[key]}».")
    value = re.sub(r"[\r\n\t]+", " ", value)
    if key == "sample_number":
        value = re.sub(r"^(?:№\s*)+", "", value).strip()
        if not value:
            raise TemplateError("Вкажіть номер зразка.")
    if key == "issue_date":
        try:
            d = date.fromisoformat(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else datetime.strptime(value, "%d.%m.%Y").date()
        except ValueError as exc:
            raise TemplateError("Некоректна дата видачі завдання.") from exc
        value = d.strftime("%d.%m.%Y")
    return value


def _set_paragraph(para: _Para, value: str, values: dict[int, str], insertions: dict[int, str]) -> None:
    if para.nodes:
        # Retain all run formatting. Put the value into the first original run
        # and clear subsequent runs/paragraphs in a multi-line table cell.
        first = True
        for node in para.nodes:
            values[id(node)] = value if first else ""
            first = False
    elif value:
        insertions[id(para)] = value


def render_assignment(data: bytes, fields: dict[str, str]) -> bytes:
    """Fill sample identity, weight, ND and date; retain tests and signatures."""
    doc = _load(data)
    replacements = {key: _value(fields, key, doc) for key in FIELD_LABELS}
    selected_issuer = any(key in fields for key in _ISSUER_FIELDS)
    if selected_issuer:
        if not doc.issuer_targets:
            raise TemplateError("У шаблоні немає рядка видавця завдання. Додайте «Видав:» або метки {{issuer_name}} і {{issuer_position}}.")
        for key in _ISSUER_FIELDS:
            value = str(fields.get(key) or "").strip()
            if len(value) > 500 or any(ord(c) < 32 for c in value):
                raise TemplateError("Некоректне ім’я або посада особи, яка видає завдання.")
            replacements[key] = value
        if not replacements["issuer_name"]:
            raise TemplateError("Укажіть ім’я особи, яка сформулювала та видала завдання.")
    values: dict[int, str] = {}
    insertions: dict[int, str] = {}
    # Ranges in a paragraph use original positions and therefore run right-to-left.
    targets = doc.targets + (doc.issuer_targets if selected_issuer else [])
    for target in sorted(targets, key=lambda t: (t.paragraphs[0].tag_start, t.start), reverse=True):
        if target.key == "issuer_signature":
            position = replacements["issuer_position"]
            replacement = (position + target.separator if position else "") + replacements["issuer_name"]
        else:
            replacement = replacements[target.key]
        if target.end is None:
            for i, para in enumerate(target.paragraphs):
                _set_paragraph(para, replacement if i == 0 else "", values, insertions)
        else:
            _replace_range(target.paragraphs[0], values, target.start, target.end, replacement)

    edits: list[tuple[int, int, bytes]] = []
    for para in doc.paragraphs:
        inserted = insertions.get(id(para))
        if inserted is not None:
            opening = doc.xml[para.tag_start:para.tag_end]
            qname = re.match(br"<([^\s/>]+)", opening).group(1)
            prefix = qname.rsplit(b":", 1)[0] + b":" if b":" in qname else b""
            content = b"<" + prefix + b"r><" + prefix + b't xml:space="preserve">' + escape(inserted).encode() + b"</" + prefix + b"t></" + prefix + b"r>"
            if para.empty:
                edits.append((para.tag_start, para.tag_end, opening[:-2] + b">" + content + b"</" + qname + b">"))
            else:
                edits.append((para.close_start, para.close_start, content))
        for node in para.nodes:
            value = values.get(id(node))
            if value is None or value == node.text:
                continue
            if node.empty:
                opening = doc.xml[node.tag_start:node.tag_end]
                qname = re.match(br"<([^\s/>]+)", opening).group(1)
                edits.append((node.tag_start, node.tag_end, opening[:-2] + b">" + escape(value).encode() + b"</" + qname + b">"))
                continue
            left = 0
            while left < min(len(node.text), len(value)) and node.text[left] == value[left]:
                left += 1
            right = 0
            while right < min(len(node.text) - left, len(value) - left) and node.text[-right-1] == value[-right-1]:
                right += 1
            offsets = _text_offsets(doc.xml[node.start:node.end], node.text)
            edits.append((node.start + offsets[left], node.start + offsets[len(node.text)-right],
                          escape(value[left:len(value)-right]).encode()))
            if value and (value[0].isspace() or value[-1].isspace()) and not node.preserve_space:
                opening = doc.xml[node.tag_start:node.tag_end]
                if b"xml:space=" in opening:
                    opening = re.sub(br"xml:space\s*=\s*(['\"]).*?\1", b'xml:space="preserve"', opening)
                else:
                    opening = opening[:-1] + b' xml:space="preserve">'
                edits.append((node.tag_start, node.tag_end, opening))
    xml = doc.xml
    for start, end, replacement in sorted(edits, reverse=True):
        xml = xml[:start] + replacement + xml[end:]
    _parse(xml)
    output = BytesIO()
    with ZipFile(BytesIO(data)) as original, ZipFile(output, "w") as target:
        target.comment = original.comment
        parts = {member.filename: original.read(member) for member in original.infolist()}
        updates = pagination_updates(parts)
        updates['word/document.xml'] = xml
        for member in original.infolist():
            target.writestr(member, updates.get(member.filename, parts[member.filename]))
    return output.getvalue()
