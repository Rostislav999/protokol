"""Print a reviewed assignment archive through the user's local Microsoft Word."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
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


def _printer_names() -> list[str]:
    """Read installed local/connected queues from the Windows spooler each time."""
    import ctypes
    from ctypes import wintypes

    class PrinterInfo(ctypes.Structure):
        _fields_ = [('name', wintypes.LPWSTR), ('server', wintypes.LPWSTR), ('attributes', wintypes.DWORD)]

    enum = ctypes.WinDLL('winspool.drv', use_last_error=True).EnumPrintersW
    enum.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPVOID,
                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    enum.restype = wintypes.BOOL
    needed, count = wintypes.DWORD(), wintypes.DWORD()
    if not enum(6, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(count)):
        if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise PrintError('Не вдалося прочитати список принтерів Windows. Перевірте службу друку та оновіть список.')
    for _ in range(3):
        if not needed.value:
            return []
        if needed.value > 16 * 1024 * 1024:
            raise PrintError('Список принтерів завеликий. Перевірте налаштування друку Windows.')
        buffer = ctypes.create_string_buffer(needed.value)
        if enum(6, None, 4, buffer, len(buffer), ctypes.byref(needed), ctypes.byref(count)):
            entries = ctypes.cast(buffer, ctypes.POINTER(PrinterInfo))
            names = {entries[index].name for index in range(count.value) if entries[index].name}
            return sorted(names, key=str.casefold)
        if ctypes.get_last_error() != 122:
            break
    raise PrintError('Список принтерів змінився або недоступний. Натисніть «Оновити список».')


def _file_output_printer(name: str) -> bool:
    return bool(re.search(r'Microsoft Print to PDF|Microsoft XPS Document Writer', name, re.I))


def printing_status() -> dict:
    """Inspect local prerequisites without opening Word or sending any print job."""
    if not _is_windows():
        return {"available": False, "platform": "other", "printer": "", "printers": [], "copies": 2,
                "reason": "Прямий друк доступний у Windows з установленим Microsoft Word. "
                          "На цьому комп’ютері завантажте Word-файли та надрукуйте їх у редакторі."}
    if not _powershell():
        return {"available": False, "platform": "windows", "printer": "", "printers": [], "copies": 2,
                "reason": "Не знайдено Windows PowerShell, потрібний для друку через Microsoft Word."}
    if not _word_registered():
        return {"available": False, "platform": "windows", "printer": "", "printers": [], "copies": 2,
                "reason": "Для прямого друку встановіть настільний Microsoft Word. "
                          "Потім натисніть «Оновити список» у вікні друку."}
    default = _default_printer()
    try:
        names = _printer_names()
    except (PrintError, OSError) as exc:
        return {"available": False, "platform": "windows", "printer": default, "printers": [],
                "copies": 2, "reason": str(exc)}
    printers = [{"name": name, "is_default": name.casefold() == default.casefold(),
                 "selectable": not _file_output_printer(name),
                 "reason": "Для PDF/XPS збережіть документи у файли." if _file_output_printer(name) else ""}
                for name in names]
    available = any(printer['selectable'] for printer in printers)
    return {"available": available, "platform": "windows", "printer": default, "printers": printers, "copies": 2,
            "reason": "" if available else "Не знайдено принтера для друку на папері. Підключіть принтер у Windows і натисніть «Оновити список»."}


# This is fixed source code. Paths, copies and document names are data in a UTF-8
# JSON file, never interpolated into PowerShell commands. Keep the script ASCII
# so Windows PowerShell 5.1 does not mistake its source encoding for an ANSI codepage.
_PRINT_SCRIPT = r'''param([string]$SettingsPath, [string]$ResultPath)
function Invoke-WordPrint([object]$Document, [int]$Copies) {
    # Dispatch optional VARIANT arguments as boxed objects. Passing [ref]
    # Boolean variables through PowerShell's COM adapter can fail conversion.
    $missing = [System.Type]::Missing
    [object[]]$arguments = @($false, $false, 0, $missing, $missing, $missing,
        0, $Copies, $missing, 0, $false, $true,
        $missing, $missing, $missing, $missing, $missing, $missing)
    [void]$Document.GetType().InvokeMember('PrintOut',
        [System.Reflection.BindingFlags]::InvokeMethod, $null, $Document, $arguments)
}
function Set-WordPrinter([object]$Word, [string]$Printer, [string[]]$Installed) {
    $basic = $Word.WordBasic
    try {
        # Supply every optional VARIANT. The .NET Framework binder used by
        # Windows PowerShell 5.1 cannot safely reorder omitted ref arguments.
        # FilePrintSetup: Printer, Options, Network, DoNotSetAsSysDefault.
        $missing = [System.Type]::Missing
        [object[]]$arguments = @($Printer, $missing, $missing, 1)
        [void]$basic.GetType().InvokeMember('FilePrintSetup',
            [System.Reflection.BindingFlags]::InvokeMethod, $null, $basic, $arguments)
    } finally {
        if ($null -ne $basic -and [System.Runtime.InteropServices.Marshal]::IsComObject($basic)) {
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($basic)
        }
    }
    # Word appends a localized port suffix. Resolve the longest installed name
    # to distinguish e.g. "HP" from "HP Copy", and never fall back to a default.
    $active = [string]$Word.ActivePrinter
    $matched = @($Installed | Where-Object {
        $active.Equals($_, [System.StringComparison]::OrdinalIgnoreCase) -or
        $active.StartsWith($_ + ' ', [System.StringComparison]::OrdinalIgnoreCase)
    } | Sort-Object -Property @{Expression={$_.Length}; Descending=$true})
    if ($matched.Count -eq 0 -or $matched[0] -ne $Printer) { throw 'PRINTER_SELECTION_FAILED' }
}
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
$originalUpdateLinks = $null
$utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
$result = @{ok=$false; documents=0; copies=2; printer=''; error=''; stage='startup'}
try {
    $settings = [System.IO.File]::ReadAllText($SettingsPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $result.copies = [int]$settings.copies
    $result.printer = [string]$settings.printer
    Add-Type -AssemblyName System.Drawing
    [string[]]$installed = @([System.Drawing.Printing.PrinterSettings]::InstalledPrinters)
    if ([string]::IsNullOrWhiteSpace($result.printer) -or $installed -notcontains $result.printer) {
        throw 'PRINTER_NOT_FOUND'
    }
    if ($result.printer -match '(?i)Microsoft Print to PDF|Microsoft XPS Document Writer') {
        throw 'FILE_OUTPUT_PRINTER'
    }
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $originalUpdateLinks = $word.Options.UpdateLinksAtOpen
    $word.Options.UpdateLinksAtOpen = $false
    Set-WordPrinter $word $result.printer $installed
    $result.stage = 'printing'
    foreach ($path in $settings.documents) {
        $document = $word.Documents.Open([string]$path, $false, $true, $false)
        Invoke-WordPrint $document ([int]$settings.copies)
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
    if error == "PRINTER_NOT_FOUND":
        message = "Вибраний принтер більше не доступний. Оновіть список і виберіть принтер знову."
    elif error == "PRINTER_SELECTION_FAILED":
        message = "Microsoft Word не зміг вибрати потрібний принтер. Документи не передано до друку; оновіть список і повторіть вибір."
    elif error == "FILE_OUTPUT_PRINTER":
        message = "Для прямого друку виберіть принтер, який друкує на папері. " \
                  "Microsoft Print to PDF та XPS потребують окремого збереження файлів."
    else:
        message = fallback + (" Причина: " + error if error else "")
    sent = result.get("documents", 0)
    if type(sent) is int and sent > 0:
        message += f" До збою в чергу передано {sent} документ(ів), по 2 примірники. " \
                   "Перевірте чергу принтера перед повторним друком."
    return message


def print_assignments(archive: bytes, copies: int = 2, printer: str = '') -> dict:
    """Queue exactly two copies on the explicitly selected installed printer.

    The caller must require an explicit print request after document review.
    This function never generates files, assigns sample numbers or retries jobs.
    """
    if type(copies) is not int or copies != 2:
        raise PrintError("Для кожного завдання друкуються рівно 2 примірники.")
    documents = _documents(archive)
    status = printing_status()
    if not status["available"]:
        raise PrintError(status["reason"])
    selected = next((entry for entry in status.get('printers', []) if entry['name'] == printer), None)
    if selected is None:
        raise PrintError('Виберіть принтер зі списку. Якщо його немає, натисніть «Оновити список».')
    if not selected['selectable']:
        raise PrintError('Для прямого друку виберіть принтер, який друкує на папері. Для PDF/XPS збережіть документи у файли.')
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
        settings.write_text(json.dumps({"documents": paths, "copies": copies, "printer": printer}, ensure_ascii=False), encoding="utf-8")
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
        if (process.returncode or result.get("ok") is not True or result.get("documents") != len(documents)
                or result.get('printer') != printer):
            if not result.get("error"):
                result["error"] = ((process.stderr or "") + "\n" + (process.stdout or "")).strip()[:1200]
            raise PrintError(_failure(result, "Microsoft Word не зміг надрукувати весь комплект."))
        return {"documents": len(documents), "copies": copies, "total_copies": len(documents) * copies,
                "printer": printer}
