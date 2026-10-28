from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

import launch
from scripts import prepare_ocr


def test_offline_model_check_never_downloads_or_creates_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_ocr.urllib.request, "urlopen", lambda *a, **k: pytest.fail("Network used"))
    directory = tmp_path / "missing"
    with pytest.raises(RuntimeError, match="отсутствует"):
        prepare_ocr.prepare_models(directory, download=False)
    assert not directory.exists()


def test_verified_model_is_reused_without_network(tmp_path, monkeypatch):
    data = b"valid model"
    monkeypatch.setattr(prepare_ocr, "CHECKSUMS", {"ukr": sha256(data).hexdigest()})
    (tmp_path / "ukr.traineddata").write_bytes(data)
    monkeypatch.setattr(prepare_ocr.urllib.request, "urlopen", lambda *a, **k: pytest.fail("Network used"))
    prepare_ocr.prepare_models(tmp_path)


def test_bad_model_download_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "ukr.traineddata"
    target.write_bytes(b"old data")
    monkeypatch.setattr(prepare_ocr, "CHECKSUMS", {"ukr": sha256(b"trusted").hexdigest()})
    monkeypatch.setattr(prepare_ocr.urllib.request, "urlopen", lambda *a, **k: BytesIO(b"bad data"))
    with pytest.raises(RuntimeError, match="контрольная сумма"):
        prepare_ocr.prepare_models(tmp_path)
    assert target.read_bytes() == b"old data"
    assert not list(tmp_path.glob("*.tmp"))


def test_verified_model_download_installs_atomically(tmp_path, monkeypatch):
    data = b"verified model"
    monkeypatch.setattr(prepare_ocr, "CHECKSUMS", {"ukr": sha256(data).hexdigest()})
    monkeypatch.setattr(prepare_ocr.urllib.request, "urlopen", lambda *a, **k: BytesIO(data))
    prepare_ocr.prepare_models(tmp_path)
    assert (tmp_path / "ukr.traineddata").read_bytes() == data
    assert not list(tmp_path.glob("*.tmp"))


def test_windows_tesseract_found_in_standard_directory_with_spaces(tmp_path, monkeypatch):
    executable = tmp_path / "Program Files/Tesseract-OCR/tesseract.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test")
    monkeypatch.setattr(launch.shutil, "which", lambda *a, **k: None)
    assert launch.find_tesseract({"ProgramFiles": str(tmp_path / "Program Files")}, windows=True) == executable


def test_missing_tesseract_has_actionable_error(monkeypatch):
    monkeypatch.setattr(launch.shutil, "which", lambda *a, **k: None)
    with pytest.raises(launch.LaunchError, match="Установите"):
        launch.find_tesseract({}, windows=True)


@pytest.mark.parametrize("value", ["0", "80", "65536", "abc"])
def test_invalid_port_is_rejected(value):
    with pytest.raises(launch.LaunchError):
        launch.port_number(value)


def test_existing_app_reuses_server_and_does_not_spawn_another(monkeypatch):
    monkeypatch.setattr(launch, "app_is_ready", lambda _: True)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: pytest.fail("Duplicate server"))
    opened = []
    monkeypatch.setattr(launch, "open_app", lambda url, **k: opened.append(url))
    assert launch.serve(Path("python"), {}, no_browser=True) == 0
    assert opened == ["http://127.0.0.1:8000"]


def test_busy_unrelated_port_is_actionable_error(monkeypatch):
    monkeypatch.setattr(launch, "app_is_ready", lambda _: False)
    monkeypatch.setattr(launch, "port_is_busy", lambda _: True)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: pytest.fail("Server started on occupied port"))
    with pytest.raises(launch.LaunchError, match="занят другой"):
        launch.serve(Path("python"), {})


def test_check_mode_never_serves_installs_or_opens_browser(monkeypatch):
    monkeypatch.setattr(launch, "find_tesseract", lambda: Path("/test/tesseract"))
    monkeypatch.setattr(launch, "verify_tesseract", lambda *a: None)
    checks = []
    def dependencies(*, check_only):
        assert check_only
        checks.append("dependencies")
        return Path("python")
    def models(*, download):
        assert not download
        checks.append("models")
    monkeypatch.setattr(launch, "ensure_dependencies", dependencies)
    monkeypatch.setattr(launch, "prepare_models", models)
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="langs\nukr\nrus\neng\n"))
    monkeypatch.setattr(launch, "serve", lambda *a, **k: pytest.fail("Server started"))
    monkeypatch.setattr(launch.webbrowser, "open", lambda *a, **k: pytest.fail("Browser opened"))
    assert launch.main(["--check"]) == 0
    assert checks == ["dependencies", "models"]


def test_dependency_check_respects_inactive_platform_markers(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock"
    lock.write_text("missing-protokol-test-package==1.0 ; sys_platform == 'not-a-real-os' \\\n")
    monkeypatch.setattr(launch, "LOCK", lock)
    assert launch.installed_dependencies(Path(sys.executable))
    lock.write_text("missing-protokol-test-package==1.0\n")
    assert not launch.installed_dependencies(Path(sys.executable))


def test_browser_opens_after_server_readiness(monkeypatch):
    events = []
    calls = iter([False, False, True])
    def ready(_):
        result = next(calls)
        events.append(("ready", result))
        return result
    class Process:
        finished = False
        def poll(self):
            return 0 if self.finished else None
        def wait(self):
            self.finished = True
            return 0
    monkeypatch.setattr(launch, "app_is_ready", ready)
    monkeypatch.setattr(launch, "port_is_busy", lambda _: False)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(launch.time, "sleep", lambda _: None)
    monkeypatch.setattr(launch, "open_app", lambda *a, **k: events.append(("browser", True)))
    assert launch.serve(Path("python"), {}) == 0
    assert events == [("ready", False), ("ready", False), ("ready", True), ("browser", True)]
