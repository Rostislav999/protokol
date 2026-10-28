"""Exercise the real launcher from Python without application dependencies."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import venv

import pytest

import launch
from scripts import prepare_ocr


def test_bare_python_checks_ocr_using_prepared_application_environment(tmp_path):
    runtime = launch.venv_python()
    if not launch.installed_dependencies(runtime):
        pytest.skip("Requires the prepared application .venv with native dependencies")
    try:
        executable = launch.find_tesseract()
        launch.verify_tesseract(executable, dict(os.environ))
        prepare_ocr.prepare_models(download=False)
    except (launch.LaunchError, OSError, RuntimeError) as exc:
        pytest.skip(f"Requires local Tesseract 5 and verified OCR models: {exc}")

    # A fresh Windows installation starts with a standard-library-only Python.
    # Keep both spaces and Cyrillic characters in this interpreter's path.
    bare = tmp_path / "Чистий Python без бібліотек"
    venv.EnvBuilder(with_pip=False).create(bare)
    bootstrap = bare / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environ = dict(os.environ)
    for variable in ("PYTHONHOME", "PYTHONPATH"):
        environ.pop(variable, None)
    environ["PYTHONNOUSERSITE"] = "1"
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    environ["PYTHONIOENCODING"] = "utf-8"
    probe = subprocess.run(
        [str(bootstrap), "-c", "import importlib.util; assert importlib.util.find_spec('PIL') is None"],
        cwd=tmp_path, env=environ, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr

    # Block network access in the bootstrap process and its Python children.
    # --check must reuse prepared dependencies/models and never start a server.
    guard = tmp_path / "network guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import socket, urllib.request\n"
        "def blocked(*args, **kwargs):\n"
        "    raise AssertionError('Network access during offline launch check')\n"
        "urllib.request.urlopen = blocked\n"
        "socket.socket.connect = blocked\n"
        "socket.socket.connect_ex = blocked\n"
        "socket.create_connection = blocked\n",
        encoding="utf-8",
    )
    environ["PYTHONPATH"] = str(guard)
    data = tmp_path / "must not create application data"
    environ["PROTOKOL_DATA_DIR"] = str(data)
    models = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in prepare_ocr.DEFAULT_DIRECTORY.glob("*.traineddata")
    }
    stamp = launch.ROOT / ".venv/protokol-lock.sha256"
    stamp_before = (stamp.read_bytes(), stamp.stat().st_mtime_ns) if stamp.exists() else None

    result = subprocess.run(
        [str(bootstrap), str(launch.ROOT / "launch.py"), "--check"],
        cwd=tmp_path, env=environ, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Проверка завершена" in output
    assert "Traceback" not in output
    assert not data.exists()
    assert models == {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in prepare_ocr.DEFAULT_DIRECTORY.glob("*.traineddata")
    }
    stamp_after = (stamp.read_bytes(), stamp.stat().st_mtime_ns) if stamp.exists() else None
    assert stamp_after == stamp_before
    assert not list(prepare_ocr.DEFAULT_DIRECTORY.glob("protokol-ocr-*"))
