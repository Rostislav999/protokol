"""Isolated Windows print jobs with private Qt/driver settings for each copy.

Word exports the pages before this worker starts. No printer defaults are changed,
and two separate one-copy jobs prevent drivers from dropping the second copy or
putting it on the back of the first copy's last sheet.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys

import fitz

DUPLEX_MODES = ('simplex', 'long_edge', 'short_edge')
MAX_PAGE_PIXELS = 40_000_000


def _save(path: Path, result: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def _validate_pdf(path: str) -> None:
    with fitz.open(path) as document:
        if not document.is_pdf or document.needs_pass or not 1 <= len(document) <= 10000:
            raise ValueError('Не вдалося підготувати сторінки завдання до друку.')
        for page in document:
            if any(not math.isfinite(size) or not 36 <= size <= 10000 for size in (page.rect.width, page.rect.height)):
                raise ValueError('Некоректний розмір сторінки завдання.')


class WindowsPrinter:
    def __init__(self, name: str, duplex: str):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

        self.application = QApplication.instance() or QApplication(['Protokol-print-worker'])
        if name not in QPrinterInfo.availablePrinterNames():
            raise ValueError('PRINTER_NOT_FOUND')
        self.info = QPrinterInfo.printerInfo(name)
        self.name = name
        self.mode = {'simplex': QPrinter.DuplexNone, 'long_edge': QPrinter.DuplexLongSide,
                     'short_edge': QPrinter.DuplexShortSide}[duplex]
        if duplex != 'simplex' and self.mode not in self.info.supportedDuplexModes():
            raise ValueError('DUPLEX_UNSUPPORTED')
        # Check settings without creating a painter or sending a physical job.
        self._printer()

    def _printer(self):
        from PySide6.QtPrintSupport import QPrinter

        printer = QPrinter(self.info, QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.NativeFormat)
        printer.setPrinterName(self.name)
        printer.setResolution(300)
        printer.setCopyCount(1)
        printer.setCollateCopies(False)
        printer.setPrintRange(QPrinter.AllPages)
        printer.setPageOrder(QPrinter.FirstPageFirst)
        printer.setDuplex(self.mode)
        printer.setFullPage(True)
        if not printer.isValid() or printer.printerName() != self.name:
            raise ValueError('PRINTER_NOT_FOUND')
        if printer.duplex() != self.mode or printer.copyCount() != 1:
            raise ValueError('PRINTER_SETTINGS_REJECTED')
        return printer

    @staticmethod
    def _page_size(printer, rectangle):
        from PySide6.QtCore import QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize

        width, height = rectangle.width, rectangle.height
        size = QPageSize(QSizeF(min(width, height), max(width, height)), QPageSize.Point)
        printer.setPageSize(size)
        printer.setPageOrientation(QPageLayout.Landscape if width > height else QPageLayout.Portrait)
        printer.setFullPage(True)

    def print_copy(self, path: str, label: str) -> None:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter

        printer = self._printer()
        printer.setDocName(label)
        printer.setCreator('ОЗтаУСЯ')
        painter = QPainter()
        try:
            with fitz.open(path) as document:
                self._page_size(printer, document[0].rect)
                if not painter.begin(printer):
                    raise ValueError('Драйвер не зміг відкрити завдання друку. Перевірте чергу принтера.')
                for index, page in enumerate(document):
                    if index:
                        self._page_size(printer, page.rect)
                        if not printer.newPage():
                            raise ValueError('Драйвер не зміг передати наступну сторінку до принтера.')
                    scale = min(300 / 72, math.sqrt(MAX_PAGE_PIXELS / (page.rect.width * page.rect.height)))
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                    pixels = pixmap.samples
                    image = QImage(pixels, pixmap.width, pixmap.height, pixmap.stride, QImage.Format_RGB888)
                    if image.isNull():
                        raise ValueError('Не вдалося підготувати зображення сторінки.')
                    # Full physical size preserves the DOCX margins; do not fit
                    # the PDF into the printer's smaller printable rectangle.
                    dpi = printer.resolution()
                    target = QRectF(0, 0, page.rect.width * dpi / 72, page.rect.height * dpi / 72)
                    painter.drawImage(target, image)
                if not painter.end():
                    raise ValueError('Драйвер не підтвердив передавання примірника до черги.')
        except BaseException:
            printer.abort()
            if painter.isActive():
                painter.end()
            raise


def queue(settings: dict, result_path: Path, *, backend_factory=WindowsPrinter) -> dict:
    """Validate the whole batch, then record progress for each separate copy."""
    result = dict(ok=False, documents=0, copies=2, queued_copies=0, active_copy='',
                  printer=settings.get('printer', ''), duplex=settings.get('duplex', ''), error='')
    _save(result_path, result)
    try:
        paths = settings.get('documents')
        if (settings.get('copies') != 2 or type(settings.get('copies')) is not int
                or settings.get('duplex') not in DUPLEX_MODES or not isinstance(paths, list)
                or not 1 <= len(paths) <= 300 or not isinstance(result['printer'], str) or not result['printer'].strip()):
            raise ValueError('Некоректні параметри друку.')
        from .printing import _file_output_printer
        if _file_output_printer(result['printer']):
            raise ValueError('FILE_OUTPUT_PRINTER')
        for path in paths:
            _validate_pdf(path)
        backend = backend_factory(result['printer'], result['duplex'])
        for index, path in enumerate(paths, 1):
            for copy in (1, 2):
                result['active_copy'] = f'Завдання {index}, примірник {copy}'
                _save(result_path, result)
                backend.print_copy(path, f'ОЗтаУСЯ — завдання {index:03d} — примірник {copy}/2')
                result['queued_copies'] += 1
                result['active_copy'] = ''
                _save(result_path, result)
            result['documents'] += 1
        result['ok'] = True
    except Exception as exc:
        result['error'] = str(exc)[:1200]
    _save(result_path, result)
    return result


def main(argv=None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        return 2
    settings_path, result_path = map(Path, arguments)
    if os.name != 'nt':
        _save(result_path, dict(ok=False, error='Прямий друк доступний у Windows.'))
        return 1
    try:
        settings = json.loads(settings_path.read_text(encoding='utf-8'))
        return 0 if queue(settings, result_path)['ok'] else 1
    except Exception as exc:
        _save(result_path, dict(ok=False, error=str(exc)[:1200]))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
