"""Regression checks for the Windows OCR startup and Unicode paths."""
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from scripts import tesseract_runtime as runtime


@pytest.fixture
def models(tmp_path):
    directory = tmp_path / "Користувач з пробілами" / "Моделі OCR"
    directory.mkdir(parents=True)
    for language in ("ukr", "rus", "eng"):
        (directory / f"{language}.traineddata").write_bytes(b"model fixture")
    return directory


def result(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["tesseract"], code, stdout=stdout, stderr=stderr)


def test_data_directory_honors_configured_model_directory(models):
    assert runtime.data_directory({"TESSDATA_PREFIX": str(models)}) == models


def test_runtime_uses_relative_ascii_model_path_and_unicode_cwd(models, monkeypatch):
    inherited = {"TESSDATA_PREFIX": str(models), "HTTP_PROXY": "http://proxy:8080"}
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return result("available languages\nukr\nrus\neng\n")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    runtime.run_tesseract(["--list-langs"], executable="tesseract", models_directory=models,
                          environ=inherited, timeout=12)
    command = captured["command"]
    assert command[command.index("--tessdata-dir") + 1] == "."
    assert str(captured["cwd"]) == str(models)
    assert captured["env"]["TESSDATA_PREFIX"] == "."
    assert captured["env"]["HTTP_PROXY"] == inherited["HTTP_PROXY"]
    assert inherited["TESSDATA_PREFIX"] == str(models)
    assert captured["timeout"] == 12
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert all(str(argument).isascii() for argument in command)


def test_ocr_image_uses_relative_input_and_cleans_up(models, monkeypatch):
    before = set(models.rglob("*"))
    seen = []

    def run(args, **kwargs):
        relative = Path(args[0])
        assert not relative.is_absolute()
        assert str(relative).isascii()
        image_path = models / relative
        assert image_path.is_file()
        with Image.open(image_path) as image:
            assert image.size == (400, 100)
        assert kwargs["models_directory"] == models
        assert args[args.index("-l") + 1] == "ukr+rus+eng"
        seen.append(image_path)
        return result("PROTOKOL 123\n")

    monkeypatch.setattr(runtime, "run_tesseract", run)
    text = runtime.ocr_image(Image.new("RGB", (400, 100), "white"), "ukr+rus+eng",
                             models_directory=models)
    assert text == "PROTOKOL 123"
    assert len(seen) == 1
    assert not seen[0].exists()
    assert set(models.rglob("*")) == before


def test_ocr_image_failure_exposes_actual_diagnostic_and_cleans_up(models, monkeypatch):
    before = set(models.rglob("*"))
    monkeypatch.setattr(runtime, "run_tesseract", lambda *a, **k:
                        result(stderr="Error opening data file ./ukr.traineddata", code=1))
    with pytest.raises(ValueError, match="Error opening data file"):
        runtime.ocr_image(Image.new("RGB", (400, 100), "white"), "ukr", models_directory=models)
    assert set(models.rglob("*")) == before


def test_available_languages_parses_language_names_without_guessing(models, monkeypatch):
    monkeypatch.setattr(runtime, "run_tesseract", lambda *a, **k:
                        result('List of available languages in "." (3):\neng\nrus\nukr\n'))
    languages, warnings = runtime.available_languages(models_directory=models)
    assert languages == "ukr+rus+eng"
    assert warnings == []


def test_failed_language_listing_requires_successful_actual_ocr(models, monkeypatch):
    monkeypatch.setattr(runtime, "run_tesseract", lambda *a, **k:
                        result(stderr="cannot enumerate Unicode directory", code=1))
    calls = []

    def smoke(image, language, psm=3, **kwargs):
        assert language == "ukr+rus+eng"
        assert psm == 7
        calls.append(language)
        return "PROTOKOL 123"

    monkeypatch.setattr(runtime, "ocr_image", smoke)
    languages, warnings = runtime.available_languages(models_directory=models)
    assert languages == "ukr+rus+eng"
    assert calls == ["ukr+rus+eng"]
    assert warnings


