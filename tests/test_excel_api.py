import base64
from io import BytesIO
import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.protocol_excel import FIELDS, NS, S, cell_text, xml
from app.supplier_details import supplier_with_code


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as value:
        yield value


def generated_protocols(client):
    result = client.post("/api/generate", json={
        "confirmed": True,
        "common": {"customer": "ТОВ «Замовник»", "supplier": "ТОВ «Постачальник»",
                   "sampling_description": "м. Самар, представниками військової частини А0873"},
        "items": [{"id": "one", "protocol_number": "87-015-3000-1400-2", "protocol_date": "2026-10-29",
                   "product_name": "Свинина тушкована", "catalog_number": "12012", "template_id": "sample-1053",
                   "manufacturer": "ТОВ «Виробник», код ЄДРПОУ: 12345678", "manufacture_date": "20.10.2026",
                   "batch_size": "1000,5 кг", "sample_weight": "9 ж/б по 525 г (4,725 кг)."}]})
    assert result.status_code == 200, result.text
    return result.content


def import_record(client):
    response = client.post("/api/excel/import", files={"files": ("Протоколи.zip", generated_protocols(client))})
    assert response.status_code == 200, response.text
    return response.json()["records"][0]


def test_original_excel_fields_stop_at_n_and_docx_zip_import_keeps_supplier_name_and_code(client):
    supplier = client.post("/api/settings/suppliers", json={"name": "ТОВ «Постачальник»", "code": "015", "edrpou": "00123456"})
    assert supplier.status_code == 200
    fields = client.get("/api/excel/fields").json()["fields"]
    assert [field["column"] for field in fields] == [column for column, _ in FIELDS]
    assert [field["column"] for field in fields] == list("DEFGHIJKLMN")
    record = import_record(client)
    assert set(record["values"]) == set("DEFGHIJKLMN")
    assert record["values"]["D"] == "№87-015-3000-1400-2"
    assert record["values"]["E"] == "29.10.2026"
    assert record["values"]["M"] == "ТОВ «Постачальник», код ЄДРПОУ: 00123456"
    assert record["values"]["N"] == "12345678"
    assert record["values"]["L"] == "0.525"
    assert record["values"]["K"] == "1000.5"
    assert record["values"]["H"] == record["values"]["I"] == "20.10.2026"


def test_excel_generate_download_is_valid_xlsx_with_dates_numbers_and_no_compliance(client):
    record = import_record(client)
    payload = {"confirmed": True, "records": [record], "update_existing": True}
    result = client.post("/api/excel/generate", data={"payload": json.dumps(payload, ensure_ascii=False)})
    assert result.status_code == 200, result.text
    response = result.json()
    assert response["counts"] == {"added": 1, "updated": 0, "skipped": 0}
    assert response["filename"].endswith(".xlsx")
    with ZipFile(BytesIO(base64.b64decode(response["workbook_base64"]))) as archive:
        sheet = xml(archive.read("xl/worksheets/sheet1.xml"))
        assert sheet.find(".//s:c[@r='N1']", NS) is not None
        assert sheet.find(".//s:c[@r='O1']", NS) is None
        assert sheet.find(".//s:c[@r='P1']", NS) is None
        assert cell_text(sheet.find(".//s:c[@r='L2']", NS), []) == "0.525"
        assert sheet.find(".//s:c[@r='E2']", NS).get("t") == "n"
        assert sheet.find(".//s:c[@r='M2']", NS).get("t") == "inlineStr"


def test_existing_excel_updates_without_touching_last_two_columns(client):
    record = import_record(client)
    first = client.post("/api/excel/generate", data={"payload": json.dumps({"confirmed": True, "records": [record]})}).json()
    original = base64.b64decode(first["workbook_base64"])
    output = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(output, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                row = root.find(".//{" + S + "}row[@r='2']")
                for column, value in (("O", "Не змінювати відповідність"), ("P", "Не змінювати Каталог")):
                    cell = ET.SubElement(row, "{" + S + "}c", {"r": column + "2", "t": "inlineStr"})
                    ET.SubElement(ET.SubElement(cell, "{" + S + "}is"), "{" + S + "}t").text = value
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, data)
    record["values"]["G"] = "Уточнена назва"
    response = client.post("/api/excel/generate", data={"payload": json.dumps({"confirmed": True, "records": [record]})},
                           files={"template": ("Таблиця.xlsx", output.getvalue())})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["counts"]["updated"] == 1
    with ZipFile(BytesIO(base64.b64decode(result["workbook_base64"]))) as archive:
        sheet = xml(archive.read("xl/worksheets/sheet1.xml"))
        assert cell_text(sheet.find(".//s:c[@r='G2']", NS), []) == "Уточнена назва"
        assert cell_text(sheet.find(".//s:c[@r='O2']", NS), []) == "Не змінювати відповідність"
        assert cell_text(sheet.find(".//s:c[@r='P2']", NS), []) == "Не змінювати Каталог"


@pytest.mark.parametrize("filename,data", [("bad.exe", b"x"), ("empty.docx", b""), ("bad.zip", b"not zip"), ("bad.docx", b"not docx"), ("bad.pdf", b"not pdf")])
def test_invalid_import_has_actionable_error(client, filename, data):
    assert client.post("/api/excel/import", files={"files": (filename, data)}).status_code == 422


def test_review_required_and_unknown_compliance_columns_rejected(client):
    record = import_record(client)
    payload = {"records": [record], "confirmed": False}
    assert client.post("/api/excel/generate", data={"payload": json.dumps(payload)}).status_code == 422
    payload["confirmed"] = True
    record["values"]["O"] = "Відповідає"
    assert client.post("/api/excel/generate", data={"payload": json.dumps(payload)}).status_code == 422


def test_supplier_directory_enrichment_never_borrows_another_company_code():
    suppliers = [{"name": "ТОВ «Перший»", "edrpou": "00123456"}, {"name": "ТОВ «Другий»", "edrpou": "87654321"}]
    assert supplier_with_code("ТОВ «Перший»", suppliers) == "ТОВ «Перший», код ЄДРПОУ: 00123456"
    assert supplier_with_code("ТОВ «Невідомий»", suppliers) == "ТОВ «Невідомий»"
    assert supplier_with_code("код ЄДРПОУ: 00123456", suppliers) == "ТОВ «Перший», код ЄДРПОУ: 00123456"
    assert supplier_with_code("ТОВ «Перший», код ЄДРПОУ: 11111111", suppliers).endswith("11111111")
