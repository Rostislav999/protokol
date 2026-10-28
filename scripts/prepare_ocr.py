"""Install official, pinned OCR models with checksum and TLS verification."""
from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import tempfile
import urllib.request

COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
CHECKSUMS = {
    "ukr": "d59e53e2bded32f4445f124b4b00240fcac7e8044c003ab822ccb94f0b3db59b",
    "rus": "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}
MAX_MODEL_BYTES = 30 * 1024 * 1024
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / ".local/tessdata"


def prepare_models(directory: Path = DEFAULT_DIRECTORY, *, download: bool = True) -> None:
    """Verify each model; atomically replace only a fully verified download.

    ``download=False`` is a strictly offline, read-only verification mode.
    """
    directory = Path(directory)
    for language, expected in CHECKSUMS.items():
        target = directory / f"{language}.traineddata"
        if target.is_file() and sha256(target.read_bytes()).hexdigest() == expected:
            print(f"OCR {language}: проверено")
            continue
        if not download:
            raise RuntimeError(f"Модель OCR {language} отсутствует или повреждена. Запустите START_WINDOWS.bat для установки.")
        url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{COMMIT}/{language}.traineddata"
        print(f"Загрузка языковой модели {language}…")
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(MAX_MODEL_BYTES + 1)
        if len(data) > MAX_MODEL_BYTES or sha256(data).hexdigest() != expected:
            raise RuntimeError(f"Неверная контрольная сумма модели OCR {language}. Файл не установлен; повторите загрузку позже.")
        directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=f"{language}-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(f"OCR {language}: установлено и проверено")


def main() -> int:
    parser = argparse.ArgumentParser(description="Подготовка проверенных языковых моделей Tesseract.")
    parser.add_argument("--check", action="store_true", help="Только проверить существующие файлы, без скачивания.")
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args()
    try:
        prepare_models(args.directory, download=not args.check)
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
