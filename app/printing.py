"""Print a reviewed assignment archive through the user's local Microsoft Word."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import BadZipFile, ZipFile

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_DOCUMENTS = 300
MAX_EXPANDED_BYTES = 150 * 1024 * 1024


class PrintError(ValueError):
    """An actionable print failure; retrying may duplicate already queued pages."""


def _is_windows() -> bool:
    return os.name == "nt"


def _word_registered() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            return True
    except (ImportError, OSError):
        return False


def _default_printer() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows NT\CurrentVersion\Windows") as key:
            value, _ = winreg.QueryValueEx(key, "Device")
            return str(value).split(",", 1)[0].strip()
    except (ImportError, OSError):
        return ""


def _powershell() -> str | None:
    return shutil.which("powershell.exe")


def printing_status() -> dict:
    """Inspect local prerequisites without opening Word or sending any print job."""
    if not _is_windows():
        return {"available": False, "platform": "other", "printer": "", "copies": 2,
                "reason": "Прямий друк доступний у Windows з установленим Microsoft Word. "
                          "На цьому комп’ютері завантажте Word-файли та надрукуйте їх у редакторі."}
    if not _powershell():
        return {"available": False, "platform": "windows", "printer": "", "copies": 2,
                "reason": "Не знайдено Windows PowerShell, потрібний для друку через Microsoft Word."}
    if not _word_registered():
        return {"available": False, "platform": "windows", "printer": "", "copies": 2,
                "reason": "Для прямого друку встановіть настільний Microsoft Word. "
                          "Потім перезапустіть програму або перевірте доступність друку знову."}
    return {"available": True, "platform": "windows", "printer": _default_printer(), "copies": 2,
            "reason": ""}


# This is fixed source code. Paths, copies and document names are data in a UTF-8
# JSON file, never interpolated into PowerShell commands. Keep the script ASCII
# so Windows PowerShell 5.1 does not mistake its source encoding for an ANSI codepage.
_PRINT_SCRIPT = r'''param([string]$SettingsPath, [string]$ResultPath)
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
$originalUpdateLinks = $null
$utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
$result = @{ok=$false; documents=0; copies=2; printer=''; error=''; stage='startup'}
try {
    $settings = [System.IO.File]::ReadAllText($SettingsPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $result.copies = [int]$settings.copies
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $originalUpdateLinks = $word.Options.UpdateLinksAtOpen
    $word.Options.UpdateLinksAtOpen = $false
    $result.printer = [string]$word.ActivePrinter
    if ([string]::IsNullOrWhiteSpace($result.printer)) { throw 'NO_DEFAULT_PRINTER' }
    if ($result.printer -match '(?i)Microsoft Print to PDF|Microsoft XPS Document Writer') {
        throw 'FILE_OUTPUT_PRINTER'
    }
    $result.stage = 'printing'
    foreach ($path in $settings.documents) {
        $document = $word.Documents.Open([string]$path, $false, $true, $false)
        $background = $false
        $append = $false
        $range = 0
        $missing = [System.Type]::Missing
        $item = 0
        $copies = [int]$settings.copies
        $pageType = 0
        $printToFile = $false
        $collate = $true
        $document.PrintOut([ref]$background, [ref]$append, [ref]$range,
            [ref]$missing, [ref]$missing, [ref]$missing, [ref]$item,
            [ref]$copies, [ref]$missing, [ref]$pageType, [ref]$printToFile, [ref]$collate)
        $result.documents += 1
        [System.IO.File]::WriteAllText($ResultPath, ($result | ConvertTo-Json -Compress),
            $utf8)
        $document.Close(0)
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
        $document = $null
    }
    $result.ok = $true
} catch {
    $result.error = $_.Exception.Message
} finally {
    if ($null -ne $document) {
        try { $document.Close(0) } catch {}
        try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) } catch {}
    }
    if ($null -ne $word) {
        if ($null -ne $originalUpdateLinks) {
            try { $word.Options.UpdateLinksAtOpen = $originalUpdateLinks } catch {}
        }
        try { $word.Quit(0) } catch {}
        try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) } catch {}
    }
    [System.IO.File]::WriteAllText($ResultPath, ($result | ConvertTo-Json -Compress),
        $utf8)
}
if (-not $result.ok) { exit 1 }
'''


def _documents(archive: bytes) -> list[bytes]:
    if not isinstance(archive, bytes) or not archive or len(archive) > MAX_ARCHIVE_BYTES:
        raise PrintError("Архів завдань порожній або перевищує 25 МБ. Сформуйте комплект повторно.")
    try:
        with ZipFile(io.BytesIO(archive)) as bundle:
            members = [member for member in bundle.infolist() if not member.is_dir()
                       and member.filename.lower().endswith(".docx")]
            if not 1 <= len(members) <= MAX_DOCUMENTS:
                raise PrintError("У комплекті має бути від 1 до 300 Word-файлів завдань.")
            if any(member.file_size > MAX_DOCUMENT_BYTES for member in members) or sum(
                    member.file_size for member in members) > MAX_EXPANDED_BYTES:
                raise PrintError("Word-файли в комплекті завеликі для прямого друку.")
            documents = [bundle.read(member) for member in members]
        for data in documents:
            with ZipFile(io.BytesIO(data)) as document:
                if "word/document.xml" not in document.namelist() or "[Content_Types].xml" not in document.namelist():
                    raise PrintError("Один із файлів не є повним документом Word. Перевірте його у Microsoft Word.")
        return documents
    except (BadZipFile, RuntimeError, OSError, EOFError) as exc:
        raise PrintError("Не вдалося прочитати комплект Word-файлів для друку. Сформуйте його повторно.") from exc


def _read_result(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _failure(result: dict, fallback: str) -> str:
    error = str(result.get("error") or "")[:1200]
    if error == "NO_DEFAULT_PRINTER":
        message = "Не вибрано принтер. У налаштуваннях Windows установіть принтер за замовчуванням."
    elif error == "FILE_OUTPUT_PRINTER":
        message = "Для прямого друку виберіть у Windows принтер за замовчуванням, який друкує на папері. " \
                  "Microsoft Print to PDF та XPS потребують окремого збереження файлів."
    else:
        message = fallback + (" Причина: " + error if error else "")
    sent = result.get("documents", 0)
    if type(sent) is int and sent > 0:
        message += f" До збою в чергу передано {sent} документ(ів), по 2 примірники. " \
                   "Перевірте чергу принтера перед повторним друком."
    return message


def print_assignments(archive: bytes, copies: int = 2) -> dict:
    """Queue exactly two copies of each generated assignment on the default printer.

    The caller must require an explicit print request after document review.
    This function never generates files, assigns sample numbers or retries jobs.
    """
    if type(copies) is not int or copies != 2:
        raise PrintError("Для кожного завдання друкуються рівно 2 примірники.")
    documents = _documents(archive)
    status = printing_status()
    if not status["available"]:
        raise PrintError(status["reason"])
    executable = _powershell()
    if not executable:
        raise PrintError("Windows PowerShell більше не доступний. Перевірте запуск програми.")
    # Every output name is generated here; archive member names are never paths.
    with tempfile.TemporaryDirectory(prefix="protokol-print-", ignore_cleanup_errors=True) as temporary:
        directory = Path(temporary)
        paths = []
        for index, data in enumerate(documents, 1):
            path = directory / f"assignment-{index:03d}.docx"
            path.write_bytes(data)
            paths.append(str(path))
        script = directory / "print.ps1"
        script.write_text(_PRINT_SCRIPT, encoding="ascii")
        settings = directory / "settings.json"
        settings.write_text(json.dumps({"documents": paths, "copies": copies}, ensure_ascii=False), encoding="utf-8")
        result_path = directory / "result.json"
        try:
            process = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                 "-SettingsPath", str(settings), "-ResultPath", str(result_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=min(900, 60 + len(documents) * 30),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise PrintError(_failure(_read_result(result_path),
                                      "Microsoft Word не завершив передавання до принтера вчасно. "
                                      "Перевірте вікна Word та чергу принтера.")) from exc
        except OSError as exc:
            raise PrintError("Не вдалося запустити Microsoft Word для друку: " + str(exc)[:1200]) from exc
        result = _read_result(result_path)
        if process.returncode or result.get("ok") is not True or result.get("documents") != len(documents):
            if not result.get("error"):
                result["error"] = ((process.stderr or "") + "\n" + (process.stdout or "")).strip()[:1200]
            raise PrintError(_failure(result, "Microsoft Word не зміг надрукувати весь комплект."))
        return {"documents": len(documents), "copies": copies, "total_copies": len(documents) * copies,
                "printer": str(result.get("printer") or status.get("printer") or "Принтер за замовчуванням")}
