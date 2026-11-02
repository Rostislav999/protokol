from datetime import datetime, timezone
import io
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.assignments_documents import inspect_assignment_template
import app.assignments_api as assignment_api


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def request_data(client, job_id="generation-one", start_number=1397):
    templates = client.get("/api/assignments/templates").json()["templates"]
    filenames = {"vet": "все.docx", "bacteriology": "бак.docx", "chemistry": "хім токс.docx"}
    selected = {d: {"template_id": next(t["id"] for t in templates if t["filename"] == name),
                    "sample_weight": "1,575 кг (3 шт.)"} for d, name in filenames.items()}
    return {"job_id": job_id, "confirmed": True, "start_number": start_number,
            "items": [{"id": "sample-one", "product_name": "Свинина тушкована", "standard": "ДСТУ 4450:2005", "departments": selected}]}


def test_all_sixty_templates_departments_and_readonly_metadata(client):
    result = client.get("/api/assignments/templates").json()
    assert len(result["templates"]) == 60
    assert {d["id"] for d in result["departments"]} == {"vet", "bacteriology", "chemistry"}
    assert all(t["standard"] for t in result["templates"])
    assert client.get("/api/assignments/numbering").json() == {"next_number": None}


def test_three_departments_share_sample_number_and_generated_date(client, monkeypatch):
    monkeypatch.setattr(assignment_api, "kyiv_today", lambda: "2026-10-28")
    result = client.post("/api/assignments/generate", json=request_data(client))
    assert result.status_code == 200, result.text
    assert result.headers["X-Sample-Numbers"] == '["1397"]'
    assert result.headers["X-Next-Sample-Number"] == "1398"
    with ZipFile(io.BytesIO(result.content)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".docx")]
        assert len(names) == 3
        for name in names:
            info = inspect_assignment_template(archive.read(name))
            assert info["fields"]["sample_number"] == "1397"
            assert info["fields"]["issue_date"] == "28.10.2026"
            assert info["fields"]["product_name"] == "Свинина тушкована"
            assert info["fields"]["sample_weight"] == "1,575 кг (3 шт.)"


def test_repeated_download_keeps_original_date_and_numbers(client, monkeypatch):
    monkeypatch.setattr(assignment_api, "kyiv_today", lambda: "2026-10-28")
    payload = request_data(client)
    first = client.post("/api/assignments/generate", json=payload)
    monkeypatch.setattr(assignment_api, "kyiv_today", lambda: "2026-10-29")
    second = client.post("/api/assignments/generate", json=payload)
    assert second.status_code == 200
    assert first.content == second.content
    assert second.headers["X-Assignment-Date"] == "2026-10-28"
    assert client.get("/api/assignments/numbering").json()["next_number"] == 1398
    new_sample = request_data(client, "generation-two", 1398)
    new_sample["items"][0]["id"] = "sample-two"
    next_job = client.post("/api/assignments/generate", json=new_sample)
    assert next_job.status_code == 200
    assert next_job.headers["X-Sample-Numbers"] == '["1398"]'
    changed = request_data(client)
    changed["items"][0]["product_name"] += " інша назва"
    assert client.post("/api/assignments/generate", json=changed).status_code == 422


def test_same_sample_keeps_its_number_when_another_department_is_added(client):
    first_payload = request_data(client)
    first_payload["items"][0]["departments"] = {"vet": first_payload["items"][0]["departments"]["vet"]}
    first = client.post("/api/assignments/generate", json=first_payload)
    assert first.status_code == 200
    later = request_data(client, "add-another-department", 1398)
    later["items"][0]["departments"] = {"chemistry": later["items"][0]["departments"]["chemistry"]}
    result = client.post("/api/assignments/generate", json=later)
    assert result.status_code == 200
    assert result.headers["X-Sample-Numbers"] == '["1397"]'
    assert result.headers["X-Next-Sample-Number"] == "1398"
    preview = client.post("/api/assignments/numbering/preview", json={"sample_ids": ["sample-one", "new-unallocated"]}).json()
    assert preview == {"next_number": 1398, "numbers": {"sample-one": "1397"}}


def test_cached_assignment_download_survives_template_deletion(client):
    payload = request_data(client)
    first = client.post("/api/assignments/generate", json=payload)
    for selection in payload["items"][0]["departments"].values():
        assert client.delete("/api/assignments/templates/" + selection["template_id"]).status_code == 200
    repeated = client.post("/api/assignments/generate", json=payload)
    assert repeated.status_code == 200
    assert repeated.content == first.content
    assert client.get("/api/assignments/numbering").json()["next_number"] == 1398


