from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from lxml import etree
import pytest

from app.assignment_forms import BASE_FILENAMES, create_assignment_form
from app.assignment_library import AssignmentLibrary
from app.assignments_documents import FIELD_LABELS, inspect_assignment_template, render_assignment
from app.documents import TemplateError
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def form(department='vet'):
    return {'name': 'Новий продукт — завдання', 'department': department, 'product_name': 'Новий продукт',
            'standard': 'ТУ У 10.1-12345678-001:2026', 'sample_weight': '', 'aliases': [], 'product_scope': '',
            'indicators': [{'indicator': '', 'method': '', 'notes': ''} for _ in range(3)]}


def document(data):
    with ZipFile(BytesIO(data)) as z:
        return etree.fromstring(z.read('word/document.xml'))


def research_rows(data):
    table = document(data).find('.//w:tbl', NS)
    return [[''.join(cell.xpath('.//w:t/text()', namespaces=NS)) for cell in row.findall('w:tc', NS)]
            for row in table.findall('w:tr', NS)]


@pytest.mark.parametrize('department', list(BASE_FILENAMES))
def test_blank_department_forms_keep_stationery_and_remove_historical_values(department):
    values = form(department)
    values.update(product_name='', standard='')
    data = create_assignment_form(values)
    info = inspect_assignment_template(data)
    assert info['department'] == department
    assert info['fields'] == {key: '' for key in FIELD_LABELS}
    assert research_rows(data)[1:] == [['', '']] * 3
    body_text = ''.join(document(data).xpath('.//w:t/text()', namespaces=NS))
    for historical in ['1397', 'Юлія', 'ГРИГОРЕНКО', '4450:2005', 'ГОСТ', '{{', 'Масова частка', 'Консерви']:
        assert historical not in body_text
    manifest = json.loads((ROOT / 'assets/assignments.json').read_text())
    base = next(x for x in manifest if x['department'] == department and x['filename'] == BASE_FILENAMES[department])
    source = (ROOT / 'assets/assignments' / base['asset']).read_bytes()
    old, new = document(source), document(data)
    for path in ['.//w:sectPr', './/w:tblPr', './/w:tblGrid']:
        if path.endswith('tblPr'):
            for floating in old.findall('.//w:tblpPr', NS):
                floating.getparent().remove(floating)
        assert etree.tostring(old.find(path, NS)) == etree.tostring(new.find(path, NS))
    assert research_rows(source)[0] == research_rows(data)[0]
    with ZipFile(BytesIO(source)) as before, ZipFile(BytesIO(data)) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name == 'word/footer1.xml':
                footer = etree.fromstring(after.read(name))
                assert footer.xpath('.//w:fldSimple/@w:instr', namespaces=NS) == ['PAGE', 'NUMPAGES']
            elif name == 'word/settings.xml':
                settings = etree.fromstring(after.read(name))
                assert settings.find('w:updateFields', NS).get('{' + NS['w'] + '}val') == 'true'
            elif name != 'word/document.xml':
                assert before.read(name) == after.read(name)
    fields = {'sample_number': '1501', 'product_name': 'Новий зразок', 'sample_weight': '3,5 кг',
              'standard': 'НД З АКТА', 'issue_date': '2026-11-11',
              'issuer_name': 'Олександр ПРИКЛАД', 'issuer_position': 'капітан'}
    filled = render_assignment(data, fields)
    assert inspect_assignment_template(filled)['fields'] == {key: '11.11.2026' if key == 'issue_date' else fields[key] for key in FIELD_LABELS}
    assert 'Олександр ПРИКЛАД' in ''.join(document(filled).xpath('.//w:t/text()', namespaces=NS))


def test_blank_form_can_add_indicators_notes_and_xml_characters_without_old_methods():
    values = form()
    values['indicators'] = [
        {'indicator': 'Запах & смак <перевірити>', 'method': 'ДСТУ 1111:2026', 'notes': 'Без сторонніх запахів'},
        {'indicator': 'Масова частка солі', 'method': 'Метод А', 'notes': ''},
        {'indicator': 'Новий показник', 'method': 'Новий метод', 'notes': ''},
        {'indicator': 'Ще один показник', 'method': '', 'notes': ''}]
    data = create_assignment_form(values)
    rows = research_rows(data)
    assert len(rows) == 5
    assert rows[1] == ['Запах & смак <перевірити>Примітка: Без сторонніх запахів', 'ДСТУ 1111:2026']
    assert rows[-1] == ['Ще один показник', '']
    assert all('8449:2015' not in cell for row in rows for cell in row)


