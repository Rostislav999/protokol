"""Persistent local customer/supplier directories and assignment issuers."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "settings.json"
        self.lock = RLock()

    def all(self) -> dict:
        with self.lock:
            if not self.path.exists():
                return {"customers": [], "suppliers": [], "issuers": [], "default_issuer_id": ""}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            # Earlier versions stored suppliers and issuers only. Reading them
            # must leave all saved records and the selected issuer intact.
            data.setdefault("customers", [])
            return data

    def _save(self, data: dict) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def save_entry(self, kind: str, values: dict, entry_id: str | None = None) -> dict:
        name = values["name"].strip()
        if not name or len(name) > 500 or any(ord(c) < 32 for c in name):
            raise SettingsError("Укажіть коректне ім’я або назву.")
        if kind == "suppliers":
            code = values["code"].strip()
            if not re.fullmatch(r"[0-9]{1,10}", code):
                raise SettingsError("Код постачальника має містити від 1 до 10 цифр. Початкові нулі зберігаються.")
            fields = {"name": name, "code": code}
            edrpou = values.get("edrpou")
            if edrpou is not None:
                edrpou = edrpou.strip()
                if edrpou and not re.fullmatch(r"[0-9]{8}(?:[0-9]{2})?", edrpou):
                    raise SettingsError("Код ЄДРПОУ має містити 8 цифр; для ФОП допускається 10 цифр.")
                fields["edrpou"] = edrpou
        elif kind == "issuers":
            position = values.get("position", "").strip()
            if len(position) > 500 or any(ord(c) < 32 for c in position):
                raise SettingsError("Укажіть коректну посаду або військове звання.")
            fields = {"name": name, "position": position}
        elif kind == "customers":
            fields = {"name": name}
        else:
            raise SettingsError("Невідомий довідник.")
        with self.lock:
            data = self.all()
            if entry_id is not None and not any(entry["id"] == entry_id for entry in data[kind]):
                raise KeyError("Запис не знайдено.")
            for entry in data[kind]:
                if entry["id"] == entry_id:
                    continue
                if " ".join(entry["name"].casefold().split()) == " ".join(name.casefold().split()):
                    raise SettingsError("Такий запис уже є в довіднику.")
                if kind == "suppliers" and entry["code"] == fields["code"]:
                    raise SettingsError("Цей код уже призначено іншому постачальнику.")
            if kind == "suppliers" and "edrpou" not in fields:
                existing = next((entry for entry in data[kind] if entry["id"] == entry_id), {})
                fields["edrpou"] = existing.get("edrpou", "")
            result = {"id": entry_id or uuid4().hex, **fields}
            data[kind] = [entry for entry in data[kind] if entry["id"] != result["id"]] + [result]
            self._save(data)
            return deepcopy(result)

    def delete(self, kind: str, entry_id: str) -> None:
        with self.lock:
            data = self.all()
            if not any(entry["id"] == entry_id for entry in data[kind]):
                raise KeyError("Запис не знайдено.")
            data[kind] = [entry for entry in data[kind] if entry["id"] != entry_id]
            if kind == "issuers" and data["default_issuer_id"] == entry_id:
                data["default_issuer_id"] = ""
            self._save(data)

    def set_default_issuer(self, entry_id: str) -> dict:
        with self.lock:
            data = self.all()
            if entry_id and not any(entry["id"] == entry_id for entry in data["issuers"]):
                raise SettingsError("Оберіть особу з довідника.")
            data["default_issuer_id"] = entry_id
            self._save(data)
            return data

    def issuer(self, entry_id: str) -> dict:
        for entry in self.all()["issuers"]:
            if entry["id"] == entry_id:
                return entry
        raise SettingsError("Обрану особу, яка видає завдання, не знайдено. Оберіть її повторно.")


class SupplierValues(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    code: str = Field(min_length=1, max_length=10)
    edrpou: str | None = Field(default=None, max_length=10)


class IssuerValues(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    position: str = Field(default="", max_length=500)


class CustomerValues(BaseModel):
    name: str = Field(min_length=1, max_length=500)


class SettingsUpdate(BaseModel):
    default_issuer_id: str = Field(max_length=200)


def settings_router(store: SettingsStore) -> APIRouter:
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    @router.get("")
    def get_settings():
        return store.all()

    @router.patch("")
    def update_settings(values: SettingsUpdate):
        try:
            return store.set_default_issuer(values.default_issuer_id)
        except SettingsError as exc:
            raise HTTPException(422, str(exc)) from exc

    def save(kind, values, entry_id=None):
        try:
            return store.save_entry(kind, values.model_dump(), entry_id)
        except SettingsError as exc:
            raise HTTPException(422, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    def remove(kind, entry_id):
        try:
            store.delete(kind, entry_id)
            return {"deleted": True}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/suppliers")
    def add_supplier(values: SupplierValues):
        return save("suppliers", values)

    @router.patch("/suppliers/{entry_id}")
    def edit_supplier(entry_id: str, values: SupplierValues):
        return save("suppliers", values, entry_id)

    @router.delete("/suppliers/{entry_id}")
    def delete_supplier(entry_id: str):
        return remove("suppliers", entry_id)

    @router.post("/customers")
    def add_customer(values: CustomerValues):
        return save("customers", values)

    @router.patch("/customers/{entry_id}")
    def edit_customer(entry_id: str, values: CustomerValues):
        return save("customers", values, entry_id)

    @router.delete("/customers/{entry_id}")
    def delete_customer(entry_id: str):
        return remove("customers", entry_id)

    @router.post("/issuers")
    def add_issuer(values: IssuerValues):
        return save("issuers", values)

    @router.patch("/issuers/{entry_id}")
    def edit_issuer(entry_id: str, values: IssuerValues):
        return save("issuers", values, entry_id)

    @router.delete("/issuers/{entry_id}")
    def delete_issuer(entry_id: str):
        return remove("issuers", entry_id)

    return router
