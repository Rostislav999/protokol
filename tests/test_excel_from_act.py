"""An act can populate the Excel review without producing any Word files."""
import base64
from io import BytesIO
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.protocol_excel import NS, cell_text, xml


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as value:
        yield value


def act_payload(**item_changes):
    item = {
        "id": "edited-position", "product_name": "Свинина тушкована 525 г (12012)",
        "protocol_number": "87-015-3000-1400-2", "protocol_date": "29 жовтня 2026 року",
        "catalog_number": "12012", "manufacturer": "ТОВ «Виробник», код ЄДРПОУ: 12345678",
        "manufacture_date": "20.10.2026", "batch_size": "1 000,5 кг",
        "sample_weight": "9 ж/б по 525 г (4,725 кг).", "template_id": None,
        "standard": "ДСТУ 4450:2005",
    }
    item.update(item_changes)
    return {
        "common": {"supplier": "ТОВ «Постачальник»", "act_number": "87-010-1-247", "act_date": "2026-10-20"},
        "items": [item], "source": "акт відбору.pdf",
    }


def preview(client, payload):
    response = client.post("/api/excel/from-act", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert isinstance(result["warnings"], list)
    assert len(result["records"]) == len(payload["items"])
    return result["records"]


def test_edited_act_fields_map_directly_without_word_requirements(client):
    saved = client.post("/api/settings/suppliers", json={
        "name": "ТОВ «Постачальник»", "code": "015", "edrpou": "00123456",
    })
    assert saved.status_code == 200, saved.text
    record = preview(client, act_payload())[0]
    assert record["id"]
    assert "акт відбору.pdf" in record["source"]
    assert record["values"] == {
        "D": "№87-015-3000-1400-2", "E": "29.10.2026", "F": "12012",
        "G": "Свинина тушкована", "H": "20.10.2026", "I": "20.10.2026", "J": "",
        "K": "1000.5", "L": "0.525", "M": "00123456", "N": "12345678",
    }
    assert isinstance(record["warnings"], list)
    assert isinstance(record["missing"], list)


@pytest.mark.parametrize("protocol_date", ["2026-10-29", "29.10.2026", "29 жовтня 2026 року"])
def test_protocol_date_accepts_the_current_form_formats(client, protocol_date):
    record = preview(client, act_payload(protocol_date=protocol_date))[0]
    assert record["values"]["E"] == "29.10.2026"


def test_act_number_and_date_never_become_missing_protocol_identity(client):
    record = preview(client, act_payload(protocol_number="", protocol_date=""))[0]
    assert record["values"]["D"] == record["values"]["E"] == ""
    assert "Номер протоколу" in record["missing"]
    assert "Дата протоколу" in record["missing"]
    response = client.post("/api/excel/generate", data={
        "payload": json.dumps({"records": [record], "confirmed": True}),
    })
    assert response.status_code == 422


def test_expiry_is_separate_and_sample_total_does_not_become_package_net_mass(client):
    record = preview(client, act_payload(
        manufacture_date="Вжити до 25.07.2028", sample_weight="4,725 кг.",
        batch_size="125000 г", catalog_number="00123", product_name="Каша гречана",
    ))[0]
    cells = record["values"]
    assert cells["H"] == cells["I"] == cells["L"] == ""
    assert cells["J"] == "25.07.2028"
    assert cells["K"] == "125"
    assert cells["F"] == "00123"


def test_invalid_date_is_flagged_without_losing_other_rows(client):
    payload = act_payload(protocol_date="31.02.2026", manufacture_date="не прочитано")
    payload["items"].append({**payload["items"][0], "id": "second", "protocol_number": "87-015-3000-1401-2", "protocol_date": "2026-10-30"})
    first, second = preview(client, payload)
    assert first["values"]["E"] == first["values"]["I"] == ""
    assert first["warnings"]
    assert first["values"]["G"] == "Свинина тушкована"
    assert second["values"]["E"] == "30.10.2026"


def test_explicit_supplier_code_wins_and_ambiguous_manufacturer_remains_blank(client):
    client.post("/api/settings/suppliers", json={"name": "ТОВ «Постачальник»", "code": "015", "edrpou": "00123456"})
    payload = act_payload(manufacturer="Виробники: 11111111 та 22222222")
    payload["common"]["supplier"] = "ТОВ «Постачальник», код ЄДРПОУ: 87654321"
    record = preview(client, payload)[0]
    assert record["values"]["M"] == "87654321"
    assert record["values"]["N"] == ""
    assert record["warnings"]


def test_direct_act_preview_produces_xlsx_after_review_with_no_compliance_columns(client):
    payload = act_payload()
    payload["common"]["supplier"] = "ТОВ «Тест», код ЄДРПОУ: 00001234"
    records = preview(client, payload)
    unreviewed = client.post("/api/excel/generate", data={"payload": json.dumps({"records": records})})
    assert unreviewed.status_code == 422
    response = client.post("/api/excel/generate", data={
        "payload": json.dumps({"records": records, "confirmed": True}),
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["counts"] == {"added": 1, "updated": 0, "skipped": 0}
    with ZipFile(BytesIO(base64.b64decode(result["workbook_base64"]))) as archive:
        sheet = xml(archive.read("xl/worksheets/sheet1.xml"))
        assert cell_text(sheet.find(".//s:c[@r='D2']", NS), []) == "№87-015-3000-1400-2"
        assert cell_text(sheet.find(".//s:c[@r='G2']", NS), []) == "Свинина тушкована"
        assert cell_text(sheet.find(".//s:c[@r='L2']", NS), []) == "0.525"
        assert cell_text(sheet.find(".//s:c[@r='M2']", NS), []) == "00001234"
        assert sheet.find(".//s:c[@r='M2']", NS).get("t") == "inlineStr"
        assert sheet.find(".//s:c[@r='E2']", NS).get("t") == "n"
        assert sheet.find(".//s:c[@r='O1']", NS) is None
        assert sheet.find(".//s:c[@r='P1']", NS) is None


@pytest.mark.parametrize("count", [0, 101])
def test_act_preview_has_the_same_record_limit_as_excel_export(client, count):
    payload = act_payload()
    payload["items"] = payload["items"] * count
    assert client.post("/api/excel/from-act", json=payload).status_code == 422


def test_empty_source_and_optional_sample_weight_can_still_be_reviewed(client):
    payload = act_payload(sample_weight=None)
    payload["source"] = ""
    record = preview(client, payload)[0]
    assert record["source"].strip()
    assert record["values"]["L"] == ""
