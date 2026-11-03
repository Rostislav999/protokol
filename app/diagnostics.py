"""Downloadable technical reports without document bodies, filenames or forms."""
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import platform
import shutil
import sys
from threading import RLock
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .word_tools import office_status


class ReportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(default='', max_length=3000)
    view: str = Field(default='', pattern=r'^[a-z_]{0,30}$')
    width: int = Field(default=0, ge=0, le=20000)
    height: int = Field(default=0, ge=0, le=20000)
    theme: str = Field(default='', pattern=r'^(?:light|dark|)$')
    events: list[dict] = Field(default_factory=list, max_length=30)


class Diagnostics:
    def __init__(self, directory):
        self.path = Path(directory).parent / 'logs' / 'diagnostics.jsonl'
        self.lock = RLock()

    def record(self, method, route, status, category='http'):
        code = 'ERR-' + uuid4().hex[:12].upper()
        entry = dict(code=code, time=datetime.now(timezone.utc).isoformat(), method=method,
                     route=route, status=status, category=category)
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > 256 * 1024:
                    self.path.replace(self.path.with_suffix('.previous.jsonl'))
                with self.path.open('a', encoding='utf-8') as output:
                    output.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass  # Disk errors must not prevent reporting the original failure.
        return code

    def events(self):
        try:
            with self.lock, self.path.open('rb') as source:
                source.seek(max(0, self.path.stat().st_size - 128 * 1024))
                rows = source.read().decode('utf-8', errors='replace').splitlines()[-200:]
            result = []
            for line in rows:
                try:
                    value = json.loads(line)
                    result.append({key: value[key] for key in ('code', 'time', 'method', 'route', 'status', 'category')})
                except (ValueError, KeyError, TypeError):
                    continue
            return result
        except OSError:
            return []


class DiagnosticMiddleware:
    def __init__(self, app, recorder):
        self.app, self.recorder = app, recorder

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path', '').startswith('/api/'):
            return await self.app(scope, receive, send)
        async def respond(message):
            if message['type'] == 'http.response.start' and message['status'] >= 400:
                route = getattr(scope.get('route'), 'path', '/api/unknown')
                if not route.startswith('/api/diagnostics'):
                    code = self.recorder.record(scope.get('method', ''), route, message['status'])
                    message = {**message, 'headers': [*message.get('headers', []), (b'x-protokol-error-id', code.encode())]}
            await send(message)
        await self.app(scope, receive, respond)


def diagnostics_router(app, directory, recorder, manager):
    router = APIRouter(prefix='/api/diagnostics', tags=['diagnostics'])

    async def exception(request, exc):
        route = getattr(request.scope.get('route'), 'path', '/api/unknown')
        code = recorder.record(request.method, route, 500, type(exc).__name__)
        return JSONResponse({'detail': 'Не вдалося завершити операцію. Спробуйте повторити її або збережіть технічний звіт. Код: ' + code},
                            status_code=500, headers={'X-Protokol-Error-ID': code})
    app.add_exception_handler(Exception, exception)

    @router.post('/export')
    def export(values: ReportInput):
        from scripts.tesseract_runtime import data_directory
        models = data_directory()
        try:
            import PySide6
            qt_version = PySide6.__version__
        except ImportError:
            qt_version = ''
        client_events = []
        for event in values.events:
            client_events.append({
                'kind': event.get('kind') if event.get('kind') in ('js-error', 'unhandled-promise') else 'interface',
                'script': str(event.get('script', 'interface')).rsplit('/', 1)[-1][:100] if str(event.get('script', '')).endswith('.js') else 'interface',
                'line': int(event.get('line', 0)) if isinstance(event.get('line'), int) else 0,
            })
        with manager.lock:
            jobs = [{'operation': job.operation, 'status': job.status} for job in list(manager.jobs.values())[-20:]]
        report = dict(format='oztausya-diagnostics', version=app.version, created_at=datetime.now(timezone.utc).isoformat(),
                      problem_description=values.description,
                      system={'os': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
                              'python': platform.python_version(), 'qt': qt_version, 'standalone': bool(getattr(sys, 'frozen', False))},
                      components={'tesseract': bool(shutil.which('tesseract')), 'ocr_models': [name for name in ('ukr', 'rus', 'eng') if (models / (name + '.traineddata')).is_file()], 'office': office_status()},
                      interface={'view': values.view, 'width': values.width, 'height': values.height, 'theme': values.theme, 'events': client_events},
                      server_events=recorder.events(),
                      processing=jobs)
        output = BytesIO()
        with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('diagnostic.json', json.dumps(report, ensure_ascii=False, indent=2))
            archive.writestr('ЧИТАТИ.txt', 'Технічний звіт ОЗтаУСЯ.\nМістить опис проблеми, введений користувачем, версії компонентів та технічні події.\nТексти документів, їхні файли, імена файлів та довідники не додаються.\nЗвіт збережено локально; програма його нікуди не надсилає.\n')
        return Response(output.getvalue(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="OZtaUSYA-diagnostics.zip"'})
    return router
