"""Read protocol PDFs locally using native text or the prepared OCR engine."""
from __future__ import annotations

import re

from .extraction import _ocr_image, _ocr_languages

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 20
MAX_PIXELS = 40_000_000
SCALE = 200 / 72


def _ink_fraction(image) -> float:
    gray = image.convert("L")
    try:
        histogram = gray.histogram()
        return sum(histogram[:190]) / max(sum(histogram), 1)
    finally:
        gray.close()


def _spread_parts(image):
    """Split clear two-page spreads; retain wide tables with ink in the gutter."""
    if image.width <= image.height * 1.3:
        return [image]
    band = image.crop((round(image.width * .49), round(image.height * .08),
                       round(image.width * .51), round(image.height * .92)))
    gray = band.convert("L")
    try:
        histogram = gray.histogram()
        white_fraction = sum(histogram[235:]) / max(sum(histogram), 1)
    finally:
        gray.close()
        band.close()
    if white_fraction <= .985:
        return [image]
    parts = [image.crop((0, 0, image.width // 2, image.height)),
             image.crop((image.width // 2, 0, image.width, image.height))]
    if all(_ink_fraction(part) >= .002 for part in parts):
        return parts
    for part in parts:
        part.close()
    return [image]


def _text_score(text: str) -> tuple[int, int, int]:
    """Choose image orientation from readable labels, without altering OCR IDs."""
    labels = (
        (r"ПРОТОКОЛ\s+ВИПРОБУВАНЬ", 60),
        (r"Ідентифікаційний\s+номер", 20),
        (r"Номер\s+за\s+Каталогом", 20),
        (r"(?:Замовник|Постачальник|Виробник)\s*:", 10),
        (r"Найменування\s+показника", 20),
        (r"(?:Результат|Методика)\s+(?:випробувань|випробування)", 15),
        (r"Мета\s+випробувань|Висновок\s*:", 15),
    )
    score = sum(weight * len(re.findall(pattern, text, re.I)) for pattern, weight in labels)
    words = re.findall(r"[А-ЯІЇЄҐа-яіїєґ]{3,}", text)
    return score, len(words), min(len(text.strip()), 20000)


def _ocr_orientation(image, language: str) -> tuple[str, int]:
    best = ("", 0)
    best_score = (-1, -1, -1)
    for rotation in (0, 90, 180, 270):
        rotated = image if not rotation else image.rotate(rotation, expand=True)
        parts = _spread_parts(rotated)
        try:
            chunks = [_ocr_image(part, language, psm=3) for part in parts if _ink_fraction(part) >= .002]
            text = "\n".join(chunks).strip()
        finally:
            for part in parts:
                if part is not rotated:
                    part.close()
            if rotated is not image:
                rotated.close()
        score = _text_score(text)
        if score > best_score:
            best_score, best = score, (text, rotation)
        if score[0] >= 60:
            break
    return best


def read_protocol_pdf(data: bytes) -> tuple[str, list[str]]:
    """Return native/OCR text plus review messages; never invoke Word or a network."""
    import fitz
    from PIL import Image

    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("PDF порожній або перевищує 25 МБ.")
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("Не вдалося відкрити PDF протоколу.") from exc
    warnings, texts, language = [], [], None
    with document:
        if document.needs_pass:
            raise ValueError("PDF захищений паролем. Завантажте незахищену копію.")
        if not 0 < len(document) <= MAX_PAGES:
            raise ValueError("PDF має містити від 1 до 20 сторінок.")
        for index, page in enumerate(document, 1):
            text = page.get_text("text") or ""
            if len(re.sub(r"\W", "", text)) >= 40:
                texts.append(text)
                continue
            if page.rect.width * page.rect.height * SCALE * SCALE > MAX_PIXELS:
                raise ValueError(f"Сторінка {index} PDF завелика для OCR (40 млн пікселів).")
            pixmap = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            try:
                if _ink_fraction(image) < .002:
                    texts.append("")
                    warnings.append(f"Сторінка {index} PDF порожня; текст не додано.")
                    continue
                if language is None:
                    language, notes = _ocr_languages()
                    warnings.extend(notes)
                text, rotation = _ocr_orientation(image, language)
            finally:
                image.close()
            if len(text.strip()) < 30:
                raise ValueError(f"OCR не прочитав сторінку {index}. Перевірте якість скана.")
            texts.append(text)
            suffix = f" Зображення повернуто на {rotation}°." if rotation else ""
            warnings.append(f"Сторінку {index} розпізнано OCR. Звірте цифри, дати та коди ЄДРПОУ зі сканом." + suffix)
    result = "\n\n".join(texts).strip()
    if len(result) < 30:
        raise ValueError("У PDF не знайдено тексту протоколу або читабельного зображення.")
    return result, list(dict.fromkeys(warnings))
