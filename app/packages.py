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
from pydantic import BaseModel, ConfigDict, Field

from .package_checks import check_package
from .workbench_store import WorkStore, WorkbenchError, WORKSPACE, MAX_FILE, safe_filename
from .workspace import DraftUpdate, WorkspaceError, DraftStore, _draft_data

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
            base, suffix, n = Path(name).stem, Path(name).suffix, 2
            while name.casefold() in seen:
                name = folder + '/' + base + f' ({n})' + suffix
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
        filename = safe_filename(package['title']) + '.zip'
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

    @router.post('/api/workbench/files')
    async def add_file(file: UploadFile = File(...), scope: str = Form(WORKSPACE, pattern=SCOPE_PATTERN),
                       section: str = Form(...), kind: str = Form(...), draft: str = Form(''), group: str = Form('', pattern=r'^(?:[a-f0-9]{32})?$')):
        try:
            content = await file.read(MAX_FILE + 1)
            try:
                data = _draft_data(json.loads(draft)) if draft else None
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
