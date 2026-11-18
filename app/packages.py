"""HTTP interfaces for local cases, their files, revisions and template choices."""
from __future__ import annotations

from io import BytesIO
import json
import mimetypes
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
import math

from .package_checks import check_package
from .workbench_store import WorkStore, WorkbenchError, WORKSPACE, MAX_FILE, safe_filename
from .workspace import DraftUpdate, WorkspaceError, DraftStore, _draft_data
from .act_review import preview_original, reocr_fragment

SCOPE_PATTERN = r'^(?:workspace|[a-f0-9]{32})$'


class PackageValues(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    customer: str = Field(default='', max_length=1000)
    reference: str = Field(default='', max_length=200)
    notes: str = Field(default='', max_length=5000)


class PackageCreate(PackageValues):
    drafts: dict[str, dict] = Field(default_factory=dict, max_length=4)
    copy_scope: str | None = Field(default=None, pattern=SCOPE_PATTERN)


class PackageUpdate(PackageValues):
    status: Literal['draft', 'ready', 'archived']
    expected_revision: int = Field(ge=1, strict=True)
    check_fingerprint: str = Field(default='', max_length=64)


class Session(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(pattern=SCOPE_PATTERN)


class FragmentOCR(Session):
    page: int = Field(ge=1, le=20)
    rotation: int = Field(default=0, ge=0, le=270, multiple_of=90)
    bbox: tuple[float, float, float, float]
    psm: Literal[6, 7] = 6

    @field_validator('bbox')
    @classmethod
    def valid_region(cls, value):
        if (any(not math.isfinite(v) or not 0 <= v <= 1 for v in value)
                or value[2] <= value[0] or value[3] <= value[1]):
            raise ValueError('Виділіть прямокутник у межах сторінки.')
        return value


class Checkpoint(Session):
    label: str = Field(default='', max_length=150)
    data: dict


class SourceBatch(Session):
    section: Literal['acts', 'assignments', 'excel', 'cover_letters']
    group: str = Field(pattern=r'^[a-f0-9]{32}$')
    append: bool = False


class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    family: Literal['protocols', 'assignments']
    template_id: str = Field(pattern=r'^[\w-]{1,200}$')
    favorite: bool | None = None
    used: bool = False


def protocol_file(entry: dict, member: str | None) -> tuple[str, bytes]:
    from .excel_api import MAX_FILE as MAX_PROTOCOL_FILE

    filename, data = entry['filename'], entry['data']
    if entry['section'] != 'excel' or entry['kind'] != 'source':
        raise HTTPException(404, 'Джерело протоколу не знайдено.')
    if member is not None:
        try:
            if Path(filename).suffix.lower() != '.zip':
                raise ValueError
            with ZipFile(BytesIO(data)) as archive:
                info = archive.getinfo(member)
                if info.is_dir() or info.file_size > MAX_PROTOCOL_FILE or Path(member).suffix.lower() not in {'.pdf', '.docx'}:
                    raise ValueError
                filename = safe_filename(member)
                data = archive.read(info)
        except (BadZipFile, KeyError, OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(422, 'Не вдалося відкрити протокол із ZIP.') from exc
    return filename, data


def packages_router(directory: Path, template_exists):
    router = APIRouter(tags=['packages'])
    store = WorkStore(directory)

    def run(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except WorkbenchError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except WorkspaceError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, 'Не вдалося зберегти роботу на диску. Перевірте доступ і вільне місце.') from exc

    def check(package_id):
        drafts, files = store.drafts(package_id), store.files(package_id)
        return check_package(drafts, files, lambda file_id: store.file(package_id, file_id)['data'])

    @router.get('/api/packages')
    def listing(q: str = Query('', max_length=1000), status: Literal['', 'draft', 'ready', 'archived'] = ''):
        return {'packages': run(store.packages, q, status)}

    @router.post('/api/packages')
    def create(values: PackageCreate):
        return run(store.create, values.model_dump(), values.drafts, values.copy_scope)

    @router.get('/api/packages/{package_id}')
    def detail(package_id: str):
        return {'package': run(store.get, package_id), 'drafts': run(store.drafts, package_id), 'files': run(store.files, package_id)}

    @router.patch('/api/packages/{package_id}')
    def edit(package_id: str, values: PackageUpdate):
        if values.status == 'ready':
            report = run(check, package_id)
            if values.check_fingerprint != report['fingerprint']:
                raise HTTPException(409, 'Дані пакета змінилися. Повторіть перевірку узгодженості.')
            if report['errors'] or any(issue['code'] in {'no-output', 'missing-output', 'outdated-output'} for issue in report['issues']):
                raise HTTPException(422, 'Виправте розбіжності та сформуйте актуальні документи перед позначенням пакета перевіреним.')
        return run(store.update, package_id, values.model_dump(), values.expected_revision)

    @router.delete('/api/packages/{package_id}')
    def delete(package_id: str, expected_revision: int = Query(..., ge=1),
               expected_updated_at: str = Query(..., min_length=1, max_length=100)):
        return run(store.delete, package_id, expected_revision, expected_updated_at)

    @router.get('/api/packages/{package_id}/drafts')
    def drafts(package_id: str):
        return run(store.drafts, package_id)

    @router.put('/api/packages/{package_id}/drafts/{section}')
    def save_draft(package_id: str, section: str, values: DraftUpdate):
        return run(store.save_draft, package_id, section, values.data, values.expected_revision)

    @router.delete('/api/packages/{package_id}/drafts/{section}')
    def delete_draft(package_id: str, section: str, expected_revision: int | None = Query(None, ge=0)):
        return run(store.save_draft, package_id, section, None, expected_revision)

    @router.get('/api/packages/{package_id}/check')
    def checks(package_id: str):
        return run(check, package_id)

    @router.get('/api/packages/{package_id}/download')
    def download(package_id: str):
        package = run(store.get, package_id)
        drafts, files, report = run(store.drafts, package_id), run(store.files, package_id), run(check, package_id)
        folders = {'acts': 'Протоколи', 'assignments': 'Завдання', 'cover_letters': 'Супровідні', 'excel': 'Таблиці'}
        out, seen = BytesIO(), set()
        def write(archive, folder, filename, data):
            name = folder + '/' + safe_filename(filename)
            n = 2
            while name.casefold() in seen:
                name = folder + '/' + safe_filename(filename, f' ({n})')
                n += 1
            seen.add(name.casefold())
            archive.writestr(name, data)
        with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
            for entry in files:
                # Preserve sources and previous outputs; the latter are explicitly separated.
                folder = 'Джерела' if entry['kind'] in {'source', 'excel_template', 'attachment'} else folders[entry['section']]
                if not entry['active']:
                    folder = 'Попередні файли/' + folder
                raw = run(store.file, package_id, entry['id'])['data']
                if entry['kind'].startswith('output_') and entry['filename'].lower().endswith('.zip'):
                    try:
                        with ZipFile(BytesIO(raw)) as bundle:
                            members = bundle.infolist()
                            if len(members) > 500 or sum(member.file_size for member in members) > MAX_FILE:
                                raise ValueError('Завеликий архів.')
                            for member in members:
                                if not member.is_dir() and not member.flag_bits & 1:
                                    write(archive, folder, member.filename.replace('\\', '/').rsplit('/', 1)[-1], bundle.read(member))
                        continue
                    except (BadZipFile, ValueError, RuntimeError):
                        pass
                write(archive, folder, entry['filename'], raw)
            archive.writestr('Пакет.json', json.dumps({'package': package, 'drafts': drafts, 'files': files}, ensure_ascii=False, indent=2))
            lines = [package['title'], 'Перевірка узгодженості пакета', f'Помилок: {report["errors"]}. Зауважень: {report["warnings"]}.', '']
            lines.extend(('ПОМИЛКА: ' if issue['level'] == 'error' else 'УВАГА: ') + issue['message'] for issue in report['issues'])
            lines.append('\nЦе перевірка узгодженості реквізитів. Результати випробувань та правильність змісту перевіряє відповідальна особа.')
            archive.writestr('ПЕРЕВІРКА.txt', '\n'.join(lines))
        filename = safe_filename(package['title'].replace('/', '_').replace('\\', '_') + '.zip')
        return Response(out.getvalue(), media_type='application/zip', headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename)})

    @router.get('/api/workbench/session')
    def session():
        scope = run(store.session)
        package = run(store.get, scope) if scope != WORKSPACE else None
        if package and package['status'] == 'archived':
            scope, package = WORKSPACE, None
        return {'scope': scope, 'package': package}

    @router.put('/api/workbench/session')
    def save_session(values: Session):
        return {'scope': run(store.session, values.scope)}

    @router.get('/api/workbench/files')
    def files(scope: str = Query(WORKSPACE, pattern=SCOPE_PATTERN)):
        return {'files': run(store.files, scope)}

    @router.get('/api/workbench/files/{file_id}/preview')
    def preview(file_id: str, scope: str = Query(WORKSPACE, pattern=SCOPE_PATTERN),
                page: int = Query(1, ge=1, le=20), rotation: int = Query(0, ge=0, le=270, multiple_of=90),
                member: str | None = Query(None, max_length=4096)):
        source = run(store.file, scope, file_id)
        filename, original = (source['filename'], source['data']) if member is None else protocol_file(source, member)
        try:
            data, pages = preview_original(original, filename, page, rotation)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(data, media_type='image/png', headers={'X-Page-Count':str(pages), 'Cache-Control':'no-store'})

    @router.post('/api/workbench/files/{file_id}/reocr')
    def reread(file_id: str, values: FragmentOCR):
        source = run(store.file, values.scope, file_id)
        if source['kind'] != 'source':
            raise HTTPException(422, 'Оберіть оригінал акта для повторного розпізнавання.')
        try:
            return reocr_fragment(source['data'], source['filename'], values.page,
                                  values.rotation, values.bbox, values.psm)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post('/api/workbench/files')
    async def add_file(file: UploadFile = File(...), scope: str = Form(WORKSPACE, pattern=SCOPE_PATTERN),
                       section: str = Form(...), kind: str = Form(...), draft: str = Form(''), group: str = Form('', pattern=r'^(?:[a-f0-9]{32})?$')):
        try:
            content = await file.read(MAX_FILE + 1)
            try:
                data = _draft_data(json.loads(draft), section=section) if draft else None
            except (ValueError, WorkspaceError) as exc:
                raise HTTPException(422, 'Некоректна чернетка для створеного файла.') from exc
            return run(store.add_file, scope, section, kind, file.filename or 'Документ', content, data, group)
        finally:
            await file.close()

    @router.get('/api/workbench/files/{file_id}')
    def file(file_id: str, scope: str = Query(WORKSPACE, pattern=SCOPE_PATTERN)):
        entry = run(store.file, scope, file_id)
        return Response(entry['data'], media_type=mimetypes.guess_type(entry['filename'])[0] or 'application/octet-stream',
                        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(entry['filename'])})

    @router.get('/api/workbench/files/{file_id}/protocol')
    def protocol_source(file_id: str, scope: str = Query(WORKSPACE, pattern=SCOPE_PATTERN),
                        member: str | None = Query(None, max_length=4096)):
        filename, data = protocol_file(run(store.file, scope, file_id), member)
        suffix = Path(filename).suffix.lower()
        if suffix not in {'.pdf', '.docx'}:
            raise HTTPException(415, 'Підтримуються протоколи PDF і DOCX.')
        return Response(data, media_type=mimetypes.guess_type(filename)[0],
                        headers={'Content-Disposition': "inline; filename*=UTF-8''" + quote(filename),
                                 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})

    @router.post('/api/workbench/sources')
    def activate_sources(values: SourceBatch):
        return run(store.activate_sources, values.scope, values.section, values.group, values.append)

    @router.get('/api/workbench/versions')
    def versions(scope: str = Query(WORKSPACE, pattern=SCOPE_PATTERN), letter_id: str = Query(..., max_length=100)):
        return {'versions': run(store.versions, scope, letter_id)}

    @router.post('/api/workbench/versions')
    def checkpoint(values: Checkpoint):
        run(store.capture, values.scope, values.data, values.label)
        return {'saved': True}

    @router.get('/api/workbench/versions/{version_id}')
    def version(version_id: str, scope: str = Query(WORKSPACE, pattern=SCOPE_PATTERN)):
        return run(store.version, scope, version_id)

    @router.get('/api/template-choices')
    def choices():
        return {'choices': run(store.choices)}

    @router.patch('/api/template-choices')
    def choose(values: Choice):
        if not template_exists(values.family, values.template_id):
            raise HTTPException(404, 'Шаблон більше не доступний у бібліотеці.')
        return run(store.choose, values.family, values.template_id, values.favorite, values.used)

    return router
