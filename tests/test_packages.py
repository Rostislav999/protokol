from copy import deepcopy
from io import BytesIO
import json
import sqlite3
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi.testclient import TestClient
import fitz
import pytest

from app.main import create_app
from app.package_checks import check_package
from app.workbench_store import WorkStore, fingerprint


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        yield client


def letter(body='Початковий текст'):
    return {'id': 'general', 'title': 'Загальна супровідна', 'content': {
        'recipients': ['Замовник'], 'body': [body], 'attachments': [],
        'position': 'Начальник лабораторії', 'signer': 'майор Ірина ПРИКЛАД', 'contact': ''}}


def create(client, title='Акт №125', **values):
    result = client.post('/api/packages', json={'title': title, **values})
    assert result.status_code == 200, result.text
    return result.json()


def save(client, package, section, data, revision=0):
    return client.put(f'/api/packages/{package["id"]}/drafts/{section}', json={'data': data, 'expected_revision': revision})


def upload(client, scope, section='acts', kind='source', name='акт.txt', data=b'act', draft=None, group=''):
    fields = {'scope': scope, 'section': section, 'kind': kind, 'group': group}
    if draft is not None:
        fields['draft'] = json.dumps(draft, ensure_ascii=False)
    result = client.post('/api/workbench/files', files={'file': (name, data)}, data=fields)
    assert result.status_code == 200, result.text
    return result.json()


def test_packages_keep_all_drafts_separate_and_reject_stale_edits(client):
    root = {'items': [{'id': 'old', 'product_name': 'Старий акт'}]}
    client.put('/api/workspace/drafts/acts', json={'data': root})
    first, second = create(client), create(client, 'Інший акт')
    for section in ('acts', 'assignments', 'excel', 'cover_letters'):
        result = save(client, first, section, {'items': [], 'confirmed': True, 'nested': {'review_confirmed': True}})
        assert result.status_code == 200
        assert result.json()['data']['confirmed'] is False
        assert result.json()['data']['nested']['review_confirmed'] is False
    assert client.get(f'/api/packages/{second["id"]}/drafts').json()['sections'] == {}
    assert client.get('/api/workspace/drafts').json()['sections']['acts']['data'] == root
    assert save(client, first, 'acts', root, 0).status_code == 409
    deleted = client.delete(f'/api/packages/{first["id"]}/drafts/acts?expected_revision=1')
    assert deleted.json()['revision'] == 2
    assert save(client, first, 'acts', root, 1).status_code == 409
    assert save(client, first, 'acts', root, 2).status_code == 200
    assert client.get('/api/packages/workspace').status_code == 404


def test_package_search_metadata_conflicts_and_archive(client):
    package = create(client, 'Свинина тушкована', customer='Військова частина', reference='125/26', notes='Поставка №7')
    assert client.get('/api/packages', params={'q': 'СВИНИНА 125'}).json()['packages'][0]['id'] == package['id']
    values = {key: package[key] for key in ('title', 'customer', 'reference', 'notes')}
    archived = client.patch('/api/packages/' + package['id'], json={**values, 'status': 'archived', 'expected_revision': 1})
    assert archived.status_code == 200
    assert save(client, package, 'acts', {'items': []}).status_code == 409
    assert client.patch('/api/packages/' + package['id'], json={**values, 'status': 'draft', 'expected_revision': 1}).status_code == 409
    assert client.get('/api/packages?status=archived').json()['packages'][0]['status'] == 'archived'
    assert client.put('/api/workbench/session', json={'scope': package['id']}).status_code == 200
    assert client.get('/api/workbench/session').json()['scope'] == 'workspace'


def test_original_files_survive_cancelled_source_batches_without_replacing_current_source(client):
    package = create(client)
    groups = ['a' * 32, 'b' * 32]
    old = upload(client, package['id'], group=groups[0])
    assert old['active'] == 0
    assert client.post('/api/workbench/sources', json={'scope': package['id'], 'section': 'acts', 'group': groups[0]}).status_code == 200
    pending = upload(client, package['id'], name='новий.txt', data=b'new', group=groups[1])
    entries = client.get('/api/workbench/files?scope=' + package['id']).json()['files']
    assert next(value for value in entries if value['id'] == old['id'])['active'] == 1
    assert next(value for value in entries if value['id'] == pending['id'])['active'] == 0
    assert client.get(f'/api/workbench/files/{pending["id"]}?scope={package["id"]}').content == b'new'
    assert client.get(f'/api/workbench/files/{old["id"]}?scope=workspace').status_code == 404
    assert client.post('/api/workbench/sources', json={'scope': package['id'], 'section': 'acts', 'group': groups[1]}).status_code == 200
    assert client.get('/api/workbench/files?scope=' + package['id']).json()['files'][0]['active'] == 1


