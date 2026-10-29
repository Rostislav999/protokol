import io
import json
import re
from xml.etree import ElementTree
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.documents import inspect_template
from app.main import create_app


def xml_text(data):
    return " ".join(ElementTree.fromstring(data).itertext())


def payload():
    return {"confirmed": True, "common": {
        "customer": "ТОВ Тест", "sampling_description": "м. Самар, представниками військової частини А0873",
        "act_number": "87-010-1-247", "act_date": "2026-10-20", "seal_number": "СПІЛЬНА-0001"},
        "items": [{"id": "one", "product_name": "Свинина тушкована", "protocol_number": "87-010-3000-1400-2",
                   "protocol_date": "29.10.2026", "template_id": "sample-1053", "seal_number": "№1 00004850; №2 00004849"},
                  {"id": "two", "product_name": "Свинина тушкована", "protocol_number": "87-010-3000-1401-2",
                   "protocol_date": "2026-10-29", "template_id": "sample-1053"}]}


def test_api_fills_existing_act_and_seal_rows_and_all_footer_numbers(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        original = client.get("/api/templates/sample-1053/download").content
        response = client.post("/api/generate", json=payload())
        assert response.status_code == 200, response.text
        with ZipFile(io.BytesIO(original)) as source, ZipFile(io.BytesIO(response.content)) as bundle:
            original_xml = source.read("word/document.xml")
            original_paragraphs = len(ElementTree.fromstring(original_xml).findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"))
            documents = [name for name in bundle.namelist() if name.endswith(".docx")]
            assert len(documents) == 2
            for index, name in enumerate(documents):
                data = bundle.read(name)
                fields = inspect_template(data)["fields"]
                number = f"87-010-3000-{1400 + index}-2"
                assert fields["protocol_number"] == number
                assert fields["protocol_date"] == "29 жовтня 2026 року"
                assert fields["act_number"] == "87-010-1-247"
                assert fields["act_date"] == "20.10.2026"
                assert fields["seal_number"] == ("№1 00004850; №2 00004849" if index == 0 else "СПІЛЬНА-0001")
                with ZipFile(io.BytesIO(data)) as document:
                    body = document.read("word/document.xml")
                    assert len(ElementTree.fromstring(body).findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")) == original_paragraphs
                    footers = [part for part in document.namelist() if re.fullmatch(r"word/footer\d+\.xml", part)]
                    assert len(footers) >= 2
                    for part in footers:
                        text = xml_text(document.read(part))
                        assert number in text
                        assert "87-010-0555-0555-2" not in text


def test_existing_library_upgrade_detects_act_and_seal_rows_without_resetting_edits(tmp_path):
    with TestClient(create_app(tmp_path)) as first:
        assert first.patch("/api/templates/sample-1053", json={"name": "Моя свинина Ω", "aliases": ["Мій шаблон"], "category": "М’ясні"}).status_code == 200
        assert first.delete("/api/templates/sample-1232").status_code == 200
    index = tmp_path / "templates/library.json"
    entries = json.loads(index.read_text(encoding="utf-8"))
    for entry in entries:
        entry.pop("protocol_fields_version", None)
        entry["available_fields"] = [key for key in entry["available_fields"] if key not in {"act_number", "act_date", "seal_number"}]
    index.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(tmp_path)) as upgraded:
        templates = upgraded.get("/api/templates").json()["templates"]
        assert len(templates) == 21
        assert not any(entry["id"] == "sample-1232" for entry in templates)
        selected = next(entry for entry in templates if entry["id"] == "sample-1053")
        assert selected["name"] == "Моя свинина Ω"
        assert selected["aliases"] == ["Мій шаблон"]
        assert {"act_number", "act_date", "seal_number"} <= set(selected["available_fields"])
        assert upgraded.post("/api/generate", json=payload()).status_code == 200