def test_failed_listing_and_failed_smoke_preserve_engine_diagnostic(models, monkeypatch):
    monkeypatch.setattr(runtime, "run_tesseract", lambda *a, **k:
                        result(stderr="cannot enumerate directory", code=1))

    def fail(*args, **kwargs):
        raise ValueError("Failed loading language 'ukr'")

    monkeypatch.setattr(runtime, "ocr_image", fail)
    with pytest.raises(ValueError, match="Failed loading language"):
        runtime.available_languages(models_directory=models)


def test_success_exit_without_recognized_digits_does_not_establish_readiness(models, monkeypatch):
    monkeypatch.setattr(runtime, "run_tesseract", lambda *a, **k:
                        result(stderr="cannot enumerate directory", code=1))
    monkeypatch.setattr(runtime, "ocr_image", lambda *a, **k: "")
    with pytest.raises(ValueError):
        runtime.available_languages(models_directory=models)


def test_verify_runtime_checks_actual_ocr_even_when_language_listing_succeeds(models, monkeypatch):
    monkeypatch.setattr(runtime, "available_languages", lambda **kwargs: ("ukr+rus+eng", []))
    calls = []

    def smoke(image, language, psm=3, **kwargs):
        calls.append(language)
        return "PROTOKOL 123"

    monkeypatch.setattr(runtime, "ocr_image", smoke)
    assert runtime.verify_runtime(models_directory=models) == ("ukr+rus+eng", [])
    assert calls == ["ukr+rus+eng"]


def test_timeout_reports_failure_instead_of_claiming_readiness(models, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(runtime.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="15 секунд"):
        runtime.run_tesseract(["--list-langs"], models_directory=models)


def test_missing_executable_reports_model_directory_and_actual_error(models, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("test executable is missing")

    monkeypatch.setattr(runtime.subprocess, "run", missing)
    with pytest.raises(ValueError, match="test executable is missing") as error:
        runtime.run_tesseract(["--list-langs"], models_directory=models)
    assert str(models) in str(error.value)


def test_large_failure_output_is_bounded(models, monkeypatch):
    monkeypatch.setattr(runtime, "run_tesseract", lambda *a, **k:
                        result(stderr="real failure " + "x" * 10000, code=1))
    with pytest.raises(ValueError) as error:
        runtime.ocr_image(Image.new("RGB", (400, 100), "white"), "ukr", models_directory=models)
    assert "real failure" in str(error.value)
    assert len(str(error.value)) < 2200


def test_missing_model_files_cannot_be_hidden_by_successful_smoke(models, monkeypatch):
    (models / "rus.traineddata").unlink()
    monkeypatch.setattr(runtime, "run_tesseract", lambda *a, **k:
                        result(stderr="cannot enumerate directory", code=1))
    monkeypatch.setattr(runtime, "ocr_image", lambda *a, **k: pytest.fail("Incomplete model set accepted"))
    with pytest.raises(ValueError, match="cannot enumerate directory"):
        runtime.available_languages(models_directory=models)


def test_failed_language_load_with_zero_exit_status_still_fails(models, monkeypatch):
    monkeypatch.setattr(runtime, "run_tesseract", lambda *a, **k:
                        result(stdout="PROTOKOL 123", stderr="Failed loading language 'ukr'", code=0))
    with pytest.raises(ValueError, match="Failed loading language 'ukr'"):
        runtime.ocr_image(Image.new("RGB", (400, 100), "white"), "ukr+rus+eng", models_directory=models)


def test_cli_checks_supplied_executable_and_models_and_prints_warnings(models, monkeypatch, capsys):
    calls = []

    def verify(**kwargs):
        calls.append(kwargs)
        return "ukr+rus+eng", ["Test OCR warning"]

    monkeypatch.setattr(runtime, "verify_runtime", verify)
    assert runtime.main(["--executable", "C:/Program Files/Tesseract-OCR/tesseract.exe",
                         "--directory", str(models)]) == 0
    assert calls == [{"executable": "C:/Program Files/Tesseract-OCR/tesseract.exe", "models_directory": models}]
    assert "Test OCR warning" in capsys.readouterr().out


@pytest.mark.parametrize("error", [ValueError("Failed loading language 'ukr'"), ModuleNotFoundError("No module named 'PIL'")])
def test_cli_failure_reports_diagnostic_without_traceback(monkeypatch, capsys, error):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(runtime, "verify_runtime", fail)
    assert runtime.main([]) == 1
    message = capsys.readouterr().err
    assert str(error) in message
    assert "Traceback" not in message
