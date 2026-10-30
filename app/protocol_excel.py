"""Protocol → Excel core adapted from Rostislav999/- at b6f448e.

The integrated workflow fills D–N only. Existing O/P compliance cells and all
service metadata are retained without interpreting or writing compliance data.
"""
from __future__ import annotations

import posixpath
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from lxml import etree as ET

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"s": S}
FIELDS = [
    ("D", "Номер протоколу"),
    ("E", "Дата протоколу"),
    ("F", "Код каталогу ХП (Товар)"),
    ("G", "Назва продукту (маркування)"),
    ("H", "Партія (позначення)"),
    ("I", "Дата виготовлення"),
    ("J", 'Дата "Вжити до"'),
    ("K", "Партія (кг)"),
    ("L", "Маса нетто одиниці (кг)"),
    ("M", "Постачальник"),
    ("N", "Виробник"),
]
LABELS = dict(FIELDS)
DATES = {"E", "I", "J"}
NUMBERS = {"K", "L"}
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 150 * 1024 * 1024
MAX_PARTS = 3000
NUMBER = r"\d+(?:[-–—]\d+){2,}"
NAME_SEPARATOR = r"(?:\s+[-–—:]\s*|\s*[-–—:]\s+)"
MONTHS = dict(zip(
    "січня лютого березня квітня травня червня липня серпня вересня жовтня листопада грудня".split(),
    range(1, 13),
))


