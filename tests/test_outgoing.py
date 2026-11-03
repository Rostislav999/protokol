from io import BytesIO
from pathlib import Path
import sqlite3
from zipfile import ZipFile

from fastapi.testclient import TestClient
from lxml import etree as E
import pytest

from app.main import create_app
from app import cover_letters
from app.outgoing import JournalStore
from app.protocol_excel import W


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as value:
        yield value


def batch(**changes):
    return {'generation_id': 'journal-example', 'confirmed': True, 'include_excel': False,
            'records': [dict(number='87-012-2972-1259-2', date='01.10.2026', supplier='ТОВ «Фірма А», код ЄДРПОУ: 00123456', code='012', pages=3),
                        dict(number='87-025-2972-1260-2', date='02.10.2026', supplier='ТОВ «Фірма Б»', code='025', pages=2)], **changes}


def post(client, path, payload):
    import json
    return client.post('/api/cover-letters/' + path, data={'payload': json.dumps(payload, ensure_ascii=False)})


def text(data):
    with ZipFile(BytesIO(data)) as document:
        root = E.fromstring(document.read('word/document.xml'))
    return '\n'.join(''.join(p.itertext()) for p in root.findall('.//{' + W + '}p'))


def test_preview_never_registers_or_changes_an_outgoing_letter(client):
    payload = batch(confirmed=False)
    assert post(client, 'preview', payload).status_code == 200
    assert post(client, 'generate', payload).status_code == 422
    assert client.get('/api/outgoing').json()['total'] == 0


def test_saved_letters_are_durable_searchable_and_idempotent(client, tmp_path, monkeypatch):
    result = post(client, 'generate', batch())
    assert result.status_code == 200, result.text
    entries = client.get('/api/outgoing').json()['entries']
    assert len(entries) == 3
    assert entries[1]['title'] == 'ТОВ «Фірма А»'
    entry = entries[1]
    with ZipFile(BytesIO(result.content)) as archive:
        original = archive.read(entry['filename'])
    assert client.get('/api/outgoing/' + entry['id'] + '/docx').content == original
    monkeypatch.setattr(cover_letters, 'render_letter', lambda *args, **kwargs: pytest.fail('Cached download was rendered again'))
    assert post(client, 'generate', batch()).content == result.content
    assert client.get('/api/outgoing').json()['total'] == 3
    assert client.get('/api/outgoing', params={'q': 'фІрМа а 1259'}).json()['total'] == 1
    with TestClient(create_app(tmp_path)) as restarted:
        assert restarted.get('/api/outgoing/' + entry['id'] + '/docx').content == original
        detail = restarted.get('/api/outgoing/' + entry['id']).json()
        assert detail['payload']['confirmed'] is False
        assert detail['payload']['edits'][entry['identity']]['recipients'] == ['ТОВ «Фірма А»']


def test_outgoing_number_and_date_fill_the_stamp_and_are_unique_per_year(client):
    payload = batch(registrations={'general': {'number': '0873/001', 'date': '2026-11-03'}})
    result = post(client, 'generate', payload)
    assert result.status_code == 200, result.text
    general = next(entry for entry in client.get('/api/outgoing').json()['entries'] if entry['identity'] == 'general')
    generated = text(client.get('/api/outgoing/' + general['id'] + '/docx').content)
    assert '№ 0873/001' in generated and '03.11.2026' in generated
    assert general['outgoing_number'] == '0873/001'
    assert client.get('/api/outgoing', params={'date_from': '2026-11-03', 'date_to': '2026-11-03', 'q': '0873/001'}).json()['total'] == 1
    duplicate = post(client, 'generate', {**payload, 'generation_id': 'duplicate-number'})
    assert duplicate.status_code == 422
    assert client.get('/api/outgoing').json()['total'] == 3
    next_year = {**payload, 'generation_id': 'next-year', 'registrations': {'general': {'number': '0873/001', 'date': '2027-01-01'}}}
    assert post(client, 'generate', next_year).status_code == 200
    assert client.get('/api/outgoing').json()['total'] == 6


def test_draft_promotes_to_ready_and_sent_status_never_changes_document(client):
    payload = batch(confirmed=False)
    assert post(client, 'save-draft', payload).status_code == 200
    assert {entry['status'] for entry in client.get('/api/outgoing').json()['entries']} == {'draft'}
    assert post(client, 'generate', {**payload, 'confirmed': True}).status_code == 200
    entries = client.get('/api/outgoing').json()['entries']
    assert len(entries) == 3 and {entry['status'] for entry in entries} == {'ready'}
    item = entries[0]
    before = client.get('/api/outgoing/' + item['id'] + '/docx').content
    updated = client.patch('/api/outgoing/' + item['id'], json={'status': 'sent', 'notes': 'Передано особисто', 'expected_updated_at': item['updated_at']})
    assert updated.status_code == 200
    assert updated.json()['sent_at'] and updated.json()['status'] == 'sent'
    assert client.get('/api/outgoing/' + item['id'] + '/docx').content == before
    assert client.get('/api/outgoing', params={'q': 'ПЕРЕДАНО', 'status': 'sent'}).json()['total'] == 1
    stale = client.patch('/api/outgoing/' + item['id'], json={'status': 'draft', 'expected_updated_at': item['updated_at']})
    assert stale.status_code == 409
    post(client, 'generate', {**payload, 'confirmed': True})
    assert client.get('/api/outgoing/' + item['id']).json()['status'] == 'sent'


