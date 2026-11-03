from io import BytesIO
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.main import create_app


def test_technical_report_omits_document_contents_names_and_directory_values(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        secret = 'PRIVATE_DOCUMENT_CUSTOMER_00123456'
        client.put('/api/workspace/drafts/acts', json={'data': {'items': [], 'text': secret, 'source': secret + '.pdf'}})
        failure = client.get('/api/templates/' + secret + '/download')
        assert failure.status_code == 404 and failure.headers['X-Protokol-Error-ID'].startswith('ERR-')
        response = client.post('/api/diagnostics/export', json={'description': 'Помилка читання', 'view': 'acts', 'width': 1000, 'height': 800, 'theme': 'dark',
                                                              'events': [{'kind': 'js-error', 'script': 'app.js', 'line': 10, 'message': secret}]})
        assert response.status_code == 200
        with ZipFile(BytesIO(response.content)) as archive:
            raw = archive.read('diagnostic.json').decode()
            report = json.loads(raw)
            assert report['problem_description'] == 'Помилка читання'
            assert report['interface']['events'] == [{'kind': 'js-error', 'script': 'app.js', 'line': 10}]
            assert report['server_events'][-1]['route'] == '/api/templates/{template_id}/download'
            assert secret not in raw and str(tmp_path) not in raw
            assert set(archive.namelist()) == {'diagnostic.json', 'ЧИТАТИ.txt'}


def test_unexpected_errors_have_a_reference_and_do_not_disclose_exception_text(tmp_path):
    app = create_app(tmp_path)
    @app.get('/api/crash-for-test')
    def crash():
        raise RuntimeError('PRIVATE_DOCUMENT_CONTENT')
    app.router.routes.insert(0, app.router.routes.pop())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/api/crash-for-test')
        assert response.status_code == 500
        assert response.headers['X-Protokol-Error-ID'] in response.json()['detail']
        assert 'PRIVATE_DOCUMENT_CONTENT' not in response.text
        report = client.post('/api/diagnostics/export', json={})
        with ZipFile(BytesIO(report.content)) as archive:
            value = json.loads(archive.read('diagnostic.json'))
            assert value['server_events'][-1]['category'] == 'RuntimeError'
