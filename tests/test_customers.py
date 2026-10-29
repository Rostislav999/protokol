"""Customer directory changes must preserve existing supplier and issuer settings."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.settings import SettingsStore, settings_router


def client_for(directory):
    app = FastAPI()
    app.include_router(settings_router(SettingsStore(directory)))
    return TestClient(app)


def test_customer_create_edit_select_data_and_delete_survive_restart(tmp_path):
    with client_for(tmp_path) as first:
        response = first.post("/api/settings/customers", json={"name": " ТОВ «Замовник Ω» "})
        assert response.status_code == 200, response.text
        customer = response.json()
        assert customer["name"] == "ТОВ «Замовник Ω»"
        assert first.get("/api/settings").json()["customers"] == [customer]
        edited = first.patch("/api/settings/customers/" + customer["id"], json={"name": "Військова частина А3466"})
        assert edited.status_code == 200
    with client_for(tmp_path) as restarted:
        assert restarted.get("/api/settings").json()["customers"] == [edited.json()]
        assert restarted.delete("/api/settings/customers/" + customer["id"]).json() == {"deleted": True}
    with client_for(tmp_path) as final:
        assert final.get("/api/settings").json()["customers"] == []


def test_legacy_settings_upgrade_preserves_suppliers_issuers_and_default(tmp_path):
    supplier = {"id": "supplier-one", "name": "ТОВ «Постачальник»", "code": "015"}
    issuer = {"id": "issuer-one", "name": "О. ПРИКЛАД", "position": "капітан"}
    original = {"suppliers": [supplier], "issuers": [issuer], "default_issuer_id": issuer["id"]}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    with client_for(tmp_path) as client:
        data = client.get("/api/settings").json()
        assert data == {**original, "customers": []}
        assert json.loads(path.read_text(encoding="utf-8")) == original
        added = client.post("/api/settings/customers", json={"name": "ТОВ «Новий замовник»"})
        assert added.status_code == 200
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored == {**original, "customers": [added.json()]}
        assert client.delete("/api/settings/customers/" + added.json()["id"]).status_code == 200
        assert client.get("/api/settings").json() == {**original, "customers": []}


@pytest.mark.parametrize("name", ["", "   ", "Ім’я\nІнше", "Ім’я\x00", "А" * 501])
def test_customer_name_validation(tmp_path, name):
    with client_for(tmp_path) as client:
        assert client.post("/api/settings/customers", json={"name": name}).status_code == 422
        assert client.get("/api/settings").json()["customers"] == []


def test_customer_directory_rejects_duplicate_and_unknown_entries(tmp_path):
    with client_for(tmp_path) as client:
        customer = client.post("/api/settings/customers", json={"name": "ТОВ Тест"}).json()
        assert client.post("/api/settings/customers", json={"name": " тов   тест "}).status_code == 422
        assert client.patch("/api/settings/customers/missing", json={"name": "Невідомий"}).status_code == 404
        assert client.delete("/api/settings/customers/missing").status_code == 404
        assert client.get("/api/settings").json()["customers"] == [customer]


def test_supplier_edrpou_keeps_leading_zeros_and_survives_edit_and_restart(tmp_path):
    with client_for(tmp_path) as client:
        response = client.post("/api/settings/suppliers", json={
            "name": "ТОВ «Постачальник Ω»", "code": "015", "edrpou": "00001234"})
        assert response.status_code == 200, response.text
        supplier = response.json()
        assert supplier["edrpou"] == "00001234"
        assert client.patch("/api/settings/suppliers/" + supplier["id"], json={
            "name": supplier["name"], "code": "010"}).status_code == 200
    with client_for(tmp_path) as restarted:
        stored = restarted.get("/api/settings").json()["suppliers"][0]
        assert stored["code"] == "010"
        assert stored["edrpou"] == "00001234"
        edited = restarted.patch("/api/settings/suppliers/" + supplier["id"], json={
            "name": supplier["name"], "code": "010", "edrpou": ""})
        assert edited.status_code == 200
        assert edited.json()["edrpou"] == ""


@pytest.mark.parametrize("edrpou", ["1234567", "123456789", "abc12345", "12345678901"])
def test_supplier_rejects_invalid_edrpou_without_creating_entry(tmp_path, edrpou):
    with client_for(tmp_path) as client:
        response = client.post("/api/settings/suppliers", json={"name": "Постачальник", "code": "015", "edrpou": edrpou})
        assert response.status_code == 422
        assert client.get("/api/settings").json()["suppliers"] == []


def test_supplier_allows_individual_tax_number(tmp_path):
    with client_for(tmp_path) as client:
        response = client.post("/api/settings/suppliers", json={"name": "ФОП Приклад", "code": "015", "edrpou": "0123456789"})
        assert response.status_code == 200
        assert response.json()["edrpou"] == "0123456789"