def test_blank_form_library_survives_restart_and_updates_its_word_contents(tmp_path):
    library = AssignmentLibrary(tmp_path)
    values = form('bacteriology')
    entry = library.create_form(values)
    assert entry['editable_form'] and entry['form'] == values
    restarted = AssignmentLibrary(tmp_path)
    assert restarted.get(entry['id'])['form'] == values
    edited = deepcopy(values)
    edited['indicators'] = [{'indicator': 'Промислова стерильність', 'method': 'Новий метод', 'notes': ''}]
    edited['standard'] = 'НОВИЙ НД'
    restarted.update_form(entry['id'], edited)
    assert research_rows(restarted.read(entry['id']))[1:] == [['Промислова стерильність', 'Новий метод']]
    assert restarted.get(entry['id'])['standard'] == 'НОВИЙ НД'
    old = next(x for x in library.all() if not x.get('editable_form'))
    original = library.read(old['id'])
    with pytest.raises(TemplateError):
        library.update_form(old['id'], edited)
    assert library.read(old['id']) == original


def test_create_preview_update_and_generate_blank_form_through_api(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        values = form()
        values['indicators'][0] = {'indicator': 'Новий показник', 'method': 'ДСТУ 1111:2026', 'notes': ''}
        preview = client.post('/api/assignments/templates/forms/preview', json=values)
        assert preview.status_code == 200
        assert research_rows(preview.content)[1][0] == 'Новий показник'
        assert len(client.get('/api/assignments/templates').json()['templates']) == 60
        response = client.post('/api/assignments/templates/forms', json=values)
        assert response.status_code == 200, response.text
        entry = response.json()
        values['indicators'].append({'indicator': 'Доданий показник', 'method': 'Метод Б', 'notes': 'Примітка'})
        response = client.put('/api/assignments/templates/' + entry['id'] + '/form', json=values)
        assert response.status_code == 200
        source = client.get('/api/assignments/templates/' + entry['id'] + '/download').content
        assert len(research_rows(source)) == 5
        payload = {'job_id': 'blank-form-act-nd', 'confirmed': True, 'start_number': 1501,
                   'items': [{'id': 'new-product', 'product_name': 'Новий продукт', 'standard': 'НД САМЕ З АКТА',
                              'departments': {'vet': {'template_id': entry['id'], 'allow_standard_override': True}}}]}
        response = client.post('/api/assignments/generate', json=payload)
        assert response.status_code == 200, response.text
        with ZipFile(BytesIO(response.content)) as z:
            data = z.read(next(name for name in z.namelist() if name.endswith('.docx')))
            assert inspect_assignment_template(data)['standard'] == 'НД САМЕ З АКТА'
            assert research_rows(data) == research_rows(source)
        assert client.delete('/api/assignments/templates/' + entry['id']).status_code == 200
        assert len(client.get('/api/assignments/templates').json()['templates']) == 60


def test_blank_form_validation_does_not_save_invalid_rows_or_change_uploaded_templates(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        values = form()
        for overrides in [{'indicators': []}, {'indicators': values['indicators'] * 34}, {'department': '../vet'}, {'name': ' '}]:
            r = client.post('/api/assignments/templates/forms', json={**values, **overrides})
            assert r.status_code == 422
        values['indicators'][0]['method'] = '\x01'
        assert client.post('/api/assignments/templates/forms', json=values).status_code == 422
        assert len(client.get('/api/assignments/templates').json()['templates']) == 60
        values = form();values.update(standard='', product_name='')
        blank = client.post('/api/assignments/templates/forms', json=values)
        assert blank.status_code == 200
        assert blank.json()['standard'] == ''
        assert client.get('/api/assignments/numbering').json()['next_number'] is None