def test_changed_generation_and_failed_render_leave_history_intact(client, monkeypatch):
    assert post(client, 'generate', batch()).status_code == 200
    assert post(client, 'generate', batch(contact='Інший контакт')).status_code == 409
    def fail(*args, **kwargs):
        raise ValueError('Не вдалося сформувати документ')
    monkeypatch.setattr(cover_letters, 'render_letter', fail)
    assert post(client, 'generate', batch(generation_id='failed-build')).status_code == 422
    assert client.get('/api/outgoing').json()['total'] == 3


@pytest.mark.parametrize('registration', [
    {'number': '001'}, {'date': '2026-02-31'}, {'number': '123\n456', 'date': '2026-11-03'},
])
def test_invalid_outgoing_metadata_never_creates_a_register_entry(client, registration):
    assert post(client, 'generate', batch(registrations={'general': registration})).status_code == 422
    assert client.get('/api/outgoing').json()['total'] == 0


def test_profiles_appearance_and_journal_survive_backup_restore(client, tmp_path):
    profile = client.post('/api/signers', json={'name': 'Начальник', 'position': 'Начальник лабораторії', 'signer': 'майор Ірина ПРИКЛАД', 'contact': 'Контакт 00 000'}).json()
    assert client.put('/api/signer-defaults', json={'general_id': profile['id'], 'company_id': profile['id']}).status_code == 200
    appearance = dict(text_size='extra_large', theme='system', density='compact', accent='violet', editor_mode='preview')
    assert client.patch('/api/preferences', json=appearance).status_code == 200
    assert post(client, 'generate', batch()).status_code == 200
    item = client.get('/api/outgoing').json()['entries'][0]
    original = client.get('/api/outgoing/' + item['id'] + '/docx').content
    saved = client.get('/api/backups/export')
    assert saved.status_code == 200
    assert client.delete('/api/signers/' + profile['id']).status_code == 200
    client.patch('/api/preferences', json={'text_size': 'normal', 'theme': 'light', 'density': 'comfortable', 'accent': 'sage', 'editor_mode': 'auto'})
    restored = client.post('/api/backups/restore', files={'file': ('backup.zip', saved.content, 'application/zip')}, data={'confirmed': 'true'})
    assert restored.status_code == 200, restored.text
    assert client.get('/api/signers').json()['general_id'] == profile['id']
    assert client.get('/api/preferences').json() == appearance
    assert client.get('/api/outgoing/' + item['id'] + '/docx').content == original
    JournalStore.validate(tmp_path / 'outgoing.sqlite3')


def test_invalid_register_schema_is_rejected_even_when_no_rows_exist(tmp_path):
    path = tmp_path / 'broken.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE outgoing_batches(id TEXT)')
        connection.execute('CREATE TABLE outgoing_letters(id TEXT)')
    with pytest.raises(ValueError, match='структура'):
        JournalStore.validate(path)


def test_journal_pdf_is_cached_and_keeps_the_original_word(client, monkeypatch):
    from app import outgoing
    import fitz
    document = fitz.open();document.new_page();raw_pdf=document.tobytes()
    calls = []
    def convert(raw, name):
        calls.append((raw, name))
        return raw_pdf, 'application/pdf'
    monkeypatch.setattr(outgoing, 'export_pdf', convert)
    assert post(client, 'generate', batch()).status_code == 200
    item = client.get('/api/outgoing').json()['entries'][0]
    original = client.get('/api/outgoing/' + item['id'] + '/docx').content
    for _ in range(2):
        result = client.get('/api/outgoing/' + item['id'] + '/pdf')
        assert result.status_code == 200 and result.content == raw_pdf
    assert calls == [(original, item['filename'])]
    assert client.get('/api/outgoing/' + item['id']).json()['has_pdf'] is True
    assert client.get('/api/outgoing/' + item['id'] + '/docx').content == original


@pytest.mark.parametrize('query', [
    {'date_from': '2026-02-31'}, {'date_from': '2026-12-01', 'date_to': '2026-01-01'},
])
def test_invalid_journal_date_ranges_are_actionable(client, query):
    assert client.get('/api/outgoing', params=query).status_code == 422


def test_profile_validation_updates_and_defaults_are_preserved(client):
    values = {'name': 'Підпис Ω', 'position': 'Начальник', 'signer': 'підполковник Іван ПРИКЛАД', 'contact': '00 000'}
    profile = client.post('/api/signers', json=values).json()
    assert client.post('/api/signers', json={**values, 'name': ' підпис Ω '}).status_code == 422
    assert client.put('/api/signer-defaults', json={'general_id': 'f' * 32}).status_code == 422
    assert client.put('/api/signer-defaults', json={'company_id': profile['id']}).status_code == 200
    edited = client.patch('/api/signers/' + profile['id'], json={**values, 'contact': 'Новий контакт'})
    assert edited.status_code == 200
    assert client.get('/api/signers').json()['profiles'][0]['contact'] == 'Новий контакт'
    assert client.post('/api/signers', json={**values, 'position': ' '}).status_code == 422
    assert client.put('/api/preferences', json={'text_size': 'tiny'}).status_code == 422
    assert client.delete('/api/signers/' + profile['id']).status_code == 200
    assert client.get('/api/signers').json()['company_id'] == ''
