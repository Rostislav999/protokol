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


@pytest.mark.parametrize("stdout,stderr", [
    ("tesseract 5.5.0\n leptonica-1.84.1\n", ""),
    ("tesseract v5.5.0.20241111\n leptonica-1.85.0\n", ""),
    ("Tesseract V5.3.3.20231005\r\n", ""),
    ("", "tesseract v5.4.0.20240606\n"),
])
def test_tesseract_version_accepts_linux_and_windows_builds(monkeypatch, stdout, stderr):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(launch.subprocess, "run", run)
    executable = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
    environ = {"PATH": "existing environment"}
    launch.verify_tesseract(executable, environ)
    assert calls[0][0] == [str(executable), "--version"]
    assert calls[0][1]["env"] is environ
    assert calls[0][1]["timeout"] == 15


def test_tesseract_old_version_message_identifies_actual_version_and_path(monkeypatch):
    executable = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="tesseract v4.1.1\n", stderr=""))
    with pytest.raises(launch.LaunchError) as raised:
        launch.verify_tesseract(executable, {})
    message = str(raised.value)
    assert "4.1.1" in message
    assert str(executable) in message
    assert "5" in message


def test_unknown_tesseract_version_is_distinct_from_an_old_version(monkeypatch):
    executable = Path("C:/OCR/tesseract.exe")
    result = SimpleNamespace(returncode=0, stdout="unknown vendor build\n", stderr="")
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: result)
    with pytest.raises(launch.LaunchError) as unknown:
        launch.verify_tesseract(executable, {})
    result.stdout = "tesseract v4.1.1\n"
    with pytest.raises(launch.LaunchError) as old:
        launch.verify_tesseract(executable, {})
    assert str(unknown.value) != str(old.value)
    assert "unknown vendor build" in str(unknown.value)
    assert str(executable) in str(unknown.value)


def test_failed_tesseract_command_reports_actual_failure_not_a_version_claim(monkeypatch):
    executable = Path("C:/OCR/tesseract.exe")
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout="", stderr="required DLL is missing\n"))
    with pytest.raises(launch.LaunchError) as raised:
        launch.verify_tesseract(executable, {})
    assert "required DLL is missing" in str(raised.value)
    assert str(executable) in str(raised.value)


def test_tesseract_diagnostic_is_bounded_and_output_decoding_is_tolerant(monkeypatch):
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=1, stdout="", stderr="failure " + "x" * 50000)
    monkeypatch.setattr(launch.subprocess, "run", run)
    with pytest.raises(launch.LaunchError) as raised:
        launch.verify_tesseract(Path("C:/OCR/tesseract.exe"), {})
    assert len(str(raised.value)) < 5000
    assert "failure" in str(raised.value)
    assert calls[0]["encoding"] == "utf-8"
    assert calls[0]["errors"] == "replace"


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
    monkeypatch.setattr(launch, "verify_ocr_runtime", lambda *a: checks.append("ocr"))
    monkeypatch.setattr(launch, "serve", lambda *a, **k: pytest.fail("Server started"))
    monkeypatch.setattr(launch.webbrowser, "open", lambda *a, **k: pytest.fail("Browser opened"))
    assert launch.main(["--check"]) == 0
    assert checks == ["dependencies", "models", "ocr"]


def test_ocr_check_uses_prepared_python_and_preserves_environment(tmp_path, monkeypatch):
    python = tmp_path / "Програма з пробілами/.venv/Scripts/python.exe"
    executable = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
    environ = {"PATH": "existing path", "TESSDATA_PREFIX": "Моделі", "HTTP_PROXY": "http://proxy:8080"}
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launch.subprocess, "run", run)
    launch.verify_ocr_runtime(python, executable, environ)
    command, options = calls[0]
    assert command == [str(python), "-m", "scripts.tesseract_runtime", "--executable", str(executable),
                       "--directory", str(launch.DEFAULT_DIRECTORY)]
    assert options["cwd"] == launch.ROOT
    assert options["env"] is environ
    assert options["timeout"] == 210


def test_failed_ocr_check_prevents_server_start(monkeypatch):
    monkeypatch.setattr(launch, "find_tesseract", lambda: Path("tesseract"))
    monkeypatch.setattr(launch, "verify_tesseract", lambda *a: None)
    monkeypatch.setattr(launch, "ensure_dependencies", lambda **k: Path(".venv/python"))
    monkeypatch.setattr(launch, "prepare_models", lambda **k: None)
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    monkeypatch.setattr(launch, "serve", lambda *a, **k: pytest.fail("Server started without working OCR"))
    assert launch.main(["--no-browser"]) == 1


def test_windows_data_location_survives_new_archive_and_preserves_legacy(tmp_path):
    old = tmp_path / "archive-old"
    (old / "data").mkdir(parents=True)
    (old / "data/library.json").write_text("custom library")
    environ = {"LOCALAPPDATA": str(tmp_path / "App Data")}
    destination = launch.configure_data_directory(environ, root=old, windows=True)
    assert destination == tmp_path / "App Data/Protokol/data"
    assert (destination / "library.json").read_text() == "custom library"
    new = tmp_path / "archive-new"
    (new / "data").mkdir(parents=True)
    (new / "data/library.json").write_text("must not overwrite")
    next_env = {"LOCALAPPDATA": str(tmp_path / "App Data")}
    assert launch.configure_data_directory(next_env, root=new, windows=True) == destination
    assert (destination / "library.json").read_text() == "custom library"


def test_explicit_data_directory_is_never_replaced(tmp_path):
    custom = tmp_path / "explicit"
    environ = {"LOCALAPPDATA": str(tmp_path / "App Data"), "PROTOKOL_DATA_DIR": str(custom)}
    assert launch.configure_data_directory(environ, windows=True) == custom
    assert not custom.exists()
    assert not (tmp_path / "App Data").exists()


def test_windows_data_migration_keeps_sqlite_counter(tmp_path):
    import sqlite3
    old = tmp_path / "old"
    (old / "data").mkdir(parents=True)
    with sqlite3.connect(old / "data/numbering.sqlite3") as connection:
        connection.execute("CREATE TABLE test_counter (value INTEGER)")
        connection.execute("INSERT INTO test_counter VALUES (1400)")
    destination = launch.configure_data_directory({"LOCALAPPDATA": str(tmp_path / "App Data")}, root=old, windows=True)
    with sqlite3.connect(destination / "numbering.sqlite3") as connection:
        assert connection.execute("SELECT value FROM test_counter").fetchone()[0] == 1400


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



def test_fresh_install_uses_bundled_models_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_ocr.urllib.request, "urlopen", lambda *a, **k: pytest.fail("Network used"))
    prepare_ocr.prepare_models(tmp_path)
    for language, checksum in prepare_ocr.CHECKSUMS.items():
        assert sha256((tmp_path / f"{language}.traineddata").read_bytes()).hexdigest() == checksum
    prepare_ocr.prepare_models(tmp_path, download=False)
