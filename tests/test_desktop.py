"""Exercise the private desktop runtime and real in-process application server."""
from hashlib import sha256
from pathlib import Path
import shutil
import socket
import sys

import pytest

import desktop
from scripts.prepare_ocr import BUNDLED_DIRECTORY, CHECKSUMS


@pytest.fixture
def runtime_root(tmp_path):
    root = tmp_path / "Протокол з пробілами"
    (root / "tesseract").mkdir(parents=True)
    (root / "tesseract/tesseract.exe").write_bytes(b"test runtime presence")
    shutil.copytree(BUNDLED_DIRECTORY, root / "assets/tessdata")
    return root


def test_private_runtime_installs_models_offline_and_selects_own_tesseract(runtime_root, tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("Network used"))
    environment = {"LOCALAPPDATA": str(tmp_path / "Користувач"), "PATH": "original-path"}
    directory, updated = desktop.prepare_runtime(runtime_root, environment)
    assert environment["PATH"] == "original-path"
    assert directory == tmp_path / "Користувач/Protokol/data"
    assert updated["PATH"].startswith(str(runtime_root / "tesseract"))
    assert updated["TESSDATA_PREFIX"] == str(directory / "tessdata")
    for language, checksum in CHECKSUMS.items():
        target = directory / f"tessdata/{language}.traineddata"
        assert sha256(target.read_bytes()).hexdigest() == checksum
        target.touch()
    before = {p.name: p.stat().st_mtime_ns for p in (directory / "tessdata").iterdir()}
    desktop.prepare_runtime(runtime_root, environment)
    after = {p.name: p.stat().st_mtime_ns for p in (directory / "tessdata").iterdir()}
    assert before == after


def test_corrupt_bundled_model_cannot_replace_existing_model(runtime_root, tmp_path):
    directory = tmp_path / "data"
    (directory / "tessdata").mkdir(parents=True)
    (directory / "tessdata/ukr.traineddata").write_bytes(b"existing damaged model")
    (runtime_root / "assets/tessdata/ukr.traineddata").write_bytes(b"unverified new model")
    with pytest.raises(RuntimeError, match="Пошкоджено"):
        desktop.prepare_runtime(runtime_root, {"PROTOKOL_DATA_DIR": str(directory)})
    assert (directory / "tessdata/ukr.traineddata").read_bytes() == b"existing damaged model"
    assert not list(directory.rglob("*.tmp"))


def test_private_runtime_requires_bundled_binary_instead_of_system_install(runtime_root):
    (runtime_root / "tesseract/tesseract.exe").unlink()
    with pytest.raises(RuntimeError, match="комплектний Tesseract"):
        desktop.prepare_runtime(runtime_root, {"PATH": "/usr/bin"})


@pytest.mark.parametrize("url,allowed", [
    ("http://127.0.0.1:12345/", True),
    ("blob:http://127.0.0.1:12345/local-document", True),
    ("about:blank", True),
    ("data:application/pdf;base64,AA==", True),
    ("qrc:/qtwebengine/pdfjs/web/viewer.html", True),
    ("https://example.com/upload", False),
    ("http://127.0.0.1:9999/", False),
    ("http://127.0.0.1:12345@evil.example/", False),
    ("http://evil.example:12345/", False),
    ("blob:https://example.com/document", False),
    ("file:///C:/Users/private.txt", False),
    ("http://127.0.0.1:invalid/", False),
])
def test_renderer_allows_only_own_server_and_internal_documents(url, allowed):
    assert desktop.local_url_allowed(url, 12345) == allowed


def test_in_process_server_exports_real_workbook_and_releases_listener(tmp_path, monkeypatch):
    # Unlike a frozen executable, the test runner has Tesseract on its normal PATH.
    from app.main import create_app
    monkeypatch.setattr(shutil, "which", lambda name: "/bundled/tesseract.exe" if name == "tesseract" else None)
    with desktop.LocalServer(create_app(tmp_path / "application-data")) as server:
        port = server.port
        thread = server.thread
        workbook = desktop.check_application(server)
        assert workbook.startswith(b"PK")
    assert not thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_import_and_runtime_checks_do_not_require_graphical_dependencies():
    # Qt is imported only by run_window or error dialogs, never by --check helpers.
    assert "PySide6.QtWidgets" not in sys.modules
