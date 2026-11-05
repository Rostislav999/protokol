"""Printing validates generated archives and invokes a fixed Word worker only on request."""
import io
import json
from pathlib import Path
import subprocess
import shutil
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from app import printing


def archive(*names):
    document = io.BytesIO()
    with ZipFile(document, "w") as word:
        word.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        word.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    output = io.BytesIO()
    with ZipFile(output, "w") as bundle:
        for name in names or ("1397 Ветсан Свинина.docx",):
            bundle.writestr(name, document.getvalue())
        bundle.writestr("ПЕРЕВІРИТИ.txt", "Завдання")
    return output.getvalue()


@pytest.fixture
def available(monkeypatch):
    monkeypatch.setattr(printing, "printing_status", lambda: {
        "available": True, "platform": "windows", "printer": "HP LaserJet", "copies": 2, "reason": "",
        "printers": [{"name": "HP LaserJet", "selectable": True}, {"name": "Canon Київ", "selectable": True}]})
    monkeypatch.setattr(printing, "_powershell", lambda: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")


def test_unsupported_platform_status_never_opens_word_or_sends_a_job(monkeypatch):
    monkeypatch.setattr(printing, "_is_windows", lambda: False)
    monkeypatch.setattr(printing.subprocess, "run", lambda *a, **k: pytest.fail("Unexpected process"))
    status = printing.printing_status()
    assert not status["available"]
    assert "Windows" in status["reason"]
    with pytest.raises(printing.PrintError, match="Windows"):
        printing.print_assignments(archive())


def test_windows_status_detects_missing_word_without_instantiating_it(monkeypatch):
    monkeypatch.setattr(printing, "_is_windows", lambda: True)
    monkeypatch.setattr(printing, "_powershell", lambda: "powershell.exe")
    monkeypatch.setattr(printing, "_word_registered", lambda: False)
    monkeypatch.setattr(printing.subprocess, "run", lambda *a, **k: pytest.fail("Word started for capability check"))
    assert "Microsoft Word" in printing.printing_status()["reason"]


def test_printer_list_refreshes_and_does_not_require_a_windows_default(monkeypatch):
    monkeypatch.setattr(printing, '_is_windows', lambda: True)
    monkeypatch.setattr(printing, '_powershell', lambda: 'powershell.exe')
    monkeypatch.setattr(printing, '_word_registered', lambda: True)
    monkeypatch.setattr(printing, '_default_printer', lambda: '')
    queues = ['HP LaserJet', 'Microsoft Print to PDF']
    monkeypatch.setattr(printing, '_printer_names', lambda: queues[:])
    first = printing.printing_status()
    assert first['available']
    assert not any(value['is_default'] for value in first['printers'])
    assert not first['printers'][1]['selectable']
    queues[:] = ['Canon Київ']
    assert [value['name'] for value in printing.printing_status()['printers']] == ['Canon Київ']
    queues.clear()
    assert not printing.printing_status()['available']


@pytest.mark.parametrize('printer', ['', 'Unavailable printer'])
def test_missing_printer_never_falls_back_to_default(available, monkeypatch, printer):
    monkeypatch.setattr(printing.subprocess, 'run', lambda *a, **k: pytest.fail('Unexpected print'))
    with pytest.raises(printing.PrintError, match='Виберіть принтер'):
        printing.print_assignments(archive(), printer=printer)


def test_non_default_unicode_printer_is_data_and_rechecked_for_every_request(available, monkeypatch):
    calls = []
    def run(command, **options):
        settings = json.loads(Path(command[command.index('-SettingsPath') + 1]).read_text(encoding='utf-8'))
        calls.append(settings['printer'])
        result = Path(command[command.index('-ResultPath') + 1])
        result.write_text(json.dumps(dict(ok=True, documents=1, printer=settings['printer'])), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(printing.subprocess, 'run', run)
    assert printing.print_assignments(archive(), printer='Canon Київ')['printer'] == 'Canon Київ'
    monkeypatch.setattr(printing, 'printing_status', lambda: dict(available=True, printer='HP LaserJet',
                                                               printers=[dict(name='HP LaserJet', selectable=True)]))
    with pytest.raises(printing.PrintError, match='Виберіть принтер'):
        printing.print_assignments(archive(), printer='Canon Київ')
    assert calls == ['Canon Київ']


@pytest.mark.parametrize("copies", [0, 1, 3, "2", True])
def test_printing_requires_exactly_two_copies(available, monkeypatch, copies):
    monkeypatch.setattr(printing.subprocess, "run", lambda *a, **k: pytest.fail("Unexpected physical print"))
    with pytest.raises(printing.PrintError, match="2 примірники"):
        printing.print_assignments(archive(), copies=copies)


@pytest.mark.parametrize("data", [b"not a ZIP", b"", None])
def test_invalid_archive_is_rejected_before_worker(available, monkeypatch, data):
    monkeypatch.setattr(printing.subprocess, "run", lambda *a, **k: pytest.fail("Unexpected physical print"))
    with pytest.raises(printing.PrintError):
        printing.print_assignments(data)


def test_worker_receives_safe_generated_paths_and_two_copies_and_cleans_up(available, monkeypatch):
    seen_paths = []

    def run(command, **options):
        assert command[0].endswith("powershell.exe")
        assert command[1:6] == ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
        script = Path(command[command.index("-File") + 1])
        assert script.read_text(encoding="ascii") == printing._PRINT_SCRIPT
        assert "$word.AutomationSecurity = 3" in script.read_text(encoding="ascii")
        settings_path = Path(command[command.index("-SettingsPath") + 1])
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert settings["copies"] == 2
        assert settings['printer'] == 'HP LaserJet'
        paths = [Path(path) for path in settings["documents"]]
        assert [path.name for path in paths] == ["assignment-001.docx", "assignment-002.docx", "assignment-003.docx"]
        for path in paths:
            assert path.parent == script.parent
            with ZipFile(path) as word:
                assert "word/document.xml" in word.namelist()
        seen_paths.extend([*paths, script, settings_path])
        assert options["encoding"] == "utf-8"
        assert options["errors"] == "replace"
        assert options["timeout"] == 150
        result = Path(command[command.index("-ResultPath") + 1])
        result.write_text(json.dumps({"ok": True, "documents": 3, "copies": 2, "printer": "HP LaserJet"}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(printing.subprocess, "run", run)
    response = printing.print_assignments(archive("../../outside.docx", "Дані;$(not-a-command).docx", "nested/third.docx"), printer='HP LaserJet')
    assert response == {"documents": 3, "copies": 2, "total_copies": 6, "printer": "HP LaserJet"}
    assert seen_paths and all(not path.exists() for path in seen_paths)


def test_partial_worker_failure_reports_already_queued_documents_and_never_retries(available, monkeypatch):
    calls = []

    def run(command, **options):
        calls.append(command)
        path = Path(command[command.index("-ResultPath") + 1])
        path.write_text(json.dumps({"ok": False, "documents": 1, "error": "Printer is offline"}), encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(printing.subprocess, "run", run)
    with pytest.raises(printing.PrintError, match="До збою в чергу передано 1") as raised:
        printing.print_assignments(archive("one.docx", "two.docx"), printer='HP LaserJet')
    assert "Printer is offline" in str(raised.value)
    assert len(calls) == 1


def test_word_timeout_preserves_partial_progress_and_cleans_up(available, monkeypatch):
    paths = []

    def run(command, **options):
        path = Path(command[command.index("-ResultPath") + 1])
        paths.append(path)
        path.write_text(json.dumps({"ok": False, "documents": 1}), encoding="utf-8")
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(printing.subprocess, "run", run)
    with pytest.raises(printing.PrintError, match="До збою в чергу передано 1"):
        printing.print_assignments(archive("one.docx", "two.docx"), printer='HP LaserJet')
    assert not paths[0].exists()


def test_file_output_printer_is_actionable_instead_of_hanging_on_hidden_save_dialog(available, monkeypatch):
    def run(command, **options):
        path = Path(command[command.index("-ResultPath") + 1])
        path.write_text(json.dumps({"ok": False, "documents": 0, "error": "FILE_OUTPUT_PRINTER"}), encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(printing.subprocess, "run", run)
    with pytest.raises(printing.PrintError, match="принтер, який друкує на папері"):
        printing.print_assignments(archive(), printer='HP LaserJet')


def test_document_limit_is_enforced_before_worker(available, monkeypatch):
    monkeypatch.setattr(printing, "MAX_DOCUMENTS", 1)
    monkeypatch.setattr(printing.subprocess, "run", lambda *a, **k: pytest.fail("Unexpected print"))
    with pytest.raises(printing.PrintError, match="від 1 до 300"):
        printing.print_assignments(archive("one.docx", "two.docx"))


@pytest.mark.skipif(not (shutil.which('powershell.exe') or shutil.which('pwsh')), reason='Requires PowerShell, not Word or a printer')
def test_word_variant_arguments_do_not_use_boolean_ref_conversion(tmp_path):
    # Exercise the worker helper against Word's ref-object method signature.
    # The fake object cannot create any physical print job.
    helper = printing._PRINT_SCRIPT.split('$ErrorActionPreference', 1)[0].split('\n', 1)[1]
    script = r'''$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public class WordPrintSignature {
    public int Calls = 0;
    public void PrintOut([Optional] ref object background, [Optional] ref object append, [Optional] ref object range,
        [Optional] ref object output, [Optional] ref object from, [Optional] ref object to, [Optional] ref object item,
        [Optional] ref object copies, [Optional] ref object pages, [Optional] ref object pageType, [Optional] ref object printToFile,
        [Optional] ref object collate, [Optional] ref object printer, [Optional] ref object duplex, [Optional] ref object columns,
        [Optional] ref object rows, [Optional] ref object width, [Optional] ref object height) {
        if (!(background is bool) || (bool)background || !(collate is bool) || !(bool)collate)
            throw new System.Exception("Incorrect Boolean variants");
        if (!(copies is int) || (int)copies != 2 || (bool)printToFile || (int)range != 0)
            throw new System.Exception("Incorrect print settings");
        if (!object.ReferenceEquals(output, System.Type.Missing) ||
            !object.ReferenceEquals(height, System.Type.Missing))
            throw new System.Exception("Missing optional variants were changed");
        Calls++;
    }
}
'@
''' + helper + r'''
$document = New-Object WordPrintSignature
Invoke-WordPrint $document 2
if ($document.Calls -ne 1) { throw 'Print method not invoked exactly once' }
'''
    path = tmp_path / 'variant-check.ps1'
    path.write_text(script, encoding='ascii')
    result = subprocess.run([shutil.which('powershell.exe') or shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-File', str(path)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not (shutil.which('powershell.exe') or shutil.which('pwsh')), reason='Requires PowerShell only')
def test_word_printer_setup_uses_complete_variants_without_changing_system_default(tmp_path):
    helper = printing._PRINT_SCRIPT.split('$ErrorActionPreference', 1)[0].split('\n', 1)[1]
    script = r'''$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class PrinterWord {
    public string ActivePrinter;
    public bool IgnoreSelection;
    public PrinterSetup WordBasic;
    public PrinterWord() { WordBasic = new PrinterSetup(this); }
}
public class PrinterSetup {
    private PrinterWord Word;
    public int Calls;
    public PrinterSetup(PrinterWord word) { Word = word; }
    public void FilePrintSetup([Optional] ref object Printer, [Optional] ref object Options,
        [Optional] ref object Network, [Optional] ref object DoNotSetAsSysDefault) {
        if (!(Printer is string) || !(DoNotSetAsSysDefault is int) || (int)DoNotSetAsSysDefault != 1)
            throw new Exception("Default printer must remain unchanged");
        if (!object.ReferenceEquals(Options, Type.Missing) || !object.ReferenceEquals(Network, Type.Missing))
            throw new Exception("Optional setup arguments changed");
        Calls++;
        if (!Word.IgnoreSelection) Word.ActivePrinter = (string)Printer + " on Ne01:";
    }
}
'@
''' + helper + r'''
$word = New-Object PrinterWord
$word.ActivePrinter = 'HP Copy on Ne02:'
Set-WordPrinter $word 'HP' @('HP', 'HP Copy')
if ($word.WordBasic.Calls -ne 1 -or $word.ActivePrinter -ne 'HP on Ne01:') { throw 'Selection failed' }
$word.IgnoreSelection = $true
$word.ActivePrinter = 'HP Copy on Ne02:'
$rejected = $false
try { Set-WordPrinter $word 'HP' @('HP', 'HP Copy') } catch {
    if ($_.Exception.Message -match 'PRINTER_SELECTION_FAILED') { $rejected = $true } else { throw }
}
if (-not $rejected) { throw 'Worker accepted a different printer with a shared name prefix' }
'''
    path = tmp_path / 'printer-setup-check.ps1'
    path.write_text(script, encoding='ascii')
    result = subprocess.run([shutil.which('powershell.exe') or shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-File', str(path)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
