import io

import fitz
from PIL import Image, ImageDraw
import pytest

from app import protocol_pdf as reader


TEXT = "Protocol testing report with sufficient text and explicit sample metadata for native extraction."
OCR = "ПРОТОКОЛ ВИПРОБУВАНЬ №87-015-3000-1305-2\nЗамовник: ТОВ Приклад\nПостачальник: ТОВ Постачальник"


def pdf_with_images(*, native=False, rotated=False):
    document = fitz.open()
    if native:
        document.new_page().insert_text((72, 72), TEXT)
    image = Image.new("RGB", (300, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 25, 120, 85), fill="black")
    if rotated:
        image = image.rotate(90, expand=True)
    output = io.BytesIO()
    image.save(output, "PNG")
    page = document.new_page(width=image.width, height=image.height)
    page.insert_image(page.rect, stream=output.getvalue())
    data = document.tobytes()
    document.close()
    return data


def test_native_pdf_does_not_require_tesseract(monkeypatch):
    document = fitz.open()
    document.new_page().insert_text((72, 72), TEXT)
    data = document.tobytes()
    document.close()
    monkeypatch.setattr(reader, "_ocr_languages", lambda: pytest.fail("Native text used OCR"))
    text, warnings = reader.read_protocol_pdf(data)
    assert TEXT in text
    assert warnings == []


def test_mixed_native_and_scanned_pdf_preserves_pages_and_warns(monkeypatch):
    monkeypatch.setattr(reader, "_ocr_languages", lambda: ("ukr+rus+eng", []))
    calls = []
    monkeypatch.setattr(reader, "_ocr_image", lambda image, language, psm: calls.append(language) or OCR)
    text, warnings = reader.read_protocol_pdf(pdf_with_images(native=True))
    assert TEXT in text and OCR in text
    assert calls == ["ukr+rus+eng"]
    assert any("Сторінку 2" in warning and "OCR" in warning for warning in warnings)


def test_rotated_scan_selects_readable_protocol_labels_without_editing_digits(monkeypatch):
    monkeypatch.setattr(reader, "_ocr_languages", lambda: ("ukr", []))
    calls = []
    def recognize(image, language, psm):
        calls.append(image.size)
        return OCR if image.height > image.width else "unreadable rotated content with enough characters"
    monkeypatch.setattr(reader, "_ocr_image", recognize)
    text, warnings = reader.read_protocol_pdf(pdf_with_images(rotated=True))
    assert "№87-015-3000-1305-2" in text
    assert len(calls) == 2
    assert any("90°" in warning for warning in warnings)


def test_two_page_spread_splits_only_when_gutter_and_both_sides_are_clear():
    image = Image.new("RGB", (1000, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 420, 400), fill="black")
    draw.rectangle((580, 40, 940, 400), fill="black")
    parts = reader._spread_parts(image)
    assert len(parts) == 2
    assert [part.size for part in parts] == [(500, 500), (500, 500)]
    for part in parts:
        part.close()
    draw.line((30, 150, 970, 150), fill="black", width=15)
    assert reader._spread_parts(image) == [image]


def test_one_sided_wide_page_is_not_a_two_page_spread():
    image = Image.new("RGB", (1000, 500), "white")
    ImageDraw.Draw(image).rectangle((40, 40, 420, 400), fill="black")
    assert reader._spread_parts(image) == [image]


def test_blank_pdf_does_not_start_tesseract(monkeypatch):
    document = fitz.open()
    document.new_page()
    data = document.tobytes()
    document.close()
    monkeypatch.setattr(reader, "_ocr_languages", lambda: pytest.fail("Blank page used OCR"))
    with pytest.raises(ValueError, match="не знайдено"):
        reader.read_protocol_pdf(data)


def test_encrypted_and_invalid_pdf_have_actionable_errors():
    document = fitz.open()
    document.new_page().insert_text((72, 72), TEXT)
    data = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password")
    document.close()
    with pytest.raises(ValueError, match="паролем"):
        reader.read_protocol_pdf(data)
    for bad in (b"", b"invalid PDF"):
        with pytest.raises(ValueError):
            reader.read_protocol_pdf(bad)


def test_page_and_render_limits_are_checked_before_ocr(monkeypatch):
    monkeypatch.setattr(reader, "MAX_PAGES", 1)
    with pytest.raises(ValueError, match="20 сторінок"):
        reader.read_protocol_pdf(pdf_with_images(native=True))
    monkeypatch.setattr(reader, "MAX_PAGES", 20)
    monkeypatch.setattr(reader, "MAX_PIXELS", 10)
    with pytest.raises(ValueError, match="40 млн"):
        reader.read_protocol_pdf(pdf_with_images())


def test_ocr_failure_does_not_become_a_successful_empty_protocol(monkeypatch):
    monkeypatch.setattr(reader, "_ocr_languages", lambda: ("ukr", []))
    monkeypatch.setattr(reader, "_ocr_image", lambda *a, **k: "")
    with pytest.raises(ValueError, match="не прочитав"):
        reader.read_protocol_pdf(pdf_with_images())