def test_generation_rejects_missing_review_stale_preview_and_wrong_department(client):
    payload = request_data(client)
    payload["confirmed"] = False
    assert client.post("/api/assignments/generate", json=payload).status_code == 422
    payload["confirmed"] = True
    payload["items"][0]["departments"]["vet"]["template_id"] = payload["items"][0]["departments"]["chemistry"]["template_id"]
    assert client.post("/api/assignments/generate", json=payload).status_code == 422
    assert client.get("/api/assignments/numbering").json()["next_number"] is None
    assert client.post("/api/assignments/generate", json=request_data(client)).status_code == 200
    stale = request_data(client, "stale", 1397)
    assert client.post("/api/assignments/generate", json=stale).status_code == 422
    assert client.get("/api/assignments/numbering").json()["next_number"] == 1398


def test_render_failure_does_not_consume_number(client, monkeypatch):
    def broken(*args, **kwargs):
        raise assignment_api.TemplateError("Пошкоджений шаблон")
    monkeypatch.setattr(assignment_api, "render_assignment", broken)
    assert client.post("/api/assignments/generate", json=request_data(client)).status_code == 422
    assert client.get("/api/assignments/numbering").json()["next_number"] is None


def test_different_standard_requires_explicit_review_and_preserves_template_standard(client):
    payload = request_data(client)
    payload["items"][0]["standard"] = "ДСТУ 4450:2006"
    assert client.post("/api/assignments/generate", json=payload).status_code == 422
    for selected in payload["items"][0]["departments"].values():
        selected["allow_standard_override"] = True
    result = client.post("/api/assignments/generate", json=payload)
    assert result.status_code == 200
    with ZipFile(io.BytesIO(result.content)) as archive:
        for name in archive.namelist():
            if name.endswith(".docx"):
                assert inspect_assignment_template(archive.read(name))["standard"] == "ДСТУ 4450:2005"


@pytest.mark.parametrize('standard', ['ДСТУ 4450:2005', 'НД, введений вручну'])
def test_manual_selection_from_whole_library_when_product_has_no_matches(client, standard):
    payload = request_data(client)
    item = payload['items'][0]
    item.update(product_name='Назва з акта, яку програма не впізнала', standard=standard)
    matches = client.post('/api/assignments/match', json={
        'product_name': item['product_name'], 'standard': standard,
    }).json()
    assert not any(matches['departments'].values())
    assert not any(matches['review_departments'].values())
    assert client.post('/api/assignments/generate', json=payload).status_code == 422
    assert client.get('/api/assignments/numbering').json()['next_number'] is None
    for selection in item['departments'].values():
        selection['allow_standard_override'] = True
    result = client.post('/api/assignments/generate', json=payload)
    assert result.status_code == 200, result.text
    with ZipFile(io.BytesIO(result.content)) as archive:
        documents = [n for n in archive.namelist() if n.endswith('.docx')]
        assert len(documents) == 3
        for name in documents:
            info = inspect_assignment_template(archive.read(name))
            assert info['fields']['product_name'] == item['product_name']
            assert info['standard'] == 'ДСТУ 4450:2005'
        assert 'ручний, підтверджений' in archive.read('ПЕРЕВІРИТИ.txt').decode()


def test_manual_selection_still_requires_correct_department_and_final_confirmation(client):
    payload = request_data(client)
    selected = payload['items'][0]['departments']
    selected['vet']['allow_standard_override'] = True
    selected['vet']['template_id'] = selected['chemistry']['template_id']
    assert client.post('/api/assignments/generate', json=payload).status_code == 422
    assert client.get('/api/assignments/numbering').json()['next_number'] is None


def test_assignment_library_upload_edit_delete_persists(client):
    template = client.get("/api/assignments/templates").json()["templates"][0]
    original = client.get("/api/assignments/templates/" + template["id"] + "/download").content
    result = client.post("/api/assignments/templates", files={"file": ("custom.docx", original)},
                         data={"name": "Мій шаблон", "aliases": '["Свинина"]'})
    assert result.status_code == 200
    entry = result.json()
    assert entry["department"] == template["department"]
    assert entry["standard"] == template["standard"]
    assert client.patch("/api/assignments/templates/" + entry["id"], json={"name": "Нова назва", "aliases": []}).status_code == 200
    assert client.delete("/api/assignments/templates/" + entry["id"]).status_code == 200
    assert len(client.get("/api/assignments/templates").json()["templates"]) == 60


def test_counter_and_archives_survive_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as first:
        payload = request_data(first)
        original = first.post("/api/assignments/generate", json=payload).content
    with TestClient(create_app(tmp_path)) as restarted:
        assert restarted.get("/api/assignments/numbering").json()["next_number"] == 1398
        assert restarted.post("/api/assignments/generate", json=payload).content == original


def test_issue_date_is_kyiv_date_not_utc(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 28, 22, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(assignment_api, "datetime", FixedDateTime)
    assert assignment_api.kyiv_today() == "2026-10-29"
