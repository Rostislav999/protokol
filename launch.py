"""One-file startup: prepare local dependencies, run the app, open the browser.

Only the Python standard library is needed to execute this launcher initially.
System Python and Tesseract installations are never changed by this script.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser

from scripts.prepare_ocr import DEFAULT_DIRECTORY, prepare_models

ROOT = Path(__file__).resolve().parent
LOCK = ROOT / "requirements.lock"
INSTALLATION_GUIDE = "https://tesseract-ocr.github.io/tessdoc/Installation.html"


class LaunchError(RuntimeError):
    """An actionable launch failure for display in the terminal."""


def find_tesseract(environ=None, *, windows: bool | None = None) -> Path:
    environ = os.environ if environ is None else environ
    found = shutil.which("tesseract", path=environ.get("PATH"))
    if found:
        return Path(found)
    if windows is None:
        windows = os.name == "nt"
    if windows:
        candidates = []
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            if environ.get(variable):
                candidates.append(Path(environ[variable]) / "Tesseract-OCR/tesseract.exe")
        if environ.get("LOCALAPPDATA"):
            local = Path(environ["LOCALAPPDATA"])
            candidates.extend((local / "Programs/Tesseract-OCR/tesseract.exe", local / "Tesseract-OCR/tesseract.exe"))
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    raise LaunchError("Tesseract OCR 5 не найден. Установите его, затем снова запустите START_WINDOWS.bat.\n"
                      f"Инструкция по установке: {INSTALLATION_GUIDE}\n"
                      "Стандартный каталог Windows: C:\\Program Files\\Tesseract-OCR.\n"
                      "Языковые модели ukr, rus, eng программа загрузит сама.")


def verify_tesseract(executable: Path, environ: dict) -> None:
    try:
        result = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15, env=environ)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaunchError(f"Не удалось запустить Tesseract: {executable}.\nПричина: {exc}") from exc
    output = (result.stdout + "\n" + result.stderr).strip()
    first_line = next((line.strip()[:400] for line in output.splitlines() if line.strip()), "Tesseract не вывел сообщений.")
    if result.returncode:
        raise LaunchError(f"Tesseract найден, но завершился с ошибкой (код {result.returncode}).\n"
                          f"Файл: {executable}\nСообщение: {first_line}")
    # Windows distributions often print "tesseract v5.x", while Linux prints
    # "tesseract 5.x". Both describe the same supported major version.
    match = re.search(r"\btesseract(?:\.exe)?\s+(?:version\s+)?v?(\d+)(?:\.\d+)*", output, re.I)
    if not match:
        raise LaunchError(f"Tesseract найден, но формат его версии не распознан.\n"
                          f"Файл: {executable}\nОтвет: {first_line}")
    if int(match.group(1)) < 5:
        raise LaunchError(f"Обнаружена старая версия Tesseract: {first_line}. Требуется версия 5 или новее.\n"
                          f"Файл: {executable}")


def venv_python(root: Path = ROOT) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed_dependencies(python: Path) -> bool:
    """Check versions and native imports without importing the application."""
    if not python.is_file():
        return False
    requirements = [line.rstrip().removesuffix("\\").strip()
                    for line in LOCK.read_text(encoding="utf-8").splitlines()
                    if re.match(r"^[A-Za-z0-9_.-]+==", line)]
    code = (
        "import sys,json; from importlib.metadata import version,PackageNotFoundError; "
        "from packaging.requirements import Requirement; "
        "requirements=[Requirement(value) for value in json.loads(sys.argv[1])]; "
        "assert requirements and all((r.marker and not r.marker.evaluate()) or version(r.name) in r.specifier for r in requirements); "
        "import fastapi,uvicorn,multipart,PIL,fitz,cv2"
    )
    try:
        result = subprocess.run([str(python), "-c", code, json.dumps(requirements)], cwd=ROOT,
                                capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def ensure_dependencies(*, check_only: bool = False) -> Path:
    python = venv_python()
    stamp = ROOT / ".venv/protokol-lock.sha256"
    digest = sha256(LOCK.read_bytes()).hexdigest()
    current = installed_dependencies(python)
    if check_only:
        if not current:
            raise LaunchError("Зависимости ещё не установлены. Запустите START_WINDOWS.bat без параметров.")
        return python
    # Reuse an environment prepared by scripts/setup.sh as well as one prepared
    # by this launcher. Only new/changed or broken installations need the network.
    previous = stamp.read_text().strip() if stamp.exists() else ""
    if current and previous in ("", digest):
        if previous != digest:
            stamp.write_text(digest)
        return python
    if not python.is_file():
        print("Создание локального окружения Python…")
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], cwd=ROOT, check=True)
    print("Первая установка зависимостей. Требуется подключение к Интернету…")
    uv = shutil.which("uv")
    if uv:
        command = [uv, "pip", "install", "--python", str(python), "--require-hashes", "-r", str(LOCK)]
    else:
        has_pip = subprocess.run([str(python), "-m", "pip", "--version"],
                                 capture_output=True, timeout=30).returncode == 0
        if not has_pip:
            subprocess.run([str(python), "-m", "ensurepip"], cwd=ROOT, check=True)
        command = [str(python), "-m", "pip", "install", "--require-hashes", "-r", str(LOCK)]
    subprocess.run(command, cwd=ROOT, check=True)
    if not installed_dependencies(python):
        raise LaunchError("Зависимости не удалось проверить после установки. Сохраните текст ошибки и обратитесь за помощью.")
    stamp.write_text(digest)
    return python


def verify_ocr_runtime(python: Path, executable: Path, environ: dict) -> None:
    """Run the image-based check where Pillow was installed, not in bootstrap Python."""
    result = subprocess.run(
        [str(python), "-m", "scripts.tesseract_runtime", "--executable", str(executable),
         "--directory", str(DEFAULT_DIRECTORY)],
        cwd=ROOT, env=environ, timeout=210,
    )
    if result.returncode:
        raise LaunchError("Проверка распознавания не пройдена. Посмотрите сообщение об ошибке выше.")


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise LaunchError("PROTOKOL_PORT должен содержать номер порта, например 8000.") from exc
    if not 1024 <= port <= 65535:
        raise LaunchError("PROTOKOL_PORT должен быть в диапазоне 1024–65535.")
    return port


def configure_data_directory(environ: dict, *, root: Path = ROOT, windows: bool | None = None) -> Path:
    """Keep Windows numbering and libraries when a new ZIP is unpacked."""
    if environ.get("PROTOKOL_DATA_DIR"):
        return Path(environ["PROTOKOL_DATA_DIR"])
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return root / "data"
    base = Path(environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    destination = base / "Protokol/data"
    legacy = root / "data"
    if not destination.exists() and legacy.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="data-migration-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "data"
            shutil.copytree(legacy, staged, ignore=shutil.ignore_patterns("*-journal", "*-wal", "*-shm"))
            database = legacy / "numbering.sqlite3"
            if database.is_file():
                # SQLite backup also works if the previous instance is open.
                source = sqlite3.connect(database)
                target = sqlite3.connect(staged / "numbering.sqlite3")
                try:
                    source.backup(target)
                finally:
                    target.close()
                    source.close()
            try:
                staged.rename(destination)
            except FileExistsError:
                if not destination.is_dir():
                    raise
        print("Попередню бібліотеку й нумерацію скопійовано в постійний каталог Windows.")
    else:
        destination.mkdir(parents=True, exist_ok=True)
    environ["PROTOKOL_DATA_DIR"] = str(destination)
    print(f"Каталог бібліотеки та нумерації: {destination}")
    return destination


def app_is_ready(url: str) -> bool:
    # Loopback stays local even when corporate Internet proxies are configured.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/health", timeout=1) as response:
            data = json.loads(response.read(65536))
        if not (data.get("status") == "ok" and data.get("local_processing") is True
                and isinstance(data.get("templates"), int) and data.get("ocr_installed") is True):
            return False
        # Distinguish our app from another local service with a generic health endpoint.
        with opener.open(url + "/", timeout=1) as response:
            beginning = response.read(8192).decode("utf-8", errors="replace")
        return "<title>Протокол" in beginning
    except (OSError, ValueError, urllib.error.URLError):
        return False


def port_is_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def open_app(url: str, *, no_browser: bool) -> None:
    print(f"Программа доступна в браузере: {url}")
    if not no_browser:
        try:
            if not webbrowser.open(url):
                print("Откройте этот адрес в браузере вручную.")
        except OSError:
            print("Не удалось открыть браузер автоматически. Откройте адрес вручную.")


def serve(python: Path, environ: dict, *, no_browser: bool = False) -> int:
    port = port_number(environ.get("PROTOKOL_PORT", "8000"))
    url = f"http://127.0.0.1:{port}"
    if app_is_ready(url):
        print("Программа уже запущена; открываем её окно.")
        open_app(url, no_browser=no_browser)
        return 0
    if port_is_busy(port):
        raise LaunchError(f"Порт {port} занят другой программой. Закройте её или задайте PROTOKOL_PORT=8001.")
    process = subprocess.Popen([str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                               cwd=ROOT, env=environ)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LaunchError("Сервер остановился при запуске. Посмотрите сообщение об ошибке выше.")
            if app_is_ready(url):
                open_app(url, no_browser=no_browser)
                print("Не закрывайте это окно во время работы. Для остановки нажмите Ctrl+C.")
                return process.wait()
            time.sleep(0.25)
        raise LaunchError("Сервер не стал доступен за 45 секунд. Проверьте сообщения выше и повторите запуск.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Запуск программы Протокол.")
    parser.add_argument("--check", action="store_true", help="Проверить готовность без установки, сети, сервера и браузера.")
    parser.add_argument("--no-browser", action="store_true", help="Запустить сервер, не открывая браузер автоматически.")
    args = parser.parse_args(argv)
    try:
        if not (3, 12) <= sys.version_info[:2] < (3, 14):
            raise LaunchError("Установите Python 3.12 или 3.13. Для этой версии программы рекомендуется Python 3.12.")
        executable = find_tesseract()
        environ = dict(os.environ)
        environ["PATH"] = str(executable.parent) + os.pathsep + environ.get("PATH", "")
        environ["TESSDATA_PREFIX"] = str(DEFAULT_DIRECTORY)
        verify_tesseract(executable, environ)
        python = ensure_dependencies(check_only=args.check)
        prepare_models(download=not args.check)
        verify_ocr_runtime(python, executable, environ)
        if args.check:
            print("Проверка завершена: Python, зависимости, Tesseract и языковые модели готовы.")
            return 0
        configure_data_directory(environ)
        return serve(python, environ, no_browser=args.no_browser)
    except KeyboardInterrupt:
        print("\nПрограмма остановлена.")
        return 0
    except (LaunchError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"\nНе удалось запустить программу: {exc}", file=sys.stderr)
        print("Подробная инструкция находится в README.md рядом с START_WINDOWS.bat.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
