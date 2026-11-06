import io
from xml.etree import ElementTree
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.documents import inspect_template
from app.protocol_text import SAMPLING_ENDING
import app.assignments_api as assignment_api


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as value:
        yield value


def assignment_payload(client):
    templates = client.get("/api/assignments/templates").json()["templates"]
    selected = {department: next(t for t in templates if t["filename"] == filename)
                for department, filename in {"vet": "все.docx", "bacteriology": "бак.docx", "chemistry": "хім токс.docx"}.items()}
    return {"job_id": "issuer-and-print", "confirmed": True, "start_number": 1400,
            "items": [{"id": "sample-one", "product_name": "Свинина тушкована", "standard": "ДСТУ 4450:2005",
                       "departments": {department: {"template_id": template["id"]} for department, template in selected.items()}}]}


def test_supplier_codes_and_default_issuer_persist_across_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as first:
        supplier = first.post("/api/settings/suppliers", json={"name": "ТОВ «Постачальник Ω»", "code": "015"}).json()
        issuer = first.post("/api/settings/issuers", json={"name": "О. ПЕТРЕНКО", "position": "старший лейтенант"}).json()
        assert first.patch("/api/settings", json={"default_issuer_id": issuer["id"]}).status_code == 200
    with TestClient(create_app(tmp_path)) as restarted:
        data = restarted.get("/api/settings").json()
        assert data["suppliers"] == [supplier]
        assert supplier["code"] == "015"
        assert data["issuers"] == [issuer]
        assert data["default_issuer_id"] == issuer["id"]
        assert restarted.patch("/api/settings/suppliers/" + supplier["id"], json={"name": supplier["name"], "code": "010"}).status_code == 200
        assert restarted.delete("/api/settings/issuers/" + issuer["id"]).status_code == 200
        assert restarted.get("/api/settings").json()["default_issuer_id"] == ""


@pytest.mark.parametrize("code", ["ABC", "15-1", "", "12345678901"])
def test_supplier_code_validation(client, code):
    assert client.post("/api/settings/suppliers", json={"name": "Постачальник", "code": code}).status_code == 422


def test_supplier_directory_prevents_duplicate_codes_and_names(client):
    assert client.post("/api/settings/suppliers", json={"name": "ТОВ Тест", "code": "015"}).status_code == 200
    assert client.post("/api/settings/suppliers", json={"name": "Інший", "code": "015"}).status_code == 422
    assert client.post("/api/settings/suppliers", json={"name": " тов   тест ", "code": "010"}).status_code == 422
    assert client.patch("/api/settings", json={"default_issuer_id": "missing"}).status_code == 422


def test_protocol_generation_removes_duplicate_catalog_and_formats_sampling(client):
    payload = {"confirmed": True, "common": {
        "customer": "ТОВ Тест", "sampling_description": "м. Київ. Представники військової частини А3466: капітан О. ДАНИЛОВ та старший лейтенант Д. МЕЛЬНИЧУК, в присутності представників ТОВ «Тест»."},
        "items": [{"id": "one", "product_name": "Свинина тушкована 525 г (12012)", "catalog_number": "12012",
                   "protocol_number": "87-015-3000-1305-2", "protocol_date": "2026-10-29", "template_id": "sample-1053"}]}
    result = client.post("/api/generate", json=payload)
    assert result.status_code == 200, result.text
    with ZipFile(io.BytesIO(result.content)) as archive:
        document = next(name for name in archive.namelist() if name.endswith(".docx"))
        fields = inspect_template(archive.read(document))["fields"]
        assert fields["product_name"] == "Свинина тушкована"
        assert fields["catalog_number"] == "12012"
        assert "представниками військової частини А3466" in fields["sampling_description"]
        assert "капітаном О. ДАНИЛОВ" in fields["sampling_description"]
        assert fields["sampling_description"].endswith(SAMPLING_ENDING)
    for incomplete in ("87-015-3000--2", "87--3000-1305-2", "87-015--1305-2"):
        payload["items"][0]["protocol_number"] = incomplete
        assert client.post("/api/generate", json=payload).status_code == 422


def test_selected_issuer_is_rendered_and_cached_even_after_deletion(client):
    issuer = client.post("/api/settings/issuers", json={"name": "О. ПЕТРЕНКО", "position": "старший лейтенант"}).json()
    payload = {**assignment_payload(client), "issuer_id": issuer["id"]}
    result = client.post("/api/assignments/generate", json=payload)
    assert result.status_code == 200, result.text
    with ZipFile(io.BytesIO(result.content)) as archive:
        for name in archive.namelist():
            if name.endswith(".docx"):
                with ZipFile(io.BytesIO(archive.read(name))) as document:
                    text = " ".join(ElementTree.fromstring(document.read("word/document.xml")).itertext())
                    assert "О. ПЕТРЕНКО" in text
                    assert "старший лейтенант" in text
    assert client.delete("/api/settings/issuers/" + issuer["id"]).status_code == 200
    assert client.post("/api/assignments/generate", json=payload).content == result.content
    payload["job_id"] = "invalid-issuer-new-generation"
    assert client.post("/api/assignments/generate", json=payload).status_code == 422


def test_print_uses_exact_cached_documents_without_consuming_numbers(client, monkeypatch):
    printed = []
    def print_documents(archive, copies, printer, duplex):
        printed.append((archive, copies, printer, duplex))
        return {"documents": 3, "copies": copies, "total_copies": 6, "printer": printer}
    monkeypatch.setattr(assignment_api, "print_assignments", print_documents)
    payload = assignment_payload(client)
    print_request = {"generation": payload, "printer": "Test printer", "copies": 2, "confirmed": True}
    assert client.post("/api/assignments/print", json=print_request).status_code == 422
    assert not printed
    generated = client.post("/api/assignments/generate", json=payload)
    assert generated.status_code == 200, generated.text
    result = client.post("/api/assignments/print", json=print_request)
    assert result.status_code == 200, result.text
    assert result.json()["total_copies"] == 6
    assert printed == [(generated.content, 2, 'Test printer', 'simplex')]
    assert client.get("/api/assignments/numbering").json()["next_number"] == 1401
    assert client.post("/api/assignments/print", json={**print_request, "confirmed": False}).status_code == 422
    changed = {**print_request, "generation": {**payload, "start_number": 1500}}
    assert client.post("/api/assignments/print", json=changed).status_code == 422
    assert len(printed) == 1
    alternate = client.post('/api/assignments/print', json={**print_request, 'printer': 'Other printer', 'duplex': 'long_edge'})
    assert alternate.status_code == 200
    assert printed[-1] == (generated.content, 2, 'Other printer', 'long_edge')
    assert client.get('/api/assignments/numbering').json()['next_number'] == 1401
    for printer in ('', ' ', 'bad\nprinter', 'x' * 1025):
        assert client.post('/api/assignments/print', json={**print_request, 'printer': printer}).status_code == 422
    for mode in ('manual', '', None):
        assert client.post('/api/assignments/print', json={**print_request, 'duplex': mode}).status_code == 422
    assert len(printed) == 2
