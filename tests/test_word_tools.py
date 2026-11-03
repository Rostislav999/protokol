from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi.testclient import TestClient
import fitz
import pytest

from app import cover_letters, word_tools
from app.cover_letters import LetterBatch, render_letter
from app.main import create_app


def docx():
    payload = LetterBatch(records=[dict(number='87-012-2972-1259-2', date='01.10.2026', supplier='ТОВ «Тест»', pages=3)])
    return render_letter(payload.records, payload, False)


def pdf(pages=2):
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((70, 70), 'Page ' + str(index + 1))
    return document.tobytes()


@pytest.fixture
def word(monkeypatch):
    monkeypatch.setattr(word_tools, 'office_status', lambda: {'available': True, 'engine': 'word', 'label': 'Microsoft Word', 'reason': ''})
    monkeypatch.setattr(word_tools, '_powershell', lambda: 'powershell.exe')


def test_worker_uses_safe_local_paths_and_count_and_pdf_leave_sources_unchanged(word, monkeypatch):
    source = docx()
    paths = []
    def run(command, **options):
        settings_path = Path(command[command.index('-SettingsPath') + 1])
        settings = json.loads(settings_path.read_text())
        for index, item in enumerate(settings['documents']):
            path = Path(item['input'])
            assert path.name == f'document-{index:03d}.docx'
            assert path.parent == settings_path.parent
            assert path.read_bytes() == source
            paths.append(path)
            if settings['pdf']:
                Path(item['output']).write_bytes(pdf(3))
        Path(command[command.index('-ResultPath') + 1]).write_text(json.dumps({'ok': True, 'documents': [
            {'ok': True, 'pages': 2, 'error': ''} for _ in settings['documents']
        ]}), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(word_tools.subprocess, 'run', run)
    counts = word_tools.process_documents([('../../$(not-a-command) Ω.docx', source)])
    assert counts[0]['pages'] == 2 and 'pdf' not in counts[0]
    exported = word_tools.process_documents([('Лист.docx', source)], pdf=True)
    assert exported[0]['pages'] == 3
    assert exported[0]['pdf'].startswith(b'%PDF')
    assert source == docx()
    assert paths and all(not path.exists() for path in paths)


def test_bad_documents_do_not_start_word(word, monkeypatch):
    monkeypatch.setattr(word_tools.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Office started for invalid input'))
    for documents in ([], [('bad.docx', b'bad')], [('empty.docx', b'')]):
        with pytest.raises(word_tools.OfficeError):
            word_tools.process_documents(documents)


def test_full_assignment_batch_supports_three_departments_for_100_samples(word, monkeypatch):
    def run(command, **options):
        settings = json.loads(Path(command[command.index('-SettingsPath') + 1]).read_text())
        assert len(settings['documents']) == 300
        Path(command[command.index('-ResultPath') + 1]).write_text(json.dumps({'ok': True, 'documents': [
            {'ok': True, 'pages': 1, 'error': ''} for _ in settings['documents']
        ]}))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(word_tools.subprocess, 'run', run)
    source=docx()
    result=word_tools.process_documents([(f'{index}.docx', source) for index in range(300)])
    assert len(result)==300 and all(item['pages']==1 for item in result)


def test_timeout_is_actionable_and_cleans_temporary_files(word, monkeypatch):
    paths = []
    def run(command, **options):
        paths.append(Path(command[command.index('-SettingsPath') + 1]))
        raise subprocess.TimeoutExpired(command, options['timeout'])
    monkeypatch.setattr(word_tools.subprocess, 'run', run)
    with pytest.raises(word_tools.OfficeError, match='вчасно'):
        word_tools.process_documents([('letter.docx', docx())], pdf=True)
    assert paths and all(not path.exists() for path in paths)


def test_pdf_archive_preserves_review_notes_and_never_writes_archive_paths(monkeypatch):
    def convert(documents, **options):
        assert [name for name, data in documents] == ['first.docx', 'first.docx']
        return [dict(name=name, pdf=pdf(1), pages=1, error='') for name, data in documents]
    monkeypatch.setattr(word_tools, 'process_documents', convert)
    bundle = BytesIO()
    with ZipFile(bundle, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('../../first.docx', docx())
        archive.writestr('nested/first.docx', docx())
        archive.writestr('ПЕРЕВІРИТИ.txt', 'Перевірте результати випробувань'.encode())
    converted, media = word_tools.export_pdf(bundle.getvalue(), 'protocols.zip')
    assert media == 'application/zip'
    with ZipFile(BytesIO(converted)) as archive:
        assert set(archive.namelist()) == {'first.pdf', '002_first.pdf', 'ПЕРЕВІРИТИ.txt'}
        assert archive.read('ПЕРЕВІРИТИ.txt').decode() == 'Перевірте результати випробувань'


def test_pdf_fails_as_a_whole_instead_of_silently_omitting_a_document(monkeypatch):
    monkeypatch.setattr(word_tools, 'process_documents', lambda documents, **options: [dict(name='Лист.docx', pages=None, error='Word зайнятий')])
    with pytest.raises(word_tools.OfficeError, match='Не всі документи'):
        word_tools.export_pdf(docx(), 'letter.docx')


def test_pdf_api_without_local_editor_has_clear_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(word_tools, 'office_status', lambda: {'available': False, 'engine': '', 'label': '', 'reason': 'Установіть Microsoft Word або LibreOffice.'})
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/word/status').json()['available'] is False
        result = client.post('/api/word/pdf', files={'file': ('letter.docx', docx())})
        assert result.status_code == 422
        assert 'Microsoft Word' in result.json()['detail']


def test_libreoffice_can_be_found_in_standard_windows_installation_without_path(tmp_path, monkeypatch):
    executable = tmp_path / 'LibreOffice/program/soffice.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'installed editor')
    monkeypatch.setattr(word_tools.shutil, 'which', lambda name: None)
    monkeypatch.setenv('PROGRAMFILES', str(tmp_path))
    assert word_tools._libreoffice() == str(executable)


def test_docx_import_uses_live_page_count_and_can_keep_manual_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(cover_letters, 'office_status', lambda: {'available': True})
    calls = []
    def count(documents, **options):
        calls.append(documents)
        return [dict(name=name, engine='word', pages=2, error='') for name, data in documents]
    monkeypatch.setattr(cover_letters, 'process_documents', count)
    with TestClient(create_app(tmp_path)) as client:
        result = client.post('/api/generate', json={
            'confirmed': True, 'common': {'customer': 'Замовник', 'supplier': 'ТОВ «Тест»', 'sampling_description': 'Одеса'},
            'items': [{'id': 'one', 'protocol_number': '87-012-2972-1259-2', 'protocol_date': '01.10.2026', 'product_name': 'Свинина тушкована', 'template_id': 'sample-1053'}],
        })
        assert result.status_code == 200
        with ZipFile(BytesIO(result.content)) as archive:
            name = next(name for name in archive.namelist() if name.endswith('.docx'))
            data = archive.read(name)
        imported = client.post('/api/cover-letters/import', files={'files': (name, data)}, data={'exact_pages': 'true'})
        assert imported.status_code == 200, imported.text
        assert imported.json()['records'][0]['pages'] == 2
        assert imported.json()['records'][0]['pages_source'] == 'Word'
        assert not any('метадані' in warning for warning in imported.json()['warnings'])
        assert len(calls) == 1
        manual = client.post('/api/cover-letters/import', files={'files': (name, data)}, data={'exact_pages': 'false'})
        assert manual.status_code == 200
        assert manual.json()['records'][0]['pages_source'] == 'metadata'
        assert len(calls) == 1
        assert any('метадані' in warning for warning in manual.json()['warnings'])


def test_failed_live_count_keeps_import_reviewable(tmp_path, monkeypatch):
    monkeypatch.setattr(cover_letters, 'office_status', lambda: {'available': True})
    monkeypatch.setattr(cover_letters, 'process_documents', lambda documents, **kwargs: [dict(pages=None, engine='word', error='Редактор зайнятий') for _ in documents])
    monkeypatch.setattr(cover_letters, '_read_record', lambda data, name: SimpleNamespace(values={'D': '87-012-2972-1259-2', 'E': '01.10.2026', 'M': 'ТОВ «Тест»'}))
    result = cover_letters.prepare([('protocol.docx', docx())], [], exact_pages=True)
    assert result['records'][0]['pages_source'] == 'metadata'
    assert any('Редактор зайнятий' in warning for warning in result['warnings'])


@pytest.mark.skipif(not shutil.which('soffice'), reason='Requires local LibreOffice')
def test_real_pdf_conversion_preserves_stamp_and_company_address():
    if word_tools.office_status()['engine'] != 'libreoffice':
        pytest.skip('Local integration test uses LibreOffice; Word worker has separate contract tests')
    source = docx()
    result = word_tools.process_documents([('Супровідна.docx', source)], pdf=True)[0]
    with fitz.open(stream=result['pdf'], filetype='pdf') as rendered:
        assert result['pages'] == len(rendered) == 1
        assert 'Код ЄДРПОУ 26614722' in rendered[0].get_text()
        assert 'ТОВ «Тест»' in rendered[0].get_text()
        colours = [span['color'] for block in rendered[0].get_text('dict')['blocks'] for line in block.get('lines', []) for span in line['spans'] if 'E-mail:' in span['text']]
        assert colours == [0x2e75b6]
    assert source == docx()


@pytest.mark.skipif(not shutil.which('soffice'), reason='Requires local LibreOffice')
def test_real_cover_signatures_keep_rank_name_and_surname_on_one_line():
    if word_tools.office_status()['engine'] != 'libreoffice':
        pytest.skip('Local integration test uses LibreOffice')
    documents, expected = [], []
    for count in (1, 12):
        for signer in (None, 'старший лейтенант Олександр СЕРЖАНТ-МІЩЕНКО',
                       'підполковник Ірина Михайлівна ПРИКЛАД'):
            changes = {} if signer is None else dict(general_signer=signer, company_signer=signer)
            payload = LetterBatch(records=[dict(number=f'87-012-2972-{1259+i}-2', date='01.10.2026',
                                                supplier='ТОВ «Тест»', code='012', pages=3)
                                           for i in range(count)], confirmed=True, **changes)
            for general in (True, False):
                value = payload.general_signer if general else payload.company_signer
                documents.append((f'letter-{len(documents)}.docx', render_letter(payload.records, payload, general)))
                expected.append(value.split())
    for words, result in zip(expected, word_tools.process_documents(documents, pdf=True)):
        assert not result['error'], result
        with fitz.open(stream=result['pdf'], filetype='pdf') as rendered:
            # Inspect each word's rendered page and baseline, rather than just
            # checking that the document contains the signer's text somewhere.
            positions = []
            for word in words:
                matches = [(page.number, rect) for page in rendered for rect in page.search_for(word)]
                assert len(matches) == 1, (words, word, matches)
                positions.append(matches[0])
            assert len({page for page, _ in positions}) == 1, words
            assert max(rect.y0 for _, rect in positions) - min(rect.y0 for _, rect in positions) < 1, words
            assert positions[0][1].x0 < positions[-1][1].x0
            assert positions[-1][1].x1 <= rendered[positions[-1][0]].rect.width - 28
