"""Read-only pagination and PDF export through a locally installed office editor."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .printing import _powershell, _word_registered

MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 150 * 1024 * 1024
MAX_DOCUMENTS = 300  # 100 assignment samples, each with three departments.


class OfficeError(ValueError):
    pass


def _libreoffice():
    executable = shutil.which('soffice') or shutil.which('soffice.exe')
    if executable:
        return executable
    for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)'):
        root = os.environ.get(name)
        if root:
            candidate = Path(root) / 'LibreOffice/program/soffice.exe'
            if candidate.is_file():
                return str(candidate)
    return None


def office_status() -> dict:
    if os.name == 'nt' and _powershell() and _word_registered():
        return {'available': True, 'engine': 'word', 'label': 'Microsoft Word', 'reason': ''}
    if _libreoffice():
        return {'available': True, 'engine': 'libreoffice', 'label': 'LibreOffice', 'reason': ''}
    return {'available': False, 'engine': '', 'label': '',
            'reason': 'Для PDF і точного підрахунку сторінок установіть настільний Microsoft Word '
                      'або LibreOffice. Word-файли можна створювати без них.'}


# Fixed ASCII source; all names/paths travel in JSON, never in PowerShell code.
_WORD_SCRIPT = r'''param([string]$SettingsPath, [string]$ResultPath)
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
$originalUpdateLinks = $null
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
$result = @{ok=$false; documents=@(); error=''}
try {
    $settings = [System.IO.File]::ReadAllText($SettingsPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $originalUpdateLinks = $word.Options.UpdateLinksAtOpen
    $word.Options.UpdateLinksAtOpen = $false
    foreach ($item in $settings.documents) {
        $entry = @{ok=$false; pages=0; error=''}
        try {
            $document = $word.Documents.Open([string]$item.input, $false, $true, $false)
            $document.Repaginate()
            foreach ($story in $document.StoryRanges) {
                $range = $story
                while ($null -ne $range) {
                    foreach ($field in $range.Fields) {
                        if ([int]$field.Type -in @(26,33)) { [void]$field.Update() }
                    }
                    $range = $range.NextStoryRange
                }
            }
            $document.Repaginate()
            $entry.pages = [int]$document.ComputeStatistics(2)
            if ($settings.pdf) {
                $document.ExportAsFixedFormat([string]$item.output, 17)
            }
            $entry.ok = $true
        } catch {
            $entry.error = $_.Exception.Message
        } finally {
            if ($null -ne $document) {
                try { $document.Close(0) } catch {}
                try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) } catch {}
                $document = $null
            }
        }
        $result.documents += $entry
        [System.IO.File]::WriteAllText($ResultPath, ($result | ConvertTo-Json -Depth 5 -Compress), $utf8)
    }
    $result.ok = $true
} catch {
    $result.error = $_.Exception.Message
} finally {
    if ($null -ne $word) {
        if ($null -ne $originalUpdateLinks) {
            try { $word.Options.UpdateLinksAtOpen = $originalUpdateLinks } catch {}
        }
        try { $word.Quit(0) } catch {}
        try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) } catch {}
    }
    [System.IO.File]::WriteAllText($ResultPath, ($result | ConvertTo-Json -Depth 5 -Compress), $utf8)
}
if (-not $result.ok) { exit 1 }
'''


def _validate_docx(data: bytes) -> None:
    if not isinstance(data, bytes) or not data or len(data) > MAX_FILE:
        raise OfficeError('Word-файл порожній або перевищує 25 МБ.')
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if (len(entries) > 4000 or sum(item.file_size for item in entries) > MAX_TOTAL
                    or len({item.filename for item in entries}) != len(entries)
                    or 'word/document.xml' not in archive.namelist()
                    or '[Content_Types].xml' not in archive.namelist()):
                raise OfficeError('Пошкоджений або завеликий Word-документ.')
            if any(item.flag_bits & 1 for item in entries):
                raise OfficeError('Word-документ захищений паролем.')
    except (BadZipFile, RuntimeError, OSError) as exc:
        raise OfficeError('Не вдалося прочитати Word-документ.') from exc


def process_documents(documents: list[tuple[str, bytes]], *, pdf: bool = False) -> list[dict]:
    """Never save original DOCX files or start a physical print job.

    Pagination may fail for one input while other files succeed. PDF callers
    require all files, whereas the import screen can retain manual page counts.
    """
    if not 1 <= len(documents) <= MAX_DOCUMENTS or sum(len(data) for _, data in documents) > MAX_TOTAL:
        raise OfficeError('Оберіть 1–300 Word-документів загальним розміром до 150 МБ.')
    for _, data in documents:
        _validate_docx(data)
    status = office_status()
    if not status['available']:
        raise OfficeError(status['reason'])
    with tempfile.TemporaryDirectory(prefix='oz-office-', ignore_cleanup_errors=True) as temporary:
        directory = Path(temporary)
        output = directory / 'pdf'
        output.mkdir()
        inputs, jobs = [], []
        for index, (_, data) in enumerate(documents):
            path = directory / f'document-{index:03d}.docx'
            path.write_bytes(data)
            inputs.append(path)
            jobs.append({'input': str(path), 'output': str(output / (path.stem + '.pdf'))})
        timeout = min(900, 60 + 15 * len(documents))
        try:
            if status['engine'] == 'word':
                script = directory / 'office.ps1'
                script.write_text(_WORD_SCRIPT, encoding='ascii')
                settings = directory / 'settings.json'
                settings.write_text(json.dumps({'pdf': pdf, 'documents': jobs}), encoding='utf-8')
                result_path = directory / 'result.json'
                completed = subprocess.run(
                    [_powershell(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                     '-SettingsPath', str(settings), '-ResultPath', str(result_path)],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0,
                )
                result = json.loads(result_path.read_text(encoding='utf-8-sig')) if result_path.exists() else {}
                if completed.returncode or result.get('ok') is not True:
                    raise OfficeError('Microsoft Word не завершив обробку. ' + str(result.get('error') or completed.stderr or '')[:800])
                results = result.get('documents', [])
                if len(results) != len(documents):
                    raise OfficeError('Microsoft Word повернув неповний результат. Повторіть обробку.')
            else:
                executable = _libreoffice()
                subprocess.run([executable, '-env:UserInstallation=' + (directory / 'profile').as_uri(),
                                '--headless', '--convert-to', 'pdf', '--outdir', str(output), *map(str, inputs)],
                               capture_output=True, timeout=timeout,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
                results = [{'ok': Path(job['output']).is_file(), 'pages': 0,
                            'error': 'LibreOffice не зміг прочитати цей документ.'} for job in jobs]
        except subprocess.TimeoutExpired as exc:
            raise OfficeError('Редактор не завершив обробку вчасно. Перевірте діалогові вікна Word і повторіть спробу.') from exc
        except (OSError, ValueError, TypeError) as exc:
            if isinstance(exc, OfficeError):
                raise
            raise OfficeError('Не вдалося обробити документи у локальному редакторі: ' + str(exc)[:800]) from exc
        processed, total_pdf = [], 0
        for (name, _), job, result in zip(documents, jobs, results):
            item = {'name': name, 'pages': None, 'engine': status['engine'], 'error': str(result.get('error') or '')[:800]}
            if result.get('ok') is True:
                pages = result.get('pages')
                if pdf or status['engine'] == 'libreoffice':
                    import fitz
                    path = Path(job['output'])
                    if not path.is_file() or path.stat().st_size > MAX_FILE:
                        raise OfficeError('Редактор не зберіг коректний PDF розміром до 25 МБ.')
                    total_pdf += path.stat().st_size
                    if total_pdf > MAX_TOTAL:
                        raise OfficeError('PDF-комплект перевищує 150 МБ. Розділіть його на менші частини.')
                    raw = path.read_bytes()
                    try:
                        with fitz.open(stream=raw, filetype='pdf') as document:
                            pages = len(document)
                    except Exception as exc:
                        raise OfficeError('Редактор повернув пошкоджений PDF.') from exc
                    if pdf:
                        item['pdf'] = raw
                if type(pages) is int and 1 <= pages <= 10000:
                    item.update(pages=pages, error='')
            processed.append(item)
        return processed


def export_pdf(data: bytes, filename: str) -> tuple[bytes, str]:
    documents, notes = [], []
    is_bundle = filename.lower().endswith('.zip')
    if is_bundle:
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if (len(entries) > 1000 or sum(entry.file_size for entry in entries) > MAX_TOTAL
                        or len({entry.filename for entry in entries}) != len(entries)):
                    raise OfficeError('Архів завеликий або містить дублікати.')
                for entry in entries:
                    name = Path(entry.filename.replace('\\', '/')).name
                    if not entry.is_dir() and name.lower().endswith('.docx'):
                        if entry.file_size > MAX_FILE:
                            raise OfficeError('Word-файл у комплекті перевищує 25 МБ.')
                        documents.append((name, archive.read(entry)))
                    elif name.lower().endswith('.txt') and entry.file_size <= 1024 * 1024:
                        notes.append((name, archive.read(entry)))
        except (BadZipFile, RuntimeError, OSError) as exc:
            raise OfficeError('Не вдалося прочитати ZIP із Word-файлами.') from exc
    elif filename.lower().endswith('.docx'):
        documents = [(filename, data)]
    else:
        raise OfficeError('Для PDF оберіть DOCX або ZIP із Word-файлами.')
    results = process_documents(documents, pdf=True)
    if any(not result.get('pdf') for result in results):
        raise OfficeError('Не всі документи вдалося перетворити на PDF. ' + '; '.join(
            result['name'] + ': ' + result['error'] for result in results if not result.get('pdf'))[:1200])
    if not is_bundle:
        return results[0]['pdf'], 'application/pdf'
    out = BytesIO()
    used = set()
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        for index, result in enumerate(results, 1):
            name = re.sub(r'[\x00-\x1f]', '_', Path(result['name']).stem)[:180] + '.pdf'
            if name.casefold() in used:
                name = f'{index:03d}_' + name
            used.add(name.casefold())
            archive.writestr(name, result['pdf'])
        for name, raw in notes:
            if name.casefold() not in used:
                archive.writestr(name, raw)
                used.add(name.casefold())
    return out.getvalue(), 'application/zip'


def word_router() -> APIRouter:
    router = APIRouter(prefix='/api/word', tags=['word'])

    @router.get('/status')
    def status():
        return office_status()

    @router.post('/pdf')
    async def pdf(file: UploadFile = File(...)):
        try:
            raw = await file.read(MAX_FILE + 1)
            if not raw or len(raw) > MAX_FILE:
                raise OfficeError('Оберіть Word-файл або ZIP розміром до 25 МБ.')
            data, media_type = await run_in_threadpool(export_pdf, raw, file.filename or '')
            return Response(data, media_type=media_type, headers={'Cache-Control': 'no-store'})
        except OfficeError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            await file.close()

    return router
