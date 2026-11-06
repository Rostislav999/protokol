"""Printing validates generated archives and invokes a fixed Word worker only on request."""
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from app import printing, word_tools, print_worker
import fitz


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


def pdf_bytes():
    with fitz.open() as document:
        for number in (1, 2):
            page = document.new_page(width=595.276, height=841.89)
            page.insert_text((72, 72), f'Assignment page {number}')
        return document.tobytes()


@pytest.fixture
def available(monkeypatch):
    monkeypatch.setattr(printing, "printing_status", lambda: {
        "available": True, "platform": "windows", "printer": "HP LaserJet", "copies": 2, "reason": "",
        "printers": [{"name": "HP LaserJet", "selectable": True}, {"name": "Canon Київ", "selectable": True}]})
    def converted(documents, **options):
        assert options == {'pdf': True}
        return [dict(pdf=pdf_bytes(), error='') for _ in documents]
    monkeypatch.setattr(word_tools, 'process_documents', converted)


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
        settings = json.loads(Path(command[-2]).read_text(encoding='utf-8'))
        calls.append(settings['printer'])
        result = Path(command[-1])
        result.write_text(json.dumps(dict(ok=True, documents=1, queued_copies=2, printer=settings['printer'], duplex=settings['duplex'])), encoding='utf-8')
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
        assert command[:4] == [sys.executable, '-m', 'app.print_worker', command[-2]]
        settings_path = Path(command[-2])
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert settings["copies"] == 2
        assert settings['printer'] == 'HP LaserJet'
        paths = [Path(path) for path in settings["documents"]]
        assert [path.name for path in paths] == ["assignment-001.pdf", "assignment-002.pdf", "assignment-003.pdf"]
        for path in paths:
            assert path.parent == settings_path.parent
            with fitz.open(path) as document:
                assert len(document) == 2
        seen_paths.extend([*paths, settings_path])
        assert options["encoding"] == "utf-8"
        assert options["errors"] == "replace"
        assert options["timeout"] == 150
        result = Path(command[-1])
        result.write_text(json.dumps({"ok": True, "documents": 3, "copies": 2, "queued_copies": 6, "printer": "HP LaserJet", "duplex": "simplex"}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(printing.subprocess, "run", run)
    response = printing.print_assignments(archive("../../outside.docx", "Дані;$(not-a-command).docx", "nested/third.docx"), printer='HP LaserJet')
    assert response == {"documents": 3, "copies": 2, "total_copies": 6, "printer": "HP LaserJet", "duplex": "simplex"}
    assert seen_paths and all(not path.exists() for path in seen_paths)


def test_partial_worker_failure_reports_already_queued_documents_and_never_retries(available, monkeypatch):
    calls = []

    def run(command, **options):
        calls.append(command)
        path = Path(command[-1])
        path.write_text(json.dumps({"ok": False, "documents": 0, "queued_copies": 1, "active_copy": "copy 2", "error": "Printer is offline"}), encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(printing.subprocess, "run", run)
    with pytest.raises(printing.PrintError, match="До збою в чергу передано 1") as raised:
        printing.print_assignments(archive("one.docx", "two.docx"), printer='HP LaserJet')
    assert "Printer is offline" in str(raised.value)
    assert len(calls) == 1


def test_word_timeout_preserves_partial_progress_and_cleans_up(available, monkeypatch):
    paths = []

    def run(command, **options):
        path = Path(command[-1])
        paths.append(path)
        path.write_text(json.dumps({"ok": False, "documents": 0, "queued_copies": 1, "active_copy": "copy 2"}), encoding="utf-8")
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(printing.subprocess, "run", run)
    with pytest.raises(printing.PrintError, match="До збою в чергу передано 1"):
        printing.print_assignments(archive("one.docx", "two.docx"), printer='HP LaserJet')
    assert not paths[0].exists()


def test_file_output_printer_is_actionable_instead_of_hanging_on_hidden_save_dialog(available, monkeypatch):
    def run(command, **options):
        path = Path(command[-1])
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


@pytest.mark.parametrize('duplex', print_worker.DUPLEX_MODES)
def test_two_complete_copies_are_separate_jobs_even_for_two_page_duplex_documents(tmp_path, duplex):
    paths = []
    for index in range(2):
        path = tmp_path / f'assignment-{index}.pdf'
        path.write_bytes(pdf_bytes())
        paths.append(str(path))
    calls = []
    class FakePrinter:
        def __init__(self, printer, mode):
            assert (printer, mode) == ('HP LaserJet', duplex)
        def print_copy(self, path, label):
            with fitz.open(path) as document:
                assert len(document) == 2
            calls.append((path, label))
    result = print_worker.queue(dict(documents=paths, copies=2, printer='HP LaserJet', duplex=duplex),
                                tmp_path / 'result.json', backend_factory=FakePrinter)
    assert result['ok'] and result['queued_copies'] == 4 and result['documents'] == 2
    assert [call[0] for call in calls] == [paths[0], paths[0], paths[1], paths[1]]
    assert len({call[1] for call in calls}) == 4


def test_second_copy_failure_retains_exact_progress_and_does_not_retry(tmp_path):
    path = tmp_path / 'assignment.pdf'
    path.write_bytes(pdf_bytes())
    calls = []
    class FakePrinter:
        def __init__(self, *args):
            pass
        def print_copy(self, path, label):
            calls.append(label)
            if len(calls) == 2:
                raise ValueError('Printer is offline')
    result_path = tmp_path / 'result.json'
    result = print_worker.queue(dict(documents=[str(path)], copies=2, printer='HP LaserJet', duplex='long_edge'),
                                result_path, backend_factory=FakePrinter)
    assert not result['ok'] and result['queued_copies'] == 1 and result['documents'] == 0
    assert result['active_copy'] and len(calls) == 2
    message = printing._failure(json.loads(result_path.read_text(encoding='utf-8')), 'Не вдалося надрукувати.')
    assert '1 примірник' in message and 'стан невідомий' in message and 'Перевірте чергу' in message


def test_unsupported_duplex_and_invalid_later_pdf_fail_before_any_job(tmp_path):
    path = tmp_path / 'assignment.pdf'
    path.write_bytes(pdf_bytes())
    calls = []
    def unsupported(*args):
        calls.append(args)
        raise ValueError('DUPLEX_UNSUPPORTED')
    settings = dict(documents=[str(path)], copies=2, printer='HP LaserJet', duplex='long_edge')
    result = print_worker.queue(settings, tmp_path / 'result.json', backend_factory=unsupported)
    assert result['error'] == 'DUPLEX_UNSUPPORTED' and result['queued_copies'] == 0 and not result['active_copy']
    assert 'односторонній' in printing._failure(result, '')
    calls.clear()
    bad = tmp_path / 'broken.pdf'
    bad.write_bytes(b'broken')
    result = print_worker.queue({**settings, 'documents': [str(path), str(bad)]}, tmp_path / 'result.json',
                                backend_factory=unsupported)
    assert not result['ok'] and not calls and result['queued_copies'] == 0


def test_pdf_export_failure_never_launches_print_worker(available, monkeypatch):
    monkeypatch.setattr(word_tools, 'process_documents', lambda *a, **k: [dict(error='Word failed')])
    monkeypatch.setattr(printing.subprocess, 'run', lambda *a, **k: pytest.fail('Unexpected print'))
    with pytest.raises(printing.PrintError, match='не передано до принтера'):
        printing.print_assignments(archive(), printer='HP LaserJet')


@pytest.mark.parametrize('duplex', print_worker.DUPLEX_MODES)
def test_selected_sides_reach_isolated_worker(available, monkeypatch, duplex):
    def run(command, **options):
        settings = json.loads(Path(command[-2]).read_text(encoding='utf-8'))
        assert settings['duplex'] == duplex
        Path(command[-1]).write_text(json.dumps(dict(ok=True, documents=1, queued_copies=2,
                                                   printer=settings['printer'], duplex=duplex)), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(printing.subprocess, 'run', run)
    assert printing.print_assignments(archive(), printer='HP LaserJet', duplex=duplex)['duplex'] == duplex


def test_native_renderer_preserves_two_pages_and_physical_size_without_a_printer(tmp_path, monkeypatch):
    pytest.importorskip('PySide6.QtPrintSupport')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtPrintSupport import QPrinter
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    application = QApplication.instance() or QApplication([])
    source = tmp_path / 'two-pages.pdf'
    source.write_bytes(pdf_bytes())
    output = tmp_path / 'printed.pdf'
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(output))
    printer.setResolution(300)
    worker = object.__new__(print_worker.WindowsPrinter)
    monkeypatch.setattr(worker, '_printer', lambda: printer)
    worker.print_copy(str(source), 'Test — no physical printer')
    with fitz.open(output) as document:
        assert len(document) == 2
        for page in document:
            assert page.rect.width == pytest.approx(595.276, abs=1)
            assert page.rect.height == pytest.approx(841.89, abs=1)
            assert page.get_images()  # Raster pages were actually painted.
    assert application is not None
