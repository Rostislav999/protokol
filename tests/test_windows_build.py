"""Verify installer inputs without pretending Linux can produce a Windows EXE."""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path

import pytest

from scripts import build_windows as build


def test_runtime_download_verifies_checksum_before_replacing_file(tmp_path, monkeypatch):
    path = tmp_path / "runtime.exe"
    path.write_bytes(b"existing runtime")
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **k: BytesIO(b"wrong download"))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        build.download_verified("https://example.invalid/runtime", path, sha256(b"verified").hexdigest())
    assert path.read_bytes() == b"existing runtime"
    assert not list(tmp_path.glob("*.tmp"))


def test_verified_download_is_reused_without_network(tmp_path, monkeypatch):
    path = tmp_path / "runtime.exe"
    data = b"verified runtime"
    path.write_bytes(data)
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **k: pytest.fail("Network used"))
    build.download_verified("https://example.invalid/runtime", path, sha256(data).hexdigest())


def test_runtime_download_is_installed_only_after_verification(tmp_path, monkeypatch):
    path = tmp_path / "downloads/runtime.exe"
    data = b"verified runtime"
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **k: BytesIO(data))
    build.download_verified("https://example.invalid/runtime", path, sha256(data).hexdigest())
    assert path.read_bytes() == data
    assert not list(path.parent.glob("*.tmp"))


def test_excessive_runtime_download_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "MAX_DOWNLOAD", 5)
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **k: BytesIO(b"too much data"))
    with pytest.raises(RuntimeError, match="size limit"):
        build.download_verified("https://example.invalid/runtime", tmp_path / "runtime.exe", "unused")
    assert not list(tmp_path.iterdir())


def test_packaged_validation_removes_system_dependency_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    monkeypatch.setenv("PATH", "system-python-and-tesseract")
    monkeypatch.setenv("PYTHONPATH", "global-site-packages")
    monkeypatch.setenv("PYTHONHOME", "system-python")
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)

    monkeypatch.setattr(build.subprocess, "run", run)
    executable = tmp_path / "Протокол/Protokol.exe"
    build.check_bundle(executable, tmp_path / "check", window=True)
    assert seen["command"] == [str(executable), "--smoke-test"]
    assert seen["env"]["PATH"] == os.pathsep.join([str(tmp_path / "Windows/System32"), str(tmp_path / "Windows")])
    assert "PYTHONPATH" not in seen["env"]
    assert "PYTHONHOME" not in seen["env"]
    assert seen["env"]["PROTOKOL_DATA_DIR"] == str(tmp_path / "check/data")
    assert seen["check"] is True


@pytest.mark.skipif(os.name == "nt", reason="This assertion concerns unsupported cross-compilation")
def test_windows_build_cannot_report_success_on_linux():
    with pytest.raises(SystemExit) as error:
        build.main([])
    assert error.value.code == 2