def test_copy_current_workspace_omits_other_acts_and_stale_outputs(client):
    current = {'items': [], 'source': 'поточний.txt', 'sourceGroup': 'a' * 32}
    first = upload(client, 'workspace', name='поточний.txt', group='a' * 32)
    upload(client, 'workspace', name='інший.txt', group='b' * 32)
    client.post('/api/workbench/sources', json={'scope': 'workspace', 'section': 'acts', 'group': 'a' * 32})
    upload(client, 'workspace', kind='output_word', name='старі.docx', data=b'old', draft={'items': [{'product_name': 'Інша справа'}]})
    package = create(client, drafts={'acts': current}, copy_scope='workspace')
    files = client.get('/api/workbench/files?scope=' + package['id']).json()['files']
    assert [value['filename'] for value in files] == ['поточний.txt']
    assert files[0]['digest'] == first['digest']
    assert client.get(f'/api/packages/{package["id"]}/drafts').json()['sections']['acts']['data'] == current


def test_letter_history_is_automatic_bounded_and_scoped(client, tmp_path, monkeypatch):
    import app.workbench_store as module
    monkeypatch.setattr(module, 'HISTORY_LIMIT', 3)
    first, second = create(client), create(client, 'Інша справа')
    original = {'letters': [letter()], 'selectedLetterId': 'general'}
    assert save(client, first, 'cover_letters', original).status_code == 200
    unchanged = {**original, 'selectedLetterId': 'company'}
    assert save(client, first, 'cover_letters', unchanged, 1).status_code == 200
    path = '/api/workbench/versions?scope=' + first['id'] + '&letter_id=general'
    assert len(client.get(path).json()['versions']) == 1
    assert save(client, first, 'cover_letters', {'letters': [letter('Невдала зміна')]}, 2).status_code == 200
    versions = client.get(path).json()['versions']
    version = client.get(f'/api/workbench/versions/{versions[-1]["id"]}?scope={first["id"]}').json()
    assert version['data']['content']['body'] == ['Початковий текст']
    assert 'registration' not in version['data']
    assert client.get(f'/api/workbench/versions/{versions[-1]["id"]}?scope={second["id"]}').status_code == 404
    assert client.post('/api/workbench/versions', json={'scope': first['id'], 'label': 'Погоджений текст', 'data': original}).status_code == 200
    assert client.get(path).json()['versions'][0]['label'] == 'Погоджений текст'
    assert save(client, first, 'cover_letters', {'letters': [letter('Конфлікт')]}, 0).status_code == 409
    assert len(client.get(path).json()['versions']) == 3
    assert any(value['label'] == 'Погоджений текст' for value in client.get(path).json()['versions'])
    assert save(client, first, 'cover_letters', {'letters': [letter('Четверта')]}, 3).status_code == 200
    assert len(client.get(path).json()['versions']) == 3
    WorkStore.validate(tmp_path / 'workbench.sqlite3')


def test_workspace_letter_autosaves_also_keep_version_history(client):
    first = client.put('/api/workspace/drafts/cover_letters', json={'data': {'letters': [letter()]}, 'expected_revision': 0})
    assert first.status_code == 200
    assert client.put('/api/workspace/drafts/cover_letters', json={'data': {'letters': [letter('Друга')]}, 'expected_revision': 0}).status_code == 409
    versions = client.get('/api/workbench/versions?letter_id=general').json()['versions']
    assert len(versions) == 1


def test_favorites_and_recent_templates_are_persistent_and_validated(client, tmp_path):
    assert client.patch('/api/template-choices', json={'family': 'protocols', 'template_id': 'sample-1053', 'favorite': True}).status_code == 200
    used = client.patch('/api/template-choices', json={'family': 'protocols', 'template_id': 'sample-1053', 'used': True}).json()
    assert used['favorite'] == 1 and used['uses'] == 1 and used['last_used_at']
    assert client.patch('/api/template-choices', json={'family': 'protocols', 'template_id': 'missing', 'favorite': True}).status_code == 404
    assert client.patch('/api/template-choices', json={'family': 'other', 'template_id': 'sample-1053'}).status_code == 422
    with TestClient(create_app(tmp_path)) as restarted:
        assert restarted.get('/api/template-choices').json()['choices'][0]['favorite'] == 1


def test_case_zip_keeps_sources_and_expands_generated_archives_safely(client):
    package = create(client)
    archive = BytesIO()
    with ZipFile(archive, 'w', ZIP_DEFLATED) as output:
        output.writestr('../../outside.docx', b'generated')
        output.writestr('ПЕРЕВІРИТИ.txt', 'review')
    upload(client, package['id'], name='../../акт.txt', data=b'original')
    upload(client, package['id'], kind='output_word', name='protocols.zip', data=archive.getvalue(), draft={})
    result = client.get('/api/packages/' + package['id'] + '/download')
    assert result.status_code == 200
    with ZipFile(BytesIO(result.content)) as output:
        assert output.read('Джерела/акт.txt') == b'original'
        assert output.read('Протоколи/outside.docx') == b'generated'
        assert {'Пакет.json', 'ПЕРЕВІРКА.txt'} <= set(output.namelist())
        assert all('..' not in name.split('/') for name in output.namelist())


