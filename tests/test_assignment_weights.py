"""Department weight defaults come from each selected Word form and stay editable."""
import io
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from app.assignments_documents import inspect_assignment_template, render_assignment
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as value:
        yield value


def herring_request(client):
    templates = client.get("/api/assignments/templates").json()["templates"]
    filenames = {"vet": "салака вет сан 3267.docx", "bacteriology": "салака бак 3267.docx",
                 "chemistry": "салака химия 3267.docx"}
    selected = {department: next(t for t in templates if t["filename"] == filename)
                for department, filename in filenames.items()}
    return {"job_id": "department-weight-defaults", "confirmed": True, "start_number": 1397,
            "items": [{"id": "herring-one", "product_name": selected["vet"]["product_name"],
                       "standard": selected["vet"]["standard"],
                       "departments": {department: {"template_id": template["id"]}
                                       for department, template in selected.items()}}]}


def generated_weights(response):
    assert response.status_code == 200, response.text
    with ZipFile(io.BytesIO(response.content)) as archive:
        return {inspect_assignment_template(archive.read(name))["department"]:
                inspect_assignment_template(archive.read(name))["fields"]["sample_weight"]
                for name in archive.namelist() if name.endswith(".docx")}


def test_all_sixty_weight_defaults_match_their_original_word_forms(client):
    templates = client.get("/api/assignments/templates").json()["templates"]
    assert len(templates) == 60
    for template in templates:
        original = client.get("/api/assignments/templates/" + template["id"] + "/download")
        assert template["sample_weight"] == inspect_assignment_template(original.content)["sample_weight"]
        assert template["sample_weight"]


def test_generation_defaults_to_different_weights_for_each_department(client):
    response = client.post("/api/assignments/generate", json=herring_request(client))
    assert generated_weights(response) == {"vet": "0,920 кг", "bacteriology": "0,690 кг", "chemistry": "1,150 кг"}


@pytest.mark.parametrize("edited,expected", [(" 2 кг (4 шт.) ", "2 кг (4 шт.)"), ("", "[НЕ ЗАПОВНЕНО]")])
def test_manual_department_weight_and_deliberate_empty_override_template(client, edited, expected):
    payload = herring_request(client)
    payload["items"][0]["departments"]["vet"]["sample_weight"] = edited
    response = client.post("/api/assignments/generate", json=payload)
    assert generated_weights(response) == {"vet": expected, "bacteriology": "0,690 кг", "chemistry": "1,150 кг"}


def test_common_weight_is_fallback_but_never_overwrites_explicit_department_edit(client):
    payload = herring_request(client)
    item = payload["items"][0]
    item["sample_weight"] = "3 кг (6 шт.)"
    item["departments"]["vet"]["sample_weight"] = "0,8 кг"
    item["departments"]["bacteriology"]["sample_weight"] = ""
    assert generated_weights(client.post("/api/assignments/generate", json=payload)) == {
        "vet": "0,8 кг", "bacteriology": "[НЕ ЗАПОВНЕНО]", "chemistry": "3 кг (6 шт.)"}


def test_upgrade_backfills_builtin_and_custom_weights_without_restoring_deleted_templates(tmp_path):
    with TestClient(create_app(tmp_path)) as first:
        entries = first.get("/api/assignments/templates").json()["templates"]
        original = first.get("/api/assignments/templates/" + entries[0]["id"] + "/download").content
        info = inspect_assignment_template(original)
        custom = render_assignment(original, {"sample_number": "123", "product_name": info["product_name"],
                                             "standard": info["standard"], "issue_date": "2026-10-28",
                                             "sample_weight": "9,5 кг (2 шт.)"})
        added = first.post("/api/assignments/templates", files={"file": ("Мій шаблон.docx", custom)},
                           data={"name": "Мій шаблон Ω", "aliases": '["моя назва"]'})
        assert added.status_code == 200, added.text
        assert added.json()["sample_weight"] == "9,5 кг (2 шт.)"
        custom_id = added.json()["id"]
        deleted_id = entries[0]["id"]
        assert first.delete("/api/assignments/templates/" + deleted_id).status_code == 200

    index = tmp_path / "assignment_templates/library.json"
    legacy = json.loads(index.read_text(encoding="utf-8"))
    for entry in legacy:
        entry.pop("sample_weight")
    index.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(tmp_path)) as restarted:
        entries = restarted.get("/api/assignments/templates").json()["templates"]
        assert len(entries) == 60
        assert not any(entry["id"] == deleted_id for entry in entries)
        custom_entry = next(entry for entry in entries if entry["id"] == custom_id)
        assert custom_entry["sample_weight"] == "9,5 кг (2 шт.)"
        assert custom_entry["name"] == "Мій шаблон Ω"
        assert custom_entry["aliases"] == ["моя назва"]
        assert all(entry["sample_weight"] for entry in entries)
        assert restarted.get("/api/assignments/numbering").json() == {"next_number": None}
