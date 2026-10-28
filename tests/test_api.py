import io
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.documents import inspect_template


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as value:
        yield value


def generation():
    return {
        "common": {"customer": "Замовник тест", "sampling_description": "Адреса тест, представник тест",
                   "supplier": "Постачальник тест", "contract": "Договір тест"},
        "items": [{"id": "one", "product_name": "Свинина тушкована", "protocol_number": "НОВИЙ-001",
                   "protocol_date": "2026-10-28", "template_id": "sample-1053"}],
        "confirmed": True,
    }


def test_seed_library_and_original_download(client):
    result = client.get("/api/templates").json()
    assert len(result["templates"]) == 22
    doc = client.get("/api/templates/sample-1053/download")
    assert doc.status_code == 200
    assert "Свинина" in inspect_template(doc.content)["product_name"]
    assert client.get("/api/templates/../../etc/passwd/download").status_code == 404


def test_parse_and_match_do_not_confirm_templates(client):
    result = client.post("/api/parse", json={"text": "Замовник: ТОВ Тест\n1. Свинина тушкована"})
    assert result.status_code == 200
    assert result.json()["items"][0]["template_id"] is None
    matches = client.post("/api/match", json={"product_name": "Свинина тушкована"}).json()["matches"]
    assert matches[0]["id"] == "sample-1053"
    assert "sample-1100" not in [entry["id"] for entry in matches]


def test_generate_real_word_per_position_and_no_historical_values(client):
    payload = generation()
    payload["items"].append({**payload["items"][0], "protocol_number": "НОВИЙ-002", "product_name": "Кілька",
                             "template_id": "sample-1232"})
    result = client.post("/api/generate", json=payload)
    assert result.status_code == 200
    assert result.headers["content-type"] == "application/zip"
    with ZipFile(io.BytesIO(result.content)) as archive:
        files = [name for name in archive.namelist() if name.endswith(".docx")]
        assert len(files) == 2
        first = inspect_template(archive.read(files[0]))
        assert first["fields"]["customer"] == "Замовник тест"
        assert first["fields"]["protocol_number"] == "НОВИЙ-001"
        assert first["fields"]["manufacturer"] == "[НЕ ЗАПОВНЕНО]"
        second = inspect_template(archive.read(files[1]))
        assert second["product_name"] == "Кілька"
        assert "старими результатами" in archive.read("ПЕРЕВІРИТИ.txt").decode()


def test_generate_requires_review_fields_unique_numbers_and_existing_template(client):
    data = generation()
    data["confirmed"] = False
    assert client.post("/api/generate", json=data).status_code == 422
    data["confirmed"] = True
    data["items"][0]["template_id"] = "../../etc/passwd"
    assert client.post("/api/generate", json=data).status_code == 422
    data = generation()
    data["items"].append(data["items"][0])
    assert client.post("/api/generate", json=data).status_code == 422
    data = generation()
    data["items"][0]["protocol_date"] = ""
    assert client.post("/api/generate", json=data).status_code == 422


def test_library_upload_edit_delete_persist_and_reject_invalid_docx(client):
    original = client.get("/api/templates/sample-1232/download").content
    response = client.post("/api/templates", files={"file": ("кілька.docx", original)},
                           data={"name": "Моя кілька", "aliases": '["кілька тест"]', "category": "Рибні"})
    assert response.status_code == 200
    template = response.json()
    assert len(client.get("/api/templates").json()["templates"]) == 23
    assert client.patch("/api/templates/" + template["id"], json={"name": "Змінена кілька", "aliases": ["риба"],
                                                              "category": "Рибні"}).status_code == 200
    assert client.delete("/api/templates/" + template["id"]).status_code == 200
    assert len(client.get("/api/templates").json()["templates"]) == 22
    assert client.get("/api/templates/" + template["id"] + "/download").status_code == 404
    assert client.post("/api/templates", files={"file": ("bad.docx", b"not a docx")}).status_code == 422


def test_upload_validation_and_text_extraction(client):
    assert client.post("/api/extract", files={"file": ("empty.pdf", b"")}).status_code == 422
    assert client.post("/api/extract", files={"file": ("bad.pdf", b"invalid")}).status_code == 422
    assert client.post("/api/extract", files={"file": ("script.exe", b"invalid")}).status_code == 422
    result = client.post("/api/extract", files={"file": ("act.txt", "Замовник: Тест\n1. Кілька".encode())})
    assert result.status_code == 200
    assert result.json()["method"] == "text"
    assert result.json()["items"][0]["matches"][0]["id"] == "sample-1232"


def test_static_assets(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_protocol_numbers_are_normalized_before_validating_uniqueness(client):
    data = generation()
    data["items"][0]["protocol_number"] = "№ 123"
    data["items"].append({**data["items"][0], "protocol_number": "123"})
    assert client.post("/api/generate", json=data).status_code == 422
    data = generation()
    data["items"][0]["protocol_number"] = "№ "
    assert client.post("/api/generate", json=data).status_code == 422


def template_bytes(keys):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        body = "".join("<w:p><w:r><w:t>{{" + key + "}}</w:t></w:r></w:p>" for key in keys)
        body += "<w:p><w:r><w:t>Вищезазначена інформація надана замовником.</w:t></w:r></w:p>"
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
    return output.getvalue()


def test_custom_templates_do_not_silently_drop_required_or_supplied_fields(client):
    minimal = template_bytes(["protocol_number", "product_name"])
    result = client.post("/api/templates", files={"file": ("incomplete.docx", minimal)})
    assert result.status_code == 422
    required = ["protocol_number", "product_name", "protocol_date", "customer", "sampling_description"]
    doc = template_bytes(required)
    added = client.post("/api/templates", files={"file": ("partial.docx", doc)}, data={"name": "Тест"})
    assert added.status_code == 200
    data = generation()
    data["items"][0]["template_id"] = added.json()["id"]
    result = client.post("/api/generate", json=data)
    assert result.status_code == 422
    assert "Постачальник" in result.json()["detail"]


def test_library_edits_and_deletions_survive_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as first:
        first.patch("/api/templates/sample-1053", json={"name": "Моя свинина", "aliases": ["Свинина"], "category": "М’ясо"})
        first.delete("/api/templates/sample-1232")
    with TestClient(create_app(tmp_path)) as restarted:
        templates = restarted.get("/api/templates").json()["templates"]
        assert len(templates) == 21
        assert next(t for t in templates if t["id"] == "sample-1053")["name"] == "Моя свинина"
        assert not any(t["id"] == "sample-1232" for t in templates)


def test_library_upgrade_adds_new_samples_without_restoring_deleted_old_ones(tmp_path):
    import json
    from app.library import Library
    directory = tmp_path / "templates"
    library = Library(directory)
    old = [e for e in library.all() if e.get("seed_version", 1) == 1 and e["id"] != "sample-1232"]
    old[0]["name"] = "Налаштований користувачем шаблон"
    (directory / "library.json").write_text(json.dumps(old))
    (directory / "seeded.json").unlink()
    upgraded = Library(directory).all()
    assert len(upgraded) == 21
    assert any(t["id"] == "sample-814" for t in upgraded)
    assert not any(t["id"] == "sample-1232" for t in upgraded)
    assert next(t for t in upgraded if t["id"] == old[0]["id"])["name"] == "Налаштований користувачем шаблон"
