"""Self-contained desktop entry point; no system Python or browser is required.

PySide6 is imported only for the window. ``--check`` exercises the bundled OCR
and a real local HTTP/Excel workflow without starting a graphical interface.
"""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
from io import BytesIO
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit
import urllib.request
from zipfile import ZipFile

LOG = logging.getLogger("protokol.desktop")


def bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def prepare_runtime(root: Path | None = None, environ: dict | None = None) -> tuple[Path, dict]:
    """Select private binaries and install verified models without network access."""
    from launch import configure_data_directory
    from scripts.prepare_ocr import CHECKSUMS, MAX_MODEL_BYTES

    root = root or bundle_root()
    env = dict(os.environ if environ is None else environ)
    directory = configure_data_directory(env, root=root, windows=True)
    executable = root / "tesseract" / "tesseract.exe"
    if not executable.is_file():
        raise RuntimeError("Не знайдено комплектний Tesseract. Перевстановіть програму з установника.")
    models = directory / "tessdata"
    models.mkdir(parents=True, exist_ok=True)
    for language, checksum in CHECKSUMS.items():
        target = models / f"{language}.traineddata"
        if target.is_file() and target.stat().st_size <= MAX_MODEL_BYTES and sha256(target.read_bytes()).hexdigest() == checksum:
            continue
        source = root / "assets" / "tessdata" / target.name
        if not source.is_file() or source.stat().st_size > MAX_MODEL_BYTES:
            raise RuntimeError(f"Не знайдено комплектну модель OCR {language}. Перевстановіть програму.")
        data = source.read_bytes()
        if sha256(data).hexdigest() != checksum:
            raise RuntimeError(f"Пошкоджено комплектну модель OCR {language}. Перевстановіть програму.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=models, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    env["PATH"] = str(executable.parent) + os.pathsep + env.get("PATH", "")
    env["TESSDATA_PREFIX"] = str(models)
    return directory, env


def configure_logging(directory: Path) -> Path:
    logs = directory.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "desktop.log"
    handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return path


def local_url_allowed(url: str, port: int) -> bool:
    """Restrict the embedded renderer to this instance and internal documents."""
    if url.startswith("blob:"):
        return local_url_allowed(url[5:], port)
    try:
        parsed = urlsplit(url)
        if parsed.scheme == "http":
            return parsed.hostname == "127.0.0.1" and parsed.port == port and not parsed.username and not parsed.password
        return parsed.scheme in {"about", "data", "qrc", "chrome-extension"}
    except ValueError:
        return False


def request_json(url: str, payload=None, *, form=False):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url)
    if payload is not None:
        request.data = (urlencode(payload) if form else json.dumps(payload)).encode("utf-8")
        request.add_header("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
    with opener.open(request, timeout=30) as response:
        return json.load(response)


class LocalServer:
    """Own one ephemeral loopback listener and stop it when the window closes."""

    def __init__(self, app):
        import uvicorn

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Keep the listener reserved until Uvicorn takes it, avoiding a port race.
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_config=None, access_log=False)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True)

    def __enter__(self):
        self.thread.start()
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if not self.thread.is_alive():
                    raise RuntimeError("Локальний сервер зупинився під час запуску.")
                if self.server.started:
                    health = request_json(self.url + "/api/health")
                    if health.get("status") == "ok":
                        return self
                time.sleep(0.1)
            raise RuntimeError("Локальний сервер не запустився за 45 секунд.")
        except BaseException:
            self.close()
            raise

    def close(self):
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=10)
        if self.thread.is_alive():
            self.server.force_exit = True
            self.thread.join(timeout=2)
        self.socket.close()

    def __exit__(self, *exc):
        self.close()


def check_application(server: LocalServer) -> bytes:
    """Prove the installed templates, routes, and Excel writer work together."""
    health = request_json(server.url + "/api/health")
    if not (health.get("local_processing") and health.get("ocr_installed") and health.get("templates", 0) >= 22):
        raise RuntimeError("Не пройдено перевірку комплектності програми.")
    preview = request_json(server.url + "/api/excel/from-act", {
        "common": {"supplier": "код ЄДРПОУ: 00001234"},
        "items": [{"protocol_number": "desktop-check", "protocol_date": "2026-10-30", "product_name": "Перевірка"}],
    })
    result = request_json(server.url + "/api/excel/generate", {
        "payload": json.dumps({"confirmed": True, "records": preview["records"]}),
    }, form=True)
    workbook = base64.b64decode(result["workbook_base64"], validate=True)
    with ZipFile(BytesIO(workbook)) as archive:
        from lxml import etree
        from app.protocol_excel import NS, cell_text
        sheet = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        cell = sheet.find(".//s:c[@r='M2']", NS)
        if cell is None or cell.get("t") != "inlineStr" or cell_text(cell, []) != "00001234":
            raise RuntimeError("Не пройдено перевірку експорту Excel.")
    LOG.info("Bundled application check passed: OCR, templates, HTTP, Excel")
    return workbook