def test_files_history_favorites_and_package_revisions_survive_backup_restore(client, tmp_path):
    package = create(client, drafts={'cover_letters': {'letters': [letter()]}})
    source = upload(client, package['id'])
    client.patch('/api/template-choices', json={'family': 'protocols', 'template_id': 'sample-1053', 'favorite': True})
    backup = client.get('/api/backups/export')
    assert backup.status_code == 200
    assert save(client, package, 'cover_letters', {'letters': [letter('Змінено')]}, 1).status_code == 200
    restored = client.post('/api/backups/restore', files={'file': ('backup.zip', backup.content)}, data={'confirmed': 'true'})
    assert restored.status_code == 200, restored.text
    draft = client.get('/api/packages/' + package['id'] + '/drafts').json()['sections']['cover_letters']
    assert draft['data']['letters'][0]['content']['body'] == ['Початковий текст']
    assert draft['revision'] > 2
    assert save(client, package, 'cover_letters', {'letters': [letter('Старе вікно')]}, 2).status_code == 409
    assert client.get(f'/api/workbench/files/{source["id"]}?scope={package["id"]}').content == b'act'
    assert client.get('/api/template-choices').json()['choices'][0]['favorite'] == 1
    WorkStore.validate(tmp_path / 'workbench.sqlite3')


def test_file_digest_is_checked_even_if_sqlite_integrity_is_valid(client, tmp_path):
    package = create(client)
    upload(client, package['id'])
    path = tmp_path / 'workbench.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE package_files SET data=?", (b'BAD',))
    with pytest.raises(ValueError, match='вміст'):
        WorkStore.validate(path)


def test_consistency_checks_compare_dates_company_attachments_and_excel():
    number = '87-012-2972-1259-2'
    content = letter()
    content['content']['attachments'] = [f'Протокол випробувань №{number} від 01.10.2026 року — на 3 арк. в 1 прим.']
    data = {'acts': {'common': {'supplier': 'ТОВ «Фірма А», код ЄДРПОУ 00123456'}, 'items': [{'protocol_number': number, 'protocol_date': '2026-10-01', 'product_name': 'Консерви'}]},
            'cover_letters': {'records': [{'number': number, 'date': '01.10.2026', 'supplier': 'ТОВ «Фірма А»', 'pages': '3', 'pages_source': 'Word'}], 'letters': [content]},
            'excel': {'records': [{'values': {'D': '№' + number, 'E': '01.10.2026', 'M': '00123456'}}]}}
    assert check_package(data, [])['errors'] == 0
    changed = deepcopy(data)
    changed['cover_letters']['records'][0].update(date='02.10.2026', pages='4')
    changed['excel']['records'][0]['values']['M'] = '00000001'
    codes = {issue['code'] for issue in check_package(changed, [])['issues']}
    assert {'act-cover-date', 'attachment-pages', 'attachment-date', 'excel-date', 'excel-supplier'} <= codes


def test_consistency_checks_count_the_actual_source_pdf_without_ocr():
    number = '87-012-2972-1259-2'
    with fitz.open() as pdf:
        pdf.new_page(); pdf.new_page()
        raw = pdf.tobytes()
    data = {'cover_letters': {'records': [{'number': number, 'source': 'p.pdf', 'date': '01.10.2026', 'pages': '3', 'pages_source': 'manual'}]}}
    files = [{'id': 'source', 'section': 'cover_letters', 'kind': 'source', 'active': 1, 'filename': 'p.pdf', 'fingerprint': 'group'}]
    report = check_package(data, files, lambda _: raw)
    assert report['verified_page_counts'] == 1
    assert any(issue['code'] == 'actual-pages' and issue['level'] == 'error' for issue in report['issues'])


def test_regenerating_an_identical_previous_output_reactivates_it(client):
    package = create(client)
    first = upload(client, package['id'], kind='output_word', name='p.docx', data=b'one', draft={'items': []})
    upload(client, package['id'], kind='output_word', name='p.docx', data=b'two', draft={'items': [{}]})
    again = upload(client, package['id'], kind='output_word', name='p.docx', data=b'one', draft={'items': []})
    assert again['id'] == first['id'] and again['active'] == 1
    assert len(client.get('/api/workbench/files?scope=' + package['id']).json()['files']) == 2


def test_restore_provenance_flags_do_not_make_an_unchanged_document_stale():
    saved = {'common': {'customer': 'Замовник'}, 'items': [{'sample_weight': '7 кг', 'protocol_number': '87-012-2972-1259-2'}]}
    restored = deepcopy(saved)
    restored['items'][0].update(_sample_weight_manual=False, _sample_weight_source='act')
    assert fingerprint(saved) == fingerprint(restored)
    restored['items'][0]['sample_weight'] = '8 кг'
    assert fingerprint(saved) != fingerprint(restored)


def test_consistency_report_preserves_partial_import_warnings():
    result = check_package({'excel': {'records': [], 'warnings': ['Не прочитано damaged.pdf']}}, [])
    assert any(issue['code'] == 'source-warning' and 'damaged.pdf' in issue['message'] for issue in result['issues'])
