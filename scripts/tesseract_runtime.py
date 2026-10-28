"""Run Tesseract with relative file names, including on Windows Unicode paths."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .prepare_ocr import DEFAULT_DIRECTORY

REQUIRED_LANGUAGES = ("ukr", "rus", "eng")


def data_directory(environ: dict | None = None) -> Path:
    env = os.environ if environ is None else environ
    directory = Path(env.get("TESSDATA_PREFIX") or DEFAULT_DIRECTORY).expanduser().resolve()
    if not (directory / "eng.traineddata").exists() and (directory / "tessdata/eng.traineddata").exists():
        directory /= "tessdata"
    return directory


def run_tesseract(args: list[str], *, executable="tesseract", models_directory: Path | None = None,
                  environ: dict | None = None, timeout: int = 15) -> subprocess.CompletedProcess:
    directory = Path(models_directory).expanduser().resolve() if models_directory is not None else data_directory(environ)
    env = dict(os.environ if environ is None else environ)
    # Some Windows builds cannot open a Cyrillic absolute TESSDATA_PREFIX.
    # Python sets cwd with the Unicode Windows API; Tesseract sees only '.'.
    env["TESSDATA_PREFIX"] = "."
    try:
        return subprocess.run([str(executable), *args, "--tessdata-dir", "."], cwd=directory, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Tesseract не відповів за {timeout} секунд.") from exc
    except OSError as exc:
        raise ValueError(f"Не вдалося запустити Tesseract. Каталог моделей: {directory}. Причина: {exc}") from exc


def _diagnostic(result) -> str:
    text = "\n".join(s.strip() for s in (result.stderr or "", result.stdout or "") if s.strip())
    return text[:1800] or f"Tesseract завершився з кодом {result.returncode} без пояснення."


def ocr_image(image, language: str, psm: int = 3, *, executable="tesseract",
              models_directory: Path | None = None, environ: dict | None = None, timeout: int = 90) -> str:
    directory = Path(models_directory).expanduser().resolve() if models_directory is not None else data_directory(environ)
    try:
        # The temporary name and every CLI path are ASCII and relative to cwd.
        # This also avoids Unicode paths in the user's Windows TEMP directory.
        with tempfile.TemporaryDirectory(prefix="protokol-ocr-", dir=directory) as temporary:
            path = Path(temporary) / "page.png"
            image.save(path, "PNG")
            relative = path.relative_to(directory).as_posix()
            result = run_tesseract([relative, "stdout", "-l", language, "--psm", str(psm),
                                    "-c", "preserve_interword_spaces=1"], executable=executable,
                                   models_directory=directory, environ=environ, timeout=timeout)
    except OSError as exc:
        raise ValueError(f"Не вдалося підготувати зображення OCR у {directory}: {exc}") from exc
    if result.returncode or re.search(r"Failed loading language|couldn.t load any languages", result.stderr or "", re.I):
        raise ValueError("Tesseract не зміг розпізнати зображення:\n" + _diagnostic(result))
    return result.stdout.strip()


def _smoke(language: str, **kwargs) -> None:
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (900, 150), "white")
    ImageDraw.Draw(image).text((25, 30), "PROTOKOL 123", fill="black", font=ImageFont.load_default(size=64))
    text = ocr_image(image, language, 7, **kwargs)
    if "123" not in text or "PROTOKOL" not in text.upper():
        raise ValueError("Tesseract запустився, але не розпізнав контрольний текст. Відповідь: " + text[:400])


def available_languages(*, executable="tesseract", models_directory: Path | None = None,
                        environ: dict | None = None) -> tuple[str, list[str]]:
    directory = Path(models_directory).expanduser().resolve() if models_directory is not None else data_directory(environ)
    result = run_tesseract(["--list-langs"], executable=executable, models_directory=directory, environ=environ)
    listed = set(re.findall(r"(?m)^\s*([a-z]{3})\s*$", (result.stdout or "") + "\n" + (result.stderr or "")))
    selected = [lang for lang in REQUIRED_LANGUAGES if lang in listed]
    if result.returncode == 0 and selected:
        warnings = [] if "ukr" in selected else ["Українська модель OCR не доступна. Перевірте встановлені моделі."]
        return "+".join(selected), warnings
    # A broken --list-langs implementation must not mask a working OCR engine.
    # Accept it only after a real multilingual request proves model loading.
    if all((directory / f"{lang}.traineddata").is_file() for lang in REQUIRED_LANGUAGES):
        language = "+".join(REQUIRED_LANGUAGES)
        try:
            _smoke(language, executable=executable, models_directory=directory, environ=environ)
        except ValueError as exc:
            raise ValueError("Не вдалося перевірити OCR. Вивід --list-langs:\n" + _diagnostic(result)
                             + "\nПеревірка розпізнавання:\n" + str(exc)) from exc
        return language, ["Список мов Tesseract недоступний, але реальне розпізнавання трьома мовами перевірено."]
    raise ValueError("Tesseract не бачить підготовлених мовних моделей:\n" + _diagnostic(result))


def verify_runtime(*, executable="tesseract", models_directory: Path | None = None,
                   environ: dict | None = None) -> tuple[str, list[str]]:
    language, warnings = available_languages(executable=executable, models_directory=models_directory, environ=environ)
    missing = set(REQUIRED_LANGUAGES) - set(language.split("+"))
    if missing:
        raise ValueError("Не вистачає моделей OCR: " + ", ".join(sorted(missing)))
    _smoke(language, executable=executable, models_directory=models_directory, environ=environ)
    return language, warnings


def main(argv=None) -> int:
    """Check real OCR inside the application's prepared Python environment."""
    parser = argparse.ArgumentParser(description="Проверка распознавания Tesseract.")
    parser.add_argument("--executable", default="tesseract")
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args(argv)
    try:
        _, warnings = verify_runtime(executable=args.executable, models_directory=args.directory)
    except (ValueError, OSError, ImportError) as exc:
        print(f"Не удалось проверить распознавание: {exc}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