def run_window(server: LocalServer, *, smoke_workbook: bytes | None = None) -> int:
    from PySide6.QtCore import QTimer, QUrl, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Protokol.Desktop")
    application = QApplication([sys.argv[0]])
    application.setApplicationName("ОЗтаУСЯ")
    application.setOrganizationName("Protokol")
    application.setWindowIcon(QIcon(str(bundle_root() / "app/static/app.ico")))
    profile = QWebEngineProfile(application)  # Off the record: no persistent browsing history.
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)

    class LocalRequests(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if not local_url_allowed(info.requestUrl().toString(), server.port):
                info.block(True)

    interceptor = LocalRequests(profile)
    profile.setUrlRequestInterceptor(interceptor)
    children = []

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            return local_url_allowed(url.toString(), server.port)

        def javaScriptAlert(self, origin, message):
            QMessageBox.information(window, "ОЗтаУСЯ", message)

        def javaScriptConfirm(self, origin, message):
            return QMessageBox.question(window, "ОЗтаУСЯ", message) == QMessageBox.StandardButton.Yes

        def createWindow(self, window_type):
            child = QMainWindow(window)
            child.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            child.setWindowTitle("ОЗтаУСЯ — оригінал документа")
            child.resize(1000, 800)
            view = QWebEngineView(child)
            page = Page(profile, view)
            page.settings().setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
            page.settings().setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
            view.setPage(page)
            child.setCentralWidget(view)
            children.append(child)
            child.destroyed.connect(lambda: children.remove(child) if child in children else None)
            child.show()
            return page

    window = QMainWindow()
    window.setWindowTitle("ОЗтаУСЯ")
    window.resize(1280, 900)
    window.setMinimumSize(900, 650)
    window.statusBar().showMessage("Обробка документів виконується на цьому комп’ютері")
    view = QWebEngineView(window)
    page = Page(profile, view)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
    view.setPage(page)
    window.setCentralWidget(view)
    smoke = {"done": False, "success": False, "loaded": False}
    smoke_directory = tempfile.TemporaryDirectory(prefix="protokol-desktop-check-") if smoke_workbook else None

    def finish_smoke(success):
        if smoke["done"]:
            return
        smoke["done"], smoke["success"] = True, success
        LOG.info("Native window smoke check: %s", "passed" if success else "failed")
        application.exit(0 if success else 1)

    def download_requested(download):
        if smoke_workbook is not None:
            path = Path(smoke_directory.name) / "desktop-check.xlsx"
        else:
            suggested = Path.home() / "Downloads" / Path(download.suggestedFileName()).name
            selected, _ = QFileDialog.getSaveFileName(window, "Зберегти файл", str(suggested))
            if not selected:
                download.cancel()
                return
            path = Path(selected)
        download.setDownloadDirectory(str(path.parent))
        download.setDownloadFileName(path.name)

        def finished():
            state = download.state()
            if state == download.DownloadState.DownloadCompleted:
                if smoke_workbook is not None:
                    finish_smoke(path.read_bytes() == smoke_workbook)
                else:
                    window.statusBar().showMessage(f"Файл збережено: {path}", 10000)
            elif state in (download.DownloadState.DownloadInterrupted, download.DownloadState.DownloadCancelled):
                if smoke_workbook is not None:
                    finish_smoke(False)
                elif state == download.DownloadState.DownloadInterrupted:
                    QMessageBox.warning(window, "ОЗтаУСЯ", "Не вдалося зберегти файл: " + download.interruptReasonString())

        download.isFinishedChanged.connect(finished)
        download.accept()

    profile.downloadRequested.connect(download_requested)

    def loaded(success):
        if smoke_workbook is None:
            if not success:
                QMessageBox.critical(window, "ОЗтаУСЯ", "Не вдалося відкрити локальний інтерфейс програми.")
            return
        if smoke["loaded"]:
            return
        smoke["loaded"] = True
        if not success:
            finish_smoke(False)
            return
        encoded = json.dumps(base64.b64encode(smoke_workbook).decode("ascii"))
        script = """(() => {
          if (!document.querySelector('#generate') || !document.querySelector('#act-file') ||
              !document.styleSheets.length || !document.title.includes('ОЗтаУСЯ')) return false;
          const bytes = Uint8Array.from(atob(%s), c => c.charCodeAt(0));
          const link = document.createElement('a');
          link.href = URL.createObjectURL(new Blob([bytes], {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
          link.download = 'desktop-check.xlsx'; document.body.append(link); link.click(); link.remove();
          return true;
        })()""" % encoded
        page.runJavaScript(script, lambda result: finish_smoke(False) if result is not True else None)

    page.loadFinished.connect(loaded)
    if smoke_workbook is not None:
        QTimer.singleShot(45000, lambda: finish_smoke(False))
    view.setUrl(QUrl(server.url))
    window.show()
    try:
        code = application.exec()
        return code if smoke_workbook is None else (0 if smoke["success"] else 1)
    finally:
        # Pages must be destroyed before their profile, including popup previews.
        for child in list(children):
            child.close()
        window.close()
        page.deleteLater()
        application.processEvents()
        if smoke_directory is not None:
            smoke_directory.cleanup()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ОЗтаУСЯ — локальна програма для Windows.")
    parser.add_argument("--check", action="store_true", help="Перевірити комплектний OCR та експорт Excel без вікна.")
    parser.add_argument("--smoke-test", action="store_true", help="Перевірити вікно й збереження файлу, потім закрити програму.")
    args = parser.parse_args(argv)
    log_path = None
    try:
        directory, environ = prepare_runtime()
        os.environ.update(environ)
        log_path = configure_logging(directory)
        from scripts.tesseract_runtime import verify_runtime
        verify_runtime(executable=bundle_root() / "tesseract/tesseract.exe", models_directory=Path(environ["TESSDATA_PREFIX"]))
        from app.main import create_app
        with LocalServer(create_app(directory)) as server:
            workbook = check_application(server) if args.check or args.smoke_test else None
            if args.check:
                return 0
            return run_window(server, smoke_workbook=workbook)
    except Exception as exc:
        LOG.exception("Desktop startup failed")
        message = str(exc) + (f"\n\nЖурнал: {log_path}" if log_path else "")
        if args.check or args.smoke_test:
            if sys.stderr is not None:
                print(message, file=sys.stderr)
        else:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox
                application = QApplication.instance() or QApplication([sys.argv[0]])
                QMessageBox.critical(None, "ОЗтаУСЯ — помилка запуску", message)
            except Exception:
                if sys.stderr is not None:
                    print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
