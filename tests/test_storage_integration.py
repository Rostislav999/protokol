"""Backups and drafts operate on the real app without racing file responses."""
import asyncio
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.main import create_app
from app.storage_lock import DataAccessMiddleware


def test_full_app_backup_restores_directories_numbering_and_draft(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        saved = client.post('/api/settings/suppliers', json={'name': 'ТОВ «Тест»', 'code': '015', 'edrpou': '00001234'})
        assert saved.status_code == 200
        draft = {'common': {'supplier': 'ТОВ «Тест»'}, 'items': [{'id': 'one', 'product_name': 'Паштет'}]}
        assert client.put('/api/workspace/drafts/acts', json={'data': draft, 'expected_revision': 0}).status_code == 200
        backup = client.get('/api/backups/export')
        assert backup.status_code == 200
        with ZipFile(BytesIO(backup.content)) as archive:
            assert any(name.endswith('numbering.sqlite3') for name in archive.namelist())
        assert client.delete('/api/settings/suppliers/' + saved.json()['id']).status_code == 200
        client.put('/api/workspace/drafts/acts', json={'data': {'items': []}, 'expected_revision': 1})
        restored = client.post('/api/backups/restore', files={'file': ('backup.zip', backup.content)}, data={'confirmed': 'true'})
        assert restored.status_code == 200, restored.text
        assert client.get('/api/settings').json()['suppliers'][0]['edrpou'] == '00001234'
        assert client.get('/api/workspace/drafts').json()['sections']['acts']['data'] == draft
        assert len(client.get('/api/templates').json()['templates']) == 22
        assert len(client.get('/api/assignments/templates').json()['templates']) == 60
        assert client.get('/api/backups/status').json()['backups']
        # Even a window using an old revision that matches the archive must
        # not silently overwrite the just-restored state.
        stale = client.put('/api/workspace/drafts/acts', json={'data': {'text': 'stale window'}, 'expected_revision': 1})
        assert stale.status_code == 409


def test_data_lock_covers_entire_streamed_response(tmp_path):
    async def exercise():
        active = 0
        maximum = 0

        async def application(scope, receive, send):
            nonlocal active, maximum
            active += 1
            maximum = max(active, maximum)
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await asyncio.sleep(0.025)
            await send({'type': 'http.response.body', 'body': b'content', 'more_body': False})
            active -= 1

        middleware = DataAccessMiddleware(application, tmp_path / 'data')
        async def receive():
            return {'type': 'http.request'}
        async def send(message):
            await asyncio.sleep(0)
        await asyncio.gather(*(middleware({'type': 'http', 'path': '/api/templates/download'}, receive, send) for _ in range(3)))
        assert maximum == 1
    asyncio.run(exercise())


def test_canceled_request_releases_data_lock(tmp_path):
    async def exercise():
        started = asyncio.Event()
        async def application(scope, receive, send):
            if scope['path'].endswith('wait'):
                started.set()
                await asyncio.Event().wait()
        async def receive():
            return {'type': 'http.request'}
        async def send(message):
            pass
        middleware = DataAccessMiddleware(application, tmp_path / 'data')
        pending = asyncio.create_task(middleware({'type': 'http', 'path': '/api/wait'}, receive, send))
        await started.wait()
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        await asyncio.wait_for(middleware({'type': 'http', 'path': '/api/next'}, receive, send), timeout=2)
    asyncio.run(exercise())