def xml(data: bytes):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML із DTD або зовнішніми сутностями не підтримується.")
    try:
        return ET.fromstring(data, ET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    except ET.XMLSyntaxError as exc:
        raise ValueError("Пошкоджений XML у файлі Word або Excel.") from exc


def _parts(data: bytes) -> tuple[dict[str, bytes], list[zipfile.ZipInfo]]:
    if not data or len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Файл порожній або перевищує 25 МБ.")
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PARTS or sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
                raise ValueError("Файл надто великий після розпакування.")
            if len({info.filename for info in infos}) != len(infos):
                raise ValueError("Файл містить дублікати ZIP-частин.")
            if any(info.flag_bits & 1 for info in infos):
                raise ValueError("Файл захищений паролем.")
            return {info.filename: archive.read(info) for info in infos}, infos
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
        raise ValueError("Завантажте справжній файл DOCX або XLSX.") from exc


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def key(value: str) -> str:
    return clean(value).casefold()


def protocol_key(value: str) -> str:
    return re.sub(r"[\s№]", "", value).replace("–", "-").replace("—", "-")


@dataclass
class Record:
    source: str
    values: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def missing(self) -> list[str]:
        return [label for col, label in FIELDS if not self.values.get(col, "").strip()]



BLOCK_STOP = re.compile(
    r"^(?:Замовник|Номер за Каталогом|Код каталогу|Ідентифікаційний номер|"
    r"Зразки харчових|Виробник|Постачальник|Договір|Дата |Розмір партії|"
    r"Вищезазначена|Акт відбору|Номер пломби|Вага зразка|Стан зразка|"
    r"Мета випробувань|Термін проведення|Умови проведення|Відбір зразків|"
    r"Висновок|Рекомендації|Примітки|Партія|Позначення партії|Номер партії|"
    r"Маса нетто|Відповідність описам|Вжити до|Кінцева дата)", re.I,
)


def find_value(text: str, label: str, multiline: bool = False) -> str:
    match = re.search(rf"(?im)^[ \t]*(?:{label})[ \t]*:[ \t]*([^\n]*)", text)
    if not match:
        return ""
    value = match[1]
    if multiline:
        for line in text[match.end():].splitlines():
            line = line.strip()
            if BLOCK_STOP.match(line):
                break
            value += " " + line
    return clean(value)


def meaningful(value: str) -> str:
    if re.fullmatch(r"\s*\[\s*НЕ\s+ЗАПОВНЕНО\s*\]\s*[. ]*", value, re.I):
        return ""
    return value if re.search(r"[^\W_]", value, re.UNICODE) else ""


def supplier_text(value: str) -> str:
    """Keep the supplier's name and its own explicit code, including zeroes."""
    value = clean(value)
    labelled = r"(?:код\s*)?[ЄЕ]ДРПОУ\s*:?\s*(\d{8}(?:\d{2})?)(?!\d)"
    codes = set(re.findall(labelled, value, re.I))
    if not codes:
        codes = set(re.findall(r"(?<!\d)\d{8}(?!\d)", value))
    if len(codes) != 1:
        return value
    code = next(iter(codes))
    name = re.sub(labelled, "", value, flags=re.I)
    name = re.sub(r"(?<!\d)" + re.escape(code) + r"(?!\d)", "", name).strip(" ,;.")
    return (name + ", " if name else "") + "код ЄДРПОУ: " + code


def supplier_code(value: str) -> str:
    """Return only an unambiguous supplier code, preserving leading zeroes."""
    codes = set(re.findall(r"(?:код\s*)?[ЄЕ]ДРПОУ\s*:?\s*([0-9]{8}(?:[0-9]{2})?)(?![0-9])", value, re.I))
    if not codes:
        codes = {value.strip()} if re.fullmatch(r"[0-9]{8}(?:[0-9]{2})?", value.strip()) else set(re.findall(r"(?<![0-9])[0-9]{8}(?![0-9])", value))
    return next(iter(codes)) if len(codes) == 1 else ""


def date_text(value: str) -> str:
    match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", value)
    if match:
        return date(int(match[3]), int(match[2]), int(match[1])).strftime("%d.%m.%Y")
    match = re.search(r"[«\"“]?\s*(\d{1,2})\s*[»\"”]?\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", value.casefold())
    if match:
        return date(int(match[3]), MONTHS[match[2]], int(match[1])).strftime("%d.%m.%Y")
    return ""


def kg(value: str) -> str:
    match = re.search(r"(?<![\w.,])([0-9]+(?:[ \u00a0][0-9]{3})*(?:[,.][0-9]+)?)\s*(кг|kg|г|g)\b", value, re.I)
    if not match:
        return ""
    from decimal import Decimal

    amount = Decimal(re.sub(r"\s", "", match[1]).replace(",", "."))
    if match[2].casefold() in {"г", "g"}:
        amount /= 1000
    return format(amount, "f")



def extract_text(text: str, source: str = "") -> Record:
    text = "\n".join(clean(line) for line in text.splitlines() if clean(line))
    record = Record(source)
    v = record.values
    # OCR often reads the № symbol as е, Хе or similar. The digits remain unmodified.
    title_pattern = r"ПРОТОКОЛ\s+ВИПРОБУВАНЬ[^\d\n]*(" + NUMBER + ")"
    titles = re.findall(title_pattern, text)
    if not titles:
        titles = re.findall(title_pattern, text, re.I)
    ids = {protocol_key(t) for t in titles}
    if len(ids) > 1:
        raise ValueError("В файле несколько протоколов. Разделите их на отдельные файлы.")
    match = re.search(r"№\s*(" + NUMBER + ")", text)
    if not titles and not match:
        raise ValueError("Не найден номер протокола. Проверьте формат документа / распознавание PDF.")
    if not titles and not re.search(r"Ідентифікаційний\s+номер\s+та\s+назва\s+зразка\s*:|Висновок\s*:\s*Представлений\s+зразок", text, re.I):
        raise ValueError("Не знайдено заголовок або структуру протоколу випробувань. Акт відбору не є протоколом.")
    v["D"] = "№" + protocol_key(titles[0] if titles else match[1])
    title = re.search(r"ПРОТОКОЛ\s+ВИПРОБУВАНЬ[^\n]*\n([^\n]+)", text, re.I)
    v["E"] = date_text(title[1]) if title else ""
    catalog = find_value(text, r"Номер за Каталогом продуктів харчування|Код каталогу(?: ХП)?")
    code = re.search(r"\b\d{4,8}\b", catalog)
    v["F"] = code[0] if code else ""
    sample = find_value(text, r"Ідентифікаційний номер та назва зразка", multiline=True)
    sample_match = re.match(r"[^\d]*(" + NUMBER + ")" + NAME_SEPARATOR + r"(.+)", sample)
    v["G"] = sample_match[2].rstrip(". ") if sample_match else ""
    if sample_match and protocol_key(sample_match[1]) != protocol_key(v["D"]):
        record.warnings.append(f'В извлечённом тексте номер образца «{sample_match[1]}» отличается от номера заголовка «{v["D"]}». Сверьте с оригиналом: в сканах возможна ошибка OCR символа №.')
    table_names = re.findall(r"(?m)^№\s*(" + NUMBER + ")" + NAME_SEPARATOR + r"([^\n]+)$", text)
    names = {clean(name).rstrip(". ") for _, name in table_names}
    if not v["G"] and len(names) == 1:
        v["G"] = next(iter(names))
        record.warnings.append("Название взято из таблицы: поле идентификации образца не найдено.")
    for name in sorted(names):
        if v["G"] and key(name) != key(v["G"]):
            record.warnings.append(f'Разные названия продукта: в описании «{v["G"]}», в таблице «{name}». Проверьте исходный протокол.')
    for number, _ in table_names:
        if protocol_key(number) != protocol_key(v["D"]):
            record.warnings.append("В таблице испытаний найден другой номер образца.")
    v["H"] = meaningful(find_value(text, r"Партія\s*\(позначення\)|Позначення партії|Номер партії" )).rstrip(". ")
    manufactured = find_value(text, r"Дата виготовлення")
    expiry_in_manufacturing_field = bool(re.search(r"\bвжити\s+до\b", manufactured, re.I))
    v["I"] = "" if expiry_in_manufacturing_field else date_text(manufactured)
    # User's workflow: the batch designation is the manufacturing date.
    if v["I"]:
        if v["H"] and v["H"] != v["I"]:
            record.warnings.append("Обозначение партии из документа заменено датой изготовления по выбранному правилу. Проверьте поле партии.")
        v["H"] = v["I"]
    v["J"] = date_text(find_value(text, r'Дата\s*[«"“]?Вжити до[»"”]?|Вжити до|Кінцева дата споживання'))
    if not v["J"] and expiry_in_manufacturing_field:
        v["J"] = date_text(manufactured)
    v["K"] = kg(find_value(text, r"Розмір партії,?\s*від якої відібрано зразок|Партія\s*\(кг\)"))
    net = find_value(text, r"Маса нетто одиниці(?:\s*\(кг\))?|Маса нетто")
    v["L"] = kg(net)
    if not v["L"]:
        weight = find_value(text, r"Вага зразка")
        each = re.search(r"\bпо\s+(.+)", weight, re.I)
        if each:
            v["L"] = kg(each[1])
        elif re.match(r"1\s*(?:пак\.?\s*од\.?|упак\w*|од\.?)(?!\w)", weight, re.I):
            v["L"] = kg(weight)
            if v["L"]:
                record.warnings.append("Масса единицы взята из веса образца: указана одна упаковочная единица. Проверьте, что это масса нетто.")
    supplier = meaningful(find_value(text, r"Постачальник", multiline=True))
    v["M"] = supplier_text(supplier)
    if supplier and not re.search(r"(?:код\s*)?[ЄЕ]ДРПОУ\s*:?\s*\d{8}(?:\d{2})?", v["M"], re.I):
        record.warnings.append("Постачальник: код ЄДРПОУ не знайдено однозначно; збережено лише наведені відомості.")
    manufacturer = meaningful(find_value(text, r"Виробник", multiline=True))
    codes = set(re.findall(r"(?<!\d)\d{8}(?!\d)", manufacturer))
    v["N"] = next(iter(codes)) if len(codes) == 1 else ""
    if manufacturer and not v["N"]:
        record.warnings.append("Виробник: не знайдено однозначний код ЄДРПОУ з 8 цифр.")

    return record


def read_docx(data: bytes, source: str = "") -> Record:
    """Read only Word body text; formatting, footers and code never execute."""
    parts, _ = _parts(data)
    if "word/document.xml" not in parts:
        raise ValueError("У DOCX не знайдено основного тексту протоколу.")
    root = xml(parts["word/document.xml"])
    body = root.find(f"{{{W}}}body")
    if body is None:
        raise ValueError("У DOCX не знайдено основного тексту протоколу.")
    lines = []
    for paragraph in body.findall(f".//{{{W}}}p"):
        fragments = []
        for node in paragraph.iter():
            if node.tag == f"{{{W}}}t":
                fragments.append(node.text or "")
            elif node.tag in {f"{{{W}}}tab", f"{{{W}}}br", f"{{{W}}}cr"}:
                fragments.append(" ")
        lines.append(clean("".join(fragments)))
    return extract_text("\n".join(lines), source)



def validate(record: Record) -> dict[str, str | float | date]:
    result = {}
    for col, label in FIELDS:
        value = record.values.get(col, "")
        if not isinstance(value, str):
            raise ValueError(f"{label}: потрібне текстове значення.")
        value = value.strip()
        if not value:
            continue
        if col == "M":
            value = supplier_code(value)
            if not value:
                continue
        if col in DATES:
            try:
                result[col] = datetime.strptime(value, "%d.%m.%Y").date()
            except ValueError as exc:
                raise ValueError(f"{label}: используйте дату ДД.ММ.ГГГГ.") from exc
        elif col in NUMBERS:
            try:
                number = float(value.replace(" ", "").replace(",", "."))
            except ValueError as exc:
                raise ValueError(f"{label}: требуется число.") from exc
            import math
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"{label}: требуется неотрицательное конечное число.")
            result[col] = number
        else:
            if col == "N" and not re.fullmatch(r"\d{8}", value):
                raise ValueError(f"{label}: нужен код ЄДРПОУ из 8 цифр.")
            limit = 3000 if col == "M" else 100 if col in {"D", "N"} else 2000 if col == "G" else 200
            if len(value) > limit:
                raise ValueError(f"{label}: не более {limit} символов.")
            if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
                raise ValueError(f"{label}: недопустимый управляющий символ.")
            result[col] = value
    if "D" not in result:
        raise ValueError("Номер протокола обязателен.")
    return result


