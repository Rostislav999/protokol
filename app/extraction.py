"""Local OCR and deliberately conservative extraction of sampling-act fields.

The parser does not infer facts that are absent from the recognized text. Its
output is a draft intended for a human to check against the scan.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from .protocol_text import clean_product_name, sampling_description_from_act

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 20
MAX_PIXELS = 40_000_000
ITEM_FIELDS = (
    "product_name", "protocol_number", "catalog_number", "manufacturer",
    "manufacture_date", "batch_size", "standard", "seal_number",
)
COMMON_FIELDS = ("customer", "sampling_description", "supplier", "contract", "act_number", "act_date", "seal_number")
_ACT_MONTHS = ("січня", "лютого", "березня", "квітня", "травня", "червня",
               "липня", "серпня", "вересня", "жовтня", "листопада", "грудня")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _ocr_languages() -> tuple[str, list[str]]:
    local_data = Path(__file__).resolve().parents[1] / ".local" / "tessdata"
    if "TESSDATA_PREFIX" not in os.environ and (local_data / "ukr.traineddata").exists():
        os.environ["TESSDATA_PREFIX"] = str(local_data)
    if not shutil.which("tesseract"):
        raise ValueError("Tesseract не встановлено. Встановіть Tesseract OCR та мовні пакети ukr, rus, eng.")
    from scripts.tesseract_runtime import available_languages
    return available_languages()


def _ocr_image(image, language: str, psm: int = 3) -> str:
    if image.width * image.height > MAX_PIXELS:
        raise ValueError("Зображення завелике: не більше 40 млн пікселів на сторінку.")
    from PIL import ImageOps
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    image = ImageOps.autocontrast(image.convert("L"))
    from scripts.tesseract_runtime import ocr_image
    return ocr_image(image, language, psm)


def _ocr_table(image, language: str, previous_header=None):
    """Read ruled tables cell by cell, preserving columns across PDF pages.

    Detects physical lines rather than guessing column positions from OCR spaces.
    Perspective correction accommodates a phone scan's slightly skewed table.
    Unknown/unruled layouts fall back to ordinary OCR for manual correction.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    from PIL import Image
    source_rgb = np.array(image.convert("RGB"))
    # Detect thin skewed grid lines at a consistent resolution. At high DPI a
    # long one-pixel morphology kernel otherwise breaks a slanted line apart.
    ratio = max(1.0, source_rgb.shape[1] / 1050)
    rgb = cv2.resize(source_rgb, (round(source_rgb.shape[1] / ratio), round(source_rgb.shape[0] / ratio))) if ratio > 1 else source_rgb
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY_INV, 25, 15)
    binary = cv2.dilate(binary, np.ones((3, 3), np.uint8))
    height, width = gray.shape
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, width // 15), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, height // 15))))
    grid = cv2.dilate(cv2.add(horizontal, vertical), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < width * height * .12:
        return None
    hull = cv2.convexHull(contour)
    corners = cv2.approxPolyDP(hull, .02 * cv2.arcLength(hull, True), True)
    if len(corners) != 4:
        return None
    points = corners[:, 0].astype(np.float32)
    points = points[np.argsort(points[:, 1])]
    top = points[:2][np.argsort(points[:2, 0])]
    bottom = points[2:][np.argsort(points[2:, 0])]
    points = np.array([top[0], top[1], bottom[1], bottom[0]], np.float32)
    table_width = round(max(np.linalg.norm(points[1] - points[0]), np.linalg.norm(points[2] - points[3])))
    table_height = round(max(np.linalg.norm(points[3] - points[0]), np.linalg.norm(points[2] - points[1])))
    original_table_width, original_table_height = round(table_width * ratio), round(table_height * ratio)
    transform = cv2.getPerspectiveTransform(points * ratio, np.array([[0, 0], [original_table_width - 1, 0], [original_table_width - 1, original_table_height - 1], [0, original_table_height - 1]], np.float32))
    table = cv2.warpPerspective(source_rgb, transform, (original_table_width, original_table_height))
    detection_table = cv2.resize(table, (table_width, table_height))
    table_gray = cv2.cvtColor(detection_table, cv2.COLOR_RGB2GRAY)
    b = cv2.adaptiveThreshold(table_gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    b = cv2.dilate(b, np.ones((3, 3), np.uint8))
    h = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, table_width // 5), 1)))
    v = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, table_height // 4))))
    def peaks(values, threshold):
        indexes = np.where(values > threshold)[0]
        groups = np.split(indexes, np.where(np.diff(indexes) > max(2, width // 500))[0] + 1)
        return [round(np.mean(group)) for group in groups if len(group)]
    xs = peaks(np.sum(v > 0, axis=0), table_height * .4)
    ys = peaks(np.sum(h > 0, axis=1), table_width * .4)
    if not 5 <= len(xs) - 1 <= 20 or not 1 <= len(ys) - 1 <= 60:
        return None
    pad = max(3, width // 500)
    cache = {}
    def cell(row, column, rotation=0):
        key = row, column, rotation
        if key not in cache:
            left, right = xs[column] + pad, xs[column + 1] - pad
            upper, lower = ys[row] + pad, ys[row + 1] - pad
            if right <= left or lower <= upper:
                cache[key] = ""
            else:
                crop = Image.fromarray(table[round(upper * ratio):round(lower * ratio), round(left * ratio):round(right * ratio)])
                if rotation:
                    crop = crop.rotate(rotation, expand=True)
                # A white border prevents tightly cropped letters touching edges.
                from PIL import ImageOps
                crop = ImageOps.expand(crop, border=12, fill="white")
                cache[key] = _normalize(_ocr_image(crop, language, psm=6))
        return cache[key]
    first_row = [cell(0, i) for i in range(len(xs) - 1)]
    fields = [_header_field(value) for value in first_row]
    rotations = {}
    has_header = "product_name" in fields and sum(value is not None for value in fields) >= 3
    if has_header:
        # The supplied act puts safe-package IDs in a narrow, rotated final
        # column. Read that labelled column explicitly rather than dropping it
        # or treating its numbers as sample/protocol IDs.
        last = len(fields) - 1
        narrow = ys[1] - ys[0] > (xs[last + 1] - xs[last]) * 1.4
        if narrow and (fields[last] is None or "сейф" in first_row[last].lower()):
            for rotation in (90, 270):
                if _header_field(cell(0, last, rotation)) == "seal_number":
                    fields[last] = "seal_number"
                    rotations[last] = rotation
                    break
    if not has_header:
        prior_fields = previous_header.get("fields") if isinstance(previous_header, dict) else previous_header
        if prior_fields is None or len(prior_fields) != len(fields):
            return None
        fields = prior_fields
        rotations = previous_header.get("rotations", {}) if isinstance(previous_header, dict) else {}
    if "product_name" not in fields:
        return None
    rows = []
    for row in range(1 if has_header else 0, len(ys) - 1):
        item = _empty_item()
        for column, field in enumerate(fields):
            if field:
                item[field] = cell(row, column, rotations.get(column, 0))
        if not item["product_name"]:
            continue
        item["manufacturer"] = re.sub(r"^виробник\s*:\s*", "", item["manufacturer"], flags=re.I)
        item["batch_size"] = re.sub(r"(?<=\d)\s*(?:кі|кг|kg)\.?$", " кг", item["batch_size"], flags=re.I)
        catalog = re.search(r"\(\s*(\d{5})\s*\)", item["product_name"])
        if catalog:
            item["catalog_number"] = catalog.group(1)
        rows.append(item)
    if not rows:
        return None
    columns = ["№", "Найменування продукції", "Ідентифікаційний номер", "Номер за каталогом", "Виробник", "Дата виготовлення", "Розмір партії", "Нормативний документ", "Номер пломби / сейф-пакета"]
    lines = [" | ".join(columns)]
    for index, item in enumerate(rows, 1):
        lines.append(" | ".join([str(index)] + [item[field].replace("|", " ") for field in ITEM_FIELDS]))
    header_text = ""
    warnings = []
    min_y = round(float(np.min(points[:, 1])))
    if min_y > height * .15:
        header_image = image.crop((0, 0, image.width, max(1, round((min_y - pad) * ratio))))
        header_text = _ocr_image(header_image, language, psm=6)
        recovered_date = _recover_act_date(header_image, header_text, language)
        if recovered_date:
            header_text += "\nДата акта: " + recovered_date
            warnings.append("Дату акта уточнено окремим розпізнаванням заголовка. Перевірте її за сканом.")
    return {"text": header_text + "\n\n" + "\n".join(lines),
            "header": {"fields": fields, "rotations": rotations}, "rows": len(rows), "warnings": warnings}


def extract_text(data: bytes, filename: str) -> dict:
    """Extract native text or run local OCR; no document data leaves the server."""
    if not data:
        raise ValueError("Завантажений файл порожній.")
    if len(data) > MAX_BYTES:
        raise ValueError("Файл завеликий: максимально 25 МБ.")
    extension = Path(filename).suffix.lower()
    warnings: list[str] = []
    if extension in (".txt", ".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1251")
            warnings.append("Текст прочитано у Windows-1251. Перевірте українські літери.")
        return {"text": text, "pages": 1, "method": "text", "warnings": warnings}
    if extension == ".docx":
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 10 * 1024 * 1024:
                    raise ValueError("XML документа завеликий.")
                root = ET.fromstring(archive.read(info))
        except (BadZipFile, KeyError, ET.ParseError) as exc:
            raise ValueError("Некоректний файл DOCX.") from exc
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        body = root.find("w:body", ns)
        if body is None:
            raise ValueError("У DOCX немає основного тексту.")
        def paragraph_text(element):
            return "".join(node.text or "" for node in element.iter() if node.tag == "{" + ns["w"] + "}t")
        lines = []
        for block in body:
            if block.tag.endswith("}p"):
                lines.append(paragraph_text(block))
            elif block.tag.endswith("}tbl"):
                for row in block.findall("w:tr", ns):
                    lines.append(" | ".join(paragraph_text(cell) for cell in row.findall("w:tc", ns)))
        text = "\n".join(lines)
        if not text.strip():
            raise ValueError("У DOCX немає тексту. Збережіть скан як PDF або зображення для OCR.")
        return {"text": text, "pages": 1, "method": "docx", "warnings": warnings}
    if extension == ".pdf":
        import fitz
        from PIL import Image
        try:
            document = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ValueError("Не вдалося відкрити PDF.") from exc
        with document:
            if document.needs_pass:
                raise ValueError("PDF захищений паролем. Завантажте незахищену копію.")
            if not 0 < len(document) <= MAX_PAGES:
                raise ValueError("PDF має містити від 1 до 20 сторінок.")
            texts, methods = [], set()
            language = None
            table_header = None
            for page in document:
                text = page.get_text(sort=True).strip()
                if len(re.sub(r"\W", "", text)) >= 40:
                    methods.add("pdf-text")
                else:
                    if language is None:
                        language, language_warnings = _ocr_languages()
                        warnings.extend(language_warnings)
                    # 200 DPI balances small table text and CPU/memory use.
                    scale = 200 / 72
                    if page.rect.width * page.rect.height * scale * scale > MAX_PIXELS:
                        raise ValueError("Сторінка PDF завелика для OCR (40 млн пікселів).")
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    table_result = _ocr_table(image, language, table_header)
                    if table_result:
                        text = table_result["text"]
                        table_header = table_result["header"]
                        methods.add("ocr-table")
                        warnings.extend(table_result.get("warnings", []))
                        warnings.append("Таблицю розпізнано по комірках. Рукописні номери не визначаються надійно — внесіть їх вручну.")
                    else:
                        text = _ocr_image(image, language)
                        methods.add("ocr")
                texts.append(text)
            return {"text": "\n\n".join(texts), "pages": len(document),
                    "method": "+".join(sorted(methods)), "warnings": list(dict.fromkeys(warnings))}
    if extension in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"):
        from PIL import Image, UnidentifiedImageError
        try:
            image = Image.open(io.BytesIO(data))
            pages = getattr(image, "n_frames", 1)
            if pages > MAX_PAGES:
                raise ValueError("Зображення має забагато сторінок: максимально 20.")
            language, language_warnings = _ocr_languages()
            warnings.extend(language_warnings)
            texts = []
            table_header = None
            for index in range(pages):
                image.seek(index)
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Зображення завелике: не більше 40 млн пікселів.")
                result = _ocr_table(image.copy(), language, table_header)
                if result:
                    texts.append(result["text"])
                    table_header = result["header"]
                    warnings.extend(result.get("warnings", []))
                    warnings.append("Таблицю розпізнано по комірках. Рукописні номери внесіть вручну.")
                else:
                    texts.append(_ocr_image(image.copy(), language))
            image.close()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Не вдалося прочитати зображення або воно завелике.") from exc
        return {"text": "\n\n".join(texts), "pages": pages, "method": "ocr", "warnings": list(dict.fromkeys(warnings))}
    raise ValueError("Підтримуються PDF, PNG, JPG, TIFF, BMP, WEBP, DOCX і TXT.")


# Labels are interpreted as document content, never as commands or instructions.
_LABELS = {
    "customer": [r"замовник(?:\s+випробувань)?", r"заказчик"],
    "supplier": [r"постачальник", r"поставщик"],
    "contract": [r"договір(?:и)?", r"договор(?:и|ы)?", r"контракт(?:и)?"],
    "act_number": [r"номер\s+акта?(?:\s+відбору(?:\s+зразків)?)?", r"акт\s+відбору(?:\s+зразків)?(?:\s*№)?"],
    "act_date": [r"дата\s+акта?(?:\s+відбору(?:\s+зразків)?)?"],
    "seal_number": [r"номер(?:и)?\s+(?:пломби|пломб|пломбування|сейф[- ]?пакет(?:а|ів)?)(?:\s+зразк(?:а|ів))?", r"пломб[аи](?:\s*№)?", r"опломбовано", r"сейф[- ]?пакет(?:\s*№)?"],
    "sampling_description": [r"зразки харчових продуктів відібрано за адресою", r"місце(?:\s+та\s+адреса)?\s+відбору(?:\s+(?:зразків|проб))?", r"адреса\s+відбору(?:\s+зразків)?", r"місце відбору зразка"],
    "product_name": [r"найменування(?:\s+(?:та|і)\s+назва)?(?:\s+(?:продукції|продукту|продуктів|харчових продуктів|зразка|зразків|товару))?", r"назва\s+(?:зразка|продукту|продукції|продуктів)", r"наименование(?:\s+(?:продукции|продукта|образца))?"],
    "protocol_number": [r"ідентифікаційний\s+(?:номер|код)(?:\s+зразка)?", r"номер\s+(?:протоколу|зразка)", r"протокол(?:\s+випробувань)?\s*(?:№)?"],
    "catalog_number": [r"номер\s+за\s+каталогом(?:\s+продуктів харчування)?", r"(?:номер|код)\s+(?:за\s+)?каталогу", r"каталожний\s+номер", r"код\s+продукту"],
    "manufacturer": [r"виробник(?:\s+(?:та|і)\s+адреса)?", r"виготовлювач", r"производитель"],
    "manufacture_date": [r"дата\s+виготовлення(?:\s*[/,]\s*термін\s+придатності)?", r"виготовлено", r"дата\s+виробництва", r"дата\s+изготовления", r"вжити\s+до"],
    "batch_size": [r"розмір\s+партії(?:,?\s+від\s+якої\s+відібрано\s+зразок)?", r"(?:маса|обсяг|кількість)\s+партії", r"партія", r"размер\s+партии"],
    "standard": [r"нормативний\s+документ", r"(?:позначення\s+)?нд", r"випробування\s+на\s+відповідність(?:\s+нд)?", r"(?:підлягає\s+)?дослідженню\s+на\s+відповідність"],
}
_LABEL_PATTERNS = [(key, re.compile(r"^\s*(?:" + "|".join(patterns) + r")\s*[:：]\s*(.*)$", re.I)) for key, patterns in _LABELS.items()]
_PRODUCT_HINT = re.compile(r"свин|ялов|говяд|суп|борщ|каша|квасол|фасол|кільк|кильк|салак|тунец|тунц|тріск|треск|джем|повидл|сіль|соль|цукор|сахар|сухар|паштет|снідан|завтрак|молоко|масло|олія|крупа|консерв", re.I)


def _empty_item() -> dict:
    return {field: "" for field in ITEM_FIELDS}


def _split_identification(value: str) -> tuple[str, str]:
    match = re.match(r"\s*№?\s*([\w]+(?:[-/]\w+)+)\s*[–—−]\s*(.+)", value)
    if match:
        return match.group(1), match.group(2).strip()
    return "", value


def _table_columns(line: str) -> list[str] | None:
    if "|" in line:
        value = line.strip()
        if value.startswith("|"):
            # Markdown-style outer borders are decoration. An ordinary final
            # delimiter, however, represents an empty last data cell.
            value = value[1:]
            if value.endswith("|"):
                value = value[:-1]
        return [cell.strip() for cell in value.split("|")]
    if "\t" in line:
        return [cell.strip() for cell in line.split("\t")]
    if ";" in line:
        return [cell.strip() for cell in line.split(";")]
    if re.search(r"\S\s{2,}\S", line):
        return [cell.strip() for cell in re.split(r"\s{2,}", line.strip())]
    return None


def _header_field(value: str) -> str | None:
    v = _normalize(value).lower().strip(".:")
    if re.search(r"пломб|сейф[\s-]*пакет|^пакет$", v):
        return "seal_number"
    if re.search(r"нормативн.*документ|випробування.*відпов|дослідж.*(?:відпов|вілпов)|^нд$", v):
        return "standard"
    if re.search(r"найменування|назва.*(?:продук|зраз)|наименование", v):
        return "product_name"
    if re.search(r"виробник|виготовлювач|производител", v):
        return "manufacturer"
    if re.search(r"дата.*(?:виготов|виробниц|изготов)|термін.*придат|вжити до", v):
        return "manufacture_date"
    if re.search(r"каталог|код продукт", v):
        return "catalog_number"
    if re.search(r"ідентифікаці|номер.*(?:зраз|протокол)", v):
        return "protocol_number"
    if re.search(r"парті|партии", v):
        return "batch_size"
    return None


def _act_date(value: str) -> str:
    """Read an explicitly dated act header without borrowing contract dates."""
    from datetime import date
    numeric = re.search(r"(?<!\d)(\d{2})[./](\d{2})[./](\d{4})(?!\d)", value)
    iso = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", value)
    words = re.search(r"(?<![\d])(?:[«\"“]?)(\d{1,2})[»\"”]?\s+(" + "|".join(_ACT_MONTHS) + r")\s+(\d{4})(?!\d)", value, re.I)
    try:
        if numeric:
            return date(int(numeric[3]), int(numeric[2]), int(numeric[1])).isoformat()
        if iso:
            return date(int(iso[1]), int(iso[2]), int(iso[3])).isoformat()
        if words:
            return date(int(words[3]), _ACT_MONTHS.index(words[2].lower()) + 1, int(words[1])).isoformat()
    except ValueError:
        pass
    return ""


def _act_metadata(preamble: str) -> tuple[str, str]:
    marker = re.search(
        r"(?im)^\s*[|]?\s*акт(?:\s+відбору(?:\s+зразків(?:\s+продукції)?)?)?\s*[:：]?\s*"
        r"(?:№|No\.?|N[º°]?\.?|#)\s*([A-ZА-ЯІЇЄҐ0-9]+(?:\s*[-/]\s*[A-ZА-ЯІЇЄҐ0-9]+)*)",
        preamble,
    )
    if not marker or not re.search(r"\d", marker[1]):
        return "", ""
    number = re.sub(r"\s+", "", marker[1])
    header = re.split(r"\b(?:З\s+метою|Відповідно|Підстава|Договір|Контракт|Постачальник)\b",
                      preamble[marker.end():], maxsplit=1, flags=re.I)[0][:500]
    return number, _act_date(header)


def _recover_act_date(image, text: str, language: str) -> str:
    """Re-read a difficult date from its own image, without guessing OCR digits.

    Quotation marks around a day can look like an extra leading digit. Accept a
    second OCR pass only for an independently recognized, standalone date line
    whose word month/year agree with the original act header. Contract lines,
    numeric contract dates and ambiguous alternatives cannot supply this fact.
    """
    number, known_date = _act_metadata(text)
    if not number or known_date:
        return ""
    header = re.split(r"\b(?:З\s+метою|Відповідно|Підстава|Договір|Контракт|Постачальник)\b",
                      text, maxsplit=1, flags=re.I)[0]
    month_years = {(match[1].lower(), int(match[2])) for match in re.finditer(
        r"\b(" + "|".join(_ACT_MONTHS) + r")\s+(\d{4})(?!\d)", header, re.I)}
    if len(month_years) != 1:
        return ""
    expected_month, expected_year = next(iter(month_years))
    crop = image.crop((0, round(image.height * .15), round(image.width * .7), round(image.height * .35)))
    recognized = _ocr_image(crop, language, psm=6)
    standalone = re.compile(r"\s*(?:від\s+)?[«\"“]?\d{1,2}[»\"”]?\s+(?:" + "|".join(_ACT_MONTHS)
                            + r")\s+\d{4}(?:\s+року)?\s*[.,]?\s*", re.I)
    dates = set()
    for line in recognized.splitlines():
        if standalone.fullmatch(line):
            date = _act_date(line)
            if date:
                dates.add(date)
    if len(dates) != 1:
        return ""
    recovered = next(iter(dates))
    if int(recovered[:4]) != expected_year or int(recovered[5:7]) != _ACT_MONTHS.index(expected_month) + 1:
        return ""
    return recovered


def parse_act(text: str) -> dict:
    """Parse labeled item blocks and common text/table layouts, with review flags.

    A table's column labels must be recognizable. Numbered rows without a table
    header are accepted as product names only; their numeric columns are not
    guessed. This avoids silently turning quantities or dates into identifiers.
    """
    common = {field: "" for field in COMMON_FIELDS}
    items: list[dict] = []
    warnings = ["Перевірте всі розпізнані поля за сканом. Автоматичне розпізнавання може помилятися."]
    if not isinstance(text, str) or not text.strip():
        return {"common": common, "items": items, "warnings": warnings + ["Текст акта порожній. Додайте текст або завантажте чіткіший скан."]}
    if len(text) > 500_000:
        raise ValueError("Текст акта завеликий: максимально 500 000 символів.")
    current = None
    previous_field = None
    table_header = None
    table_last_item = None
    pending_number = None
    # Acts often put contract and supplier in a narrative preamble, rather than
    # in "field: value" lines. Keep these separate from table item facts.
    preamble = re.split(r"\n[^\n]*(?:Найменування продукції|Назва\s+харчового)", text, maxsplit=1, flags=re.I)[0]
    flat_preamble = _normalize(preamble)
    common["act_number"], common["act_date"] = _act_metadata(preamble)
    supplier_match = re.search(r"постачальник\s*[:：]?\s+(.+?)(?=,?\s+за\s+кодом\s+постачальника|\s+проведено\s+відбір|$)", flat_preamble, re.I)
    if supplier_match:
        common["supplier"] = supplier_match.group(1).strip(" ,.;")
    contract_match = re.search(r"(?:контракту|договору)\)?\s*[,.:]?\s*(?:№|No|N)?\s*([A-ZА-ЯІЇЄ0-9]+(?:-[A-ZА-ЯІЇЄ0-9]+){2,})\s+від\s+(\d{2}\.\d{2}\.\d{4})", flat_preamble, re.I)
    if contract_match:
        common["contract"] = "№" + contract_match.group(1) + " від " + contract_match.group(2)
    sampling_match = re.search(r"Зразки харчових продуктів відібрано за адресою\s*[:：]?\s*(.+?)(?=\s+Час відбору|\n\n|$)", preamble, re.I | re.S)
    if sampling_match:
        address_lines = []
        for value_line in sampling_match.group(1).splitlines():
            if re.search(r"\d{1,2}:\d{2}", value_line):
                break
            address_lines.append(value_line)
        common["sampling_description"] = _normalize(" ".join(address_lines))
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = _normalize(raw)
        if not line:
            previous_field = None
            continue
        if re.match(r"^Зразки харчових продуктів відібрано за адресою\s+[^:]", line, re.I):
            # The narrative preamble above also includes wrapped address lines.
            previous_field = None
            continue
        cells = _table_columns(raw)
        if cells:
            header = [_header_field(cell) for cell in cells]
            if "product_name" in header and sum(field is not None for field in header) >= 2:
                table_header = header
                previous_field = None
                continue
            if table_header and len(cells) == len(table_header):
                # PDF/OCR may retain the table's repeated header on a new page.
                if "product_name" in header:
                    continue
                # A table with a serial-number column must have an actual row
                # number, not merely a matching count of OCR whitespace gaps.
                if table_header[0] is None and not re.fullmatch(r"\d{1,3}[.)]?", cells[0]):
                    continue
                item = _empty_item()
                for field, value in zip(table_header, cells):
                    if field and value and value not in ("-", "—"):
                        item[field] = _normalize(value)
                if item["product_name"]:
                    items.append(item)
                    current = table_last_item = item
                    previous_field = None
                    continue
                if table_last_item and any(item.values()):
                    for field, value in item.items():
                        if value:
                            table_last_item[field] = _normalize(table_last_item[field] + " " + value)
                    continue
        special = re.match(r"ідентифікаційний\s+номер\s+та\s+назва\s+зразка\s*:\s*(.+)$", line, re.I)
        if special:
            number, name = _split_identification(special.group(1))
            current = _empty_item()
            current.update(product_name=name, protocol_number=number or pending_number or "")
            items.append(current)
            pending_number = None
            previous_field = "product_name"
            table_header = None
            continue
        matched = False
        for field, pattern in _LABEL_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1).strip()
            if field == "act_number":
                if not common["act_date"]:
                    common["act_date"] = _act_date(value)
                value = re.split(r"\s+від\s+", value, maxsplit=1, flags=re.I)[0].lstrip("№ ")
            if field == "manufacture_date" and line.lower().startswith("вжити до"):
                value = "вжити до: " + value
            if field == "seal_number" and current is not None:
                current[field] = value
            elif field in COMMON_FIELDS:
                common[field] = value
            elif field == "product_name":
                if current is None or current["product_name"]:
                    current = _empty_item()
                    if pending_number:
                        current["protocol_number"] = pending_number
                        pending_number = None
                    items.append(current)
                current[field] = value
            elif field == "protocol_number" and current is None:
                pending_number = value.lstrip("№ ")
            else:
                if current is None:
                    current = _empty_item()
                    items.append(current)
                current[field] = value.lstrip("№ ") if field == "protocol_number" else value
            previous_field = field
            table_header = None
            matched = True
            break
        if matched:
            continue
        # A numbered line starts a product only when its words look like food.
        numbered = re.match(r"^\d{1,3}\s*[.)]\s*(.+)$", line)
        if numbered and _PRODUCT_HINT.search(numbered.group(1)):
            name = numbered.group(1)
            if cells:
                # Keep only the likely name column, and let the user map others.
                candidates = [cell for cell in cells if _PRODUCT_HINT.search(cell)]
                if candidates:
                    name = re.sub(r"^\d{1,3}\s*[.)]?\s*", "", candidates[0])
                warnings.append("Для рядка без заголовків таблиці прочитано лише назву продукту; решту полів внесіть вручну.")
            current = _empty_item()
            current["product_name"] = name
            items.append(current)
            previous_field = "product_name"
            table_header = None
            continue
        # A short unlabelled food line may be the product cell of a simple act.
        if current is None and _PRODUCT_HINT.search(line) and not re.search(r"акт|відбор|перелік|підпис", line, re.I) and len(line) <= 200:
            current = _empty_item()
            current["product_name"] = line
            items.append(current)
            previous_field = "product_name"
            continue
        # Preserve wrapped lines only for verbose known fields. Never append a
        # signature or a new numbered section to the preceding value.
        if previous_field in ("manufacturer", "sampling_description", "customer", "supplier", "contract", "product_name", "standard"):
            if re.match(r"^(?:\d+[.)]|акт\b|дата\b|підпис|коміс|відібрав|вищезазначена|результат|додаток|м\.п\.)", line, re.I):
                previous_field = None
                continue
            if ":" not in line or previous_field in ("manufacturer", "sampling_description", "standard"):
                target = common if previous_field in COMMON_FIELDS else current
                if target is not None:
                    target[previous_field] = _normalize(target[previous_field] + " " + line)
    items = [item for item in items if item["product_name"].strip()]
    for item in items:
        item["product_name"] = clean_product_name(item["product_name"], item["catalog_number"])
    common["sampling_description"] = sampling_description_from_act(common["sampling_description"], preamble)
    if common["act_date"]:
        common["act_date"] = _act_date(common["act_date"]) or common["act_date"]
    if len(items) > 200:
        raise ValueError("В акті забагато позицій: максимально 200.")
    if not items:
        warnings.append("Не вдалося впевнено визначити позиції. Додайте їх вручну або виправте текст акта.")
    if items and any(not item["protocol_number"] for item in items):
        warnings.append("Не для всіх позицій прочитано ідентифікаційний номер. Вкажіть його перед створенням Word.")
    if items and any(not item["manufacturer"] or not item["manufacture_date"] or not item["batch_size"] for item in items):
        warnings.append("Частина реквізитів позицій відсутня. Перевірте виробника, дату виготовлення й розмір партії; старі дані шаблону не підставляються.")
    return {"common": common, "items": items, "warnings": list(dict.fromkeys(warnings))}
