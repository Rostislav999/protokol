from fastapi.testclient import TestClient
from app.main import create_app
from app.preferences import Appearance


def test_legacy_appearance_defaults_and_partial_updates(tmp_path):
    (tmp_path / 'appearance.json').write_text('{"text_size":"large"}', encoding='utf-8')
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/preferences').json() == {**Appearance().model_dump(), 'text_size': 'large'}
        choices = dict(theme='dark', density='compact', accent='violet', editor_mode='preview')
        assert client.patch('/api/preferences', json=choices).json() == dict(text_size='large', **choices)
        # The old UI sends PUT with just text_size; other choices must survive.
        assert client.put('/api/preferences', json={'text_size': 'extra_large'}).json() == dict(text_size='extra_large', **choices)
        assert client.patch('/api/preferences', json={}).json() == dict(text_size='extra_large', **choices)
        expected = client.get('/api/preferences').json()
        for field, invalid in [('text_size', 'tiny'), ('theme', 'night'), ('density', 'dense'),
                               ('accent', '#123456'), ('editor_mode', 'sideways'), ('theme', None), ('unknown', True)]:
            assert client.patch('/api/preferences', json={field: invalid}).status_code == 422
            assert client.get('/api/preferences').json() == expected
    with TestClient(create_app(tmp_path)) as restarted:
        assert restarted.get('/api/preferences').json() == expected
        assert restarted.patch('/api/preferences', json=Appearance().model_dump()).json() == Appearance().model_dump()


def test_appearance_is_local_to_each_data_directory(tmp_path):
    with TestClient(create_app(tmp_path / 'user-a')) as first, TestClient(create_app(tmp_path / 'user-b')) as second:
        assert first.patch('/api/preferences', json={'theme': 'system', 'accent': 'blue'}).status_code == 200
        assert second.get('/api/preferences').json() == Appearance().model_dump()
        assert first.get('/api/preferences').json()['theme'] == 'system'


def test_invalid_saved_appearance_is_not_silently_overwritten(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        path = tmp_path / 'appearance.json'
        path.write_text('{bad json', encoding='utf-8')
        assert client.get('/api/preferences').status_code == 422
        assert client.patch('/api/preferences', json={'theme': 'dark'}).status_code == 422
        assert path.read_text(encoding='utf-8') == '{bad json'