def cell_text(cell, strings: list[str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(cell.itertext())
    v = cell.find(f"{{{S}}}v")
    if v is None or v.text is None:
        return ""
    return strings[int(v.text)] if cell.get("t") == "s" else v.text


def part_path(base: str, target: str) -> str:
    return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def write_excel(template: Path, output: Path, records: list[Record], on_duplicate: str = "skip") -> dict:
    """Append/update rows in a new copy; preserve service metadata and x14 validation."""
    if on_duplicate not in {"skip", "update"}:
        raise ValueError("Неизвестный режим обработки повторяющихся номеров.")
    if template.resolve() == output.resolve():
        raise ValueError("Сохраните результат в новый файл: исходный Excel не перезаписывается.")
    if output.exists():
        raise ValueError("Файл результата уже существует. Выберите новое имя.")
    if output.suffix.lower() != ".xlsx":
        raise ValueError("Результат должен иметь расширение .xlsx.")
    if not records:
        raise ValueError("Нет протоколов для записи.")
    prepared = [(rec, validate(rec)) for rec in records]
    numbers = [protocol_key(str(values["D"])) for _, values in prepared]
    if len(numbers) != len(set(numbers)):
        raise ValueError("Среди выбранных файлов повторяются номера протоколов. Удалите дубли перед сохранением.")
    parts, infos = _parts(template.read_bytes())
    workbook = xml(parts["xl/workbook.xml"])
    rels = xml(parts["xl/_rels/workbook.xml.rels"])
    targets = {r.get("Id"): part_path("xl/workbook.xml", r.get("Target")) for r in rels}
    strings = []
    if "xl/sharedStrings.xml" in parts:
        strings = ["".join(si.itertext()) for si in xml(parts["xl/sharedStrings.xml"])]
    selected = None
    for sheet in workbook.findall("s:sheets/s:sheet", NS):
        if sheet.get("state", "visible") != "visible":
            continue
        path = targets[sheet.get(f"{{{R}}}id")]
        root = xml(parts[path])
        headers = {re.sub(r"\d+$", "", c.get("r")): key(cell_text(c, strings))
                   for c in root.findall("s:sheetData/s:row[@r='1']/s:c", NS)}
        if all(headers.get(col) == key(label) for col, label in FIELDS):
            if selected is not None:
                raise ValueError("В Excel несколько подходящих листов. Оставьте один видимый лист с шаблоном.")
            selected = path, root, sheet.get("name")
    if selected is None:
        raise ValueError("Не найден лист с колонками D–N из предоставленного шаблона. Используйте исходную структуру заголовков.")
    path, root, sheet_name = selected
    data = root.find("s:sheetData", NS)
    rows = data.findall("s:row", NS)
    # Any content in A:P counts as occupied, including service fields and formulas.
    occupied = [int(row.get("r")) for row in rows if any(
        re.fullmatch(r"[A-P]\d+", c.get("r", "")) and
        (cell_text(c, strings) or c.find(f"{{{S}}}f") is not None)
        for c in row)]
    next_row = max(occupied, default=1) + 1
    existing = {}
    for row in rows:
        for cell in row:
            if re.fullmatch(r"D\d+", cell.get("r", "")) and int(row.get("r")) > 1:
                number = protocol_key(cell_text(cell, strings))
                if number:
                    existing.setdefault(number, []).append(row)
    base_row = next((row for row in rows if row.get("r") == "2"), None)
    styles = {re.sub(r"\d+$", "", c.get("r")): c.get("s") for c in base_row} if base_row is not None else {}
    props = workbook.find("s:workbookPr", NS)
    epoch1904 = props is not None and props.get("date1904") in {"1", "true"}
    report = {"sheet": sheet_name, "added": [], "updated": [], "skipped": []}
    last_row = next_row - 1
    for rec, values in prepared:
        matches = existing.get(protocol_key(str(values["D"])), [])
        if matches and on_duplicate == "skip":
            report["skipped"].append({"source": rec.source, "number": values["D"], "reason": "Номер уже есть в Excel; строка не изменена."})
            continue
        if len(matches) > 1:
            raise ValueError(f'В Excel несколько строк с номером {values["D"]}. Устраните дубли перед обновлением.')
        if not matches and next_row > 1048576:
            raise ValueError("Превышен лимит строк Excel.")
        target_row = int(matches[0].get("r")) if matches else next_row
        row = matches[0] if matches else next((r for r in rows if int(r.get("r")) == target_row), None)
        if row is None:
            row = ET.Element(f"{{{S}}}row", r=str(target_row))
            following = next((r for r in data if int(r.get("r")) > target_row), None)
            if following is None:
                data.append(row)
            else:
                data.insert(data.index(following), row)
        retained = []
        if matches:
            retained = [col for col, _ in FIELDS if col not in values and
                        any(c.get("r") == f"{col}{target_row}" and cell_text(c, strings) for c in row)]
        for col, value in values.items():
            address = f"{col}{target_row}"
            cell = row.find(f"s:c[@r='{address}']", NS)
            if cell is None:
                cell = ET.Element(f"{{{S}}}c", r=address)
                following = next((c for c in row if re.sub(r"\d+$", "", c.get("r", "")) > col), None)
                if following is None:
                    row.append(cell)
                else:
                    row.insert(row.index(following), cell)
            else:
                # Replace only an explicit source value, retaining all cell attributes.
                for child in list(cell):
                    cell.remove(child)
            if col in DATES:
                if isinstance(value, date):
                    serial = (value - date(1904, 1, 1)).days if epoch1904 else (value - date(1899, 12, 30)).days
                    if not epoch1904 and value < date(1900, 3, 1):
                        serial -= 1
                    value = serial
                cell.set("s", styles.get("E", "0"))
            elif styles.get(col):
                cell.set("s", styles[col])
            if isinstance(value, (float, int)):
                cell.set("t", "n")
                ET.SubElement(cell, f"{{{S}}}v").text = str(value)
            else:
                cell.set("t", "inlineStr")
                inline = ET.SubElement(cell, f"{{{S}}}is")
                ET.SubElement(inline, f"{{{S}}}t").text = str(value)
        report["updated" if matches else "added"].append(
            {"source": rec.source, "row": target_row, "values": {col: rec.values.get(col, "") for col, _ in FIELDS},
             "warnings": rec.warnings.copy(), "missing": rec.missing(), "retained": retained})
        last_row = max(last_row, target_row)
        if not matches:
            next_row += 1
    if not report["added"] and not report["updated"]:
        raise ValueError("Все номера уже есть в Excel. Новых строк нет; файл не создан.")
    dimension = root.find("s:dimension", NS)
    if dimension is not None:
        end = dimension.get("ref", "A1:P1").split(":")[-1]
        m = re.fullmatch(r"([A-Z]+)(\d+)", end)
        if m and last_row > int(m[2]):
            dimension.set("ref", f"A1:{m[1]}{last_row}")
    autofilter = root.find("s:autoFilter", NS)
    if autofilter is not None:
        match = re.fullmatch(r"([A-Z]+1:[A-Z]+)(\d+)", autofilter.get("ref", ""))
        if match and last_row > int(match[2]):
            autofilter.set("ref", f"{match[1]}{last_row}")
    parts[path] = ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
    # Expand only this sheet's tables when new rows exceed the existing table.
    rel_path = posixpath.join(posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels")
    if rel_path in parts:
        table_ids = {p.get(f"{{{R}}}id") for p in root.findall("s:tableParts/s:tablePart", NS)}
        for rel in xml(parts[rel_path]):
            if rel.get("Id") not in table_ids:
                continue
            table_path = part_path(path, rel.get("Target"))
            table = xml(parts[table_path])
            match = re.fullmatch(r"(A1:)([N-Z])(\d+)", table.get("ref", ""))
            if match and last_row > int(match[3]):
                new_ref = f"{match[1]}{match[2]}{last_row}"
                table.set("ref", new_ref)
                autofilter = table.find("s:autoFilter", NS)
                if autofilter is not None:
                    autofilter.set("ref", new_ref)
                parts[table_path] = ET.tostring(table, encoding="UTF-8", xml_declaration=True, standalone=True)
    # A temporary file prevents a partial workbook if writing fails.
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".xlsx", delete=False) as f:
            temp = Path(f.name)
        with zipfile.ZipFile(temp, "w") as z:
            for info in infos:
                z.writestr(info, parts[info.filename])
        # Exclusive creation also protects against a concurrent writer.
        with output.open("xb") as dst, temp.open("rb") as src:
            import shutil
            shutil.copyfileobj(src, dst)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return report


def report_text(report: dict) -> str:
    lines = [f'Добавлено: {len(report["added"])}; обновлено: {len(report["updated"])}; пропущено дублей: {len(report["skipped"])}.',
             "Пустые поля не найдены в документе либо требуют ручного ввода.",
             "Правило: Партія (позначення) = Дата виготовлення.",
             "Проверьте предупреждения до использования таблицы.\n"]
    for item in report["added"] + report["updated"]:
        lines += [f'Строка {item["row"]}: {item["source"]}']
        lines += [f'  {LABELS[c]}: {item["values"].get(c) or "—"}' for c, _ in FIELDS]
        lines += [f"  ВНИМАНИЕ: {warning}" for warning in item["warnings"]]
        lines += ["  Пустые поля: " + ", ".join(item["missing"]), ""]
        if item.get("retained"):
            lines += ["  Сохранены прежние значения Excel (в документе не найдены): " +
                      ", ".join(LABELS[c] for c in item["retained"]), ""]
    for item in report["skipped"]:
        lines += [f'ПРОПУЩЕНО: {item["source"]}: {item["reason"]}']
    return "\n".join(lines)


def make_template(path: Path) -> None:
    """Create a valid, styled workbook using the original D–N positions."""
    worksheet = ET.Element(f"{{{S}}}worksheet", nsmap={None: S})
    ET.SubElement(worksheet, f"{{{S}}}dimension", ref="A1:N2")
    views = ET.SubElement(worksheet, f"{{{S}}}sheetViews")
    view = ET.SubElement(views, f"{{{S}}}sheetView", workbookViewId="0", showGridLines="0")
    ET.SubElement(view, f"{{{S}}}pane", ySplit="1", topLeftCell="D2", activePane="bottomLeft", state="frozen")
    cols = ET.SubElement(worksheet, f"{{{S}}}cols")
    ET.SubElement(cols, f"{{{S}}}col", min="1", max="3", width="3", hidden="1")
    for index, (col, _) in enumerate(FIELDS, 4):
        width = "58" if col in {"G", "M"} else "24" if col == "D" else "20"
        ET.SubElement(cols, f"{{{S}}}col", min=str(index), max=str(index), width=width, customWidth="1")
    data = ET.SubElement(worksheet, f"{{{S}}}sheetData")
    header = ET.SubElement(data, f"{{{S}}}row", r="1", ht="42", customHeight="1")
    empty = ET.SubElement(data, f"{{{S}}}row", r="2")
    for col, label in FIELDS:
        cell = ET.SubElement(header, f"{{{S}}}c", r=col + "1", t="inlineStr", s="2")
        ET.SubElement(ET.SubElement(cell, f"{{{S}}}is"), f"{{{S}}}t").text = label
        ET.SubElement(empty, f"{{{S}}}c", r=col + "2", s="1" if col in DATES else "0")
    ET.SubElement(worksheet, f"{{{S}}}autoFilter", ref="D1:N2")
    ET.SubElement(worksheet, f"{{{S}}}pageMargins", left="0.25", right="0.25", top="0.5", bottom="0.5", header="0.2", footer="0.2")
    styles = f'''<styleSheet xmlns="{S}"><numFmts count="1"><numFmt numFmtId="164" formatCode="dd.mm.yyyy"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF164B5B"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    workbook = f'<workbook xmlns="{S}" xmlns:r="{R}"><bookViews><workbookView/></bookViews><sheets><sheet name="Протоколи" sheetId="1" r:id="rSheet"/></sheets></workbook>'
    package_rels = "http://schemas.openxmlformats.org/package/2006/relationships"
    content_types = '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    parts = {
        "[Content_Types].xml": content_types.encode(),
        "_rels/.rels": f'<Relationships xmlns="{package_rels}"><Relationship Id="rWorkbook" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>'.encode(),
        "xl/workbook.xml": workbook.encode(),
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{package_rels}"><Relationship Id="rSheet" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rStyles" Type="{R}/styles" Target="styles.xml"/></Relationships>'.encode(),
        "xl/worksheets/sheet1.xml": ET.tostring(worksheet, xml_declaration=True, encoding="UTF-8", standalone=True),
        "xl/styles.xml": styles.encode(),
    }
    with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)


def write_excel_bytes(template: bytes | None, records: list[Record], on_duplicate: str = "update") -> tuple[bytes, dict]:
    """Return a new copy without persisting uploaded source files."""
    with tempfile.TemporaryDirectory(prefix="protokol-excel-") as directory:
        source = Path(directory) / "source.xlsx"
        target = Path(directory) / "result.xlsx"
        if template is None:
            make_template(source)
        else:
            _parts(template)
            source.write_bytes(template)
        report = write_excel(source, target, records, on_duplicate)
        return target.read_bytes(), report

