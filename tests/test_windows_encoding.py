"""Exercise real libraries and Word generation with Windows' CP1251 default."""
import builtins
import io
import json
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from app.assignments_documents import inspect_assignment_template
from app.documents import inspect_template


def test_cp1251_default_supports_startup_restart_and_unicode_documents(tmp_path, monkeypatch):
    original_open = io.open
    original_text_encoding = io.text_encoding

    def legacy_text_encoding(encoding, stacklevel=2):
        # Path.read_text resolves None before calling io.open. Under
        # PYTHONUTF8=1 it becomes "utf-8", bypassing our legacy-default mock.
        # Simulate the locale default while retaining all explicit encodings.
        return "locale" if encoding is None else original_text_encoding(encoding, stacklevel)

    def cp1251_open(file, mode="r", buffering=-1, encoding=None, errors=None,
                    newline=None, closefd=True, opener=None):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "cp1251"
        return original_open(file, mode, buffering, encoding, errors, newline, closefd, opener)

    monkeypatch.setattr(io, "open", cp1251_open)
    monkeypatch.setattr(builtins, "open", cp1251_open)
    monkeypatch.setattr(io, "text_encoding", legacy_text_encoding)
    root = Path(__file__).resolve().parents[1]
    # Prove the simulated default reproduces the user's actual decoding failure.
    with pytest.raises(UnicodeDecodeError):
        (root / "assets/library.json").read_text()

    directory = tmp_path / "Бібліотека та номери"
    monkeypatch.setenv("PROTOKOL_DATA_DIR", str(directory))
    from app.main import create_app
    import app.assignments_api as assignments_api

    monkeypatch.setattr(assignments_api, "kyiv_today", lambda: "2026-10-28")
    protocol_name = "Моя свинина — шаблон Ω"
    assignment_name = "Моє завдання — шаблон Ω"
    with TestClient(create_app(directory)) as first:
        assert len(first.get("/api/templates").json()["templates"]) == 22
        assignments = first.get("/api/assignments/templates").json()["templates"]
        assert len(assignments) == 60
        filenames = {"vet": "все.docx", "bacteriology": "бак.docx", "chemistry": "хім токс.docx"}
        selections = {
            department: {"template_id": next(t["id"] for t in assignments if t["filename"] == filename),
                         "sample_weight": "1,575 кг (3 шт.)"}
            for department, filename in filenames.items()
        }
        assignment_id = selections["vet"]["template_id"]
        edited = first.patch("/api/templates/sample-1053", json={
            "name": protocol_name, "aliases": ["Свинина Ω"], "category": "М’ясні консерви",
        })
        assert edited.status_code == 200, edited.text
        edited = first.patch("/api/assignments/templates/" + assignment_id, json={
            "name": assignment_name, "aliases": ["Свинина Ω"],
        })
        assert edited.status_code == 200, edited.text

    # This re-reads both seed ledgers and the UTF-8 indexes written on first run.
    with TestClient(create_app(directory)) as restarted:
        protocols = restarted.get("/api/templates").json()["templates"]
        assert len(protocols) == 22
        assert next(t for t in protocols if t["id"] == "sample-1053")["name"] == protocol_name
        assignments = restarted.get("/api/assignments/templates").json()["templates"]
        assert len(assignments) == 60
        assert next(t for t in assignments if t["id"] == assignment_id)["name"] == assignment_name
        assert restarted.get("/").status_code == 200

        customer = "ТОВ «Дослідження Ω»"
        protocol = restarted.post("/api/generate", json={
            "confirmed": True,
            "common": {"customer": customer, "sampling_description": "Україна, відібрано замовником",
                       "supplier": "Постачальник тест", "contract": "Договір тест"},
            "items": [{"id": "один", "product_name": "Свинина тушкована", "protocol_number": "УКР-001",
                       "protocol_date": "2026-10-28", "template_id": "sample-1053"}],
        })
        assert protocol.status_code == 200, protocol.text
        with ZipFile(io.BytesIO(protocol.content)) as archive:
            document = next(name for name in archive.namelist() if name.endswith(".docx"))
            assert "УКР-001 Свинина тушкована" in document
            assert archive.getinfo(document).flag_bits & 0x800
            assert inspect_template(archive.read(document))["fields"]["customer"] == customer
            assert protocol_name in archive.read("ПЕРЕВІРИТИ.txt").decode("utf-8")

        payload = {
            "job_id": "windows-cp1251", "confirmed": True, "start_number": 1397,
            "items": [{"id": "зразок-один", "product_name": "Свинина тушкована",
                       "standard": "ДСТУ 4450:2005", "departments": selections}],
        }
        assignment = restarted.post("/api/assignments/generate", json=payload)
        assert assignment.status_code == 200, assignment.text
        with ZipFile(io.BytesIO(assignment.content)) as archive:
            documents = [name for name in archive.namelist() if name.endswith(".docx")]
            assert len(documents) == 3
            assert all("Свинина тушкована" in name for name in documents)
            for name in documents:
                assert archive.getinfo(name).flag_bits & 0x800
                fields = inspect_assignment_template(archive.read(name))["fields"]
                assert fields["sample_number"] == "1397"
                assert fields["product_name"] == "Свинина тушкована"
                assert fields["sample_weight"] == "1,575 кг (3 шт.)"
                assert fields["issue_date"] == "28.10.2026"
            metadata = json.loads(archive.read("НУМЕРАЦІЯ.json"))
            assert metadata["samples"][0]["id"] == "зразок-один"
            assert "ДСТУ 4450:2005" in archive.read("ПЕРЕВІРИТИ.txt").decode("utf-8")

    with TestClient(create_app(directory)) as final_restart:
        assert final_restart.get("/api/assignments/numbering").json() == {"next_number": 1398}
        repeated = final_restart.post("/api/assignments/generate", json=payload)
        assert repeated.status_code == 200
        assert repeated.content == assignment.content
