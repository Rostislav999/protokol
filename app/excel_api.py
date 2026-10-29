"""Local import/review/export of the user's original D–N Excel register."""
from __future__ import annotations

import base64
from datetime import date
import io
from pathlib import Path
import re
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from .protocol_excel import DATES, FIELDS, NUMBERS, Record, date_text, extract_text, kg, protocol_key, read_docx, report_text, write_excel_bytes
from .protocol_text import clean_product_name
from .settings import SettingsStore
from .supplier_details import supplier_with_code

MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_EXPANDED = 150 * 1024 * 1024
MAX_RECORDS = 100
SOURCE_URL = "https://github.com/Rostislav999/-"
_COLUMNS = {column for column, _ in FIELDS}


class ExcelRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str = Field(default="Протокол", max_length=1000)
    values: dict[str, str]
    warnings: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("values")
    @classmethod
    def known_columns(cls, values):
        if set(values) - _COLUMNS:
            raise ValueError("Дозволені лише стовпці D–N. Відомості про відповідність не експортуються.")
        if any(len(value) > 4000 for value in values.values()):
            raise ValueError("Значення клітинки задовге.")
        return values

    @field_validator("warnings")
    @classmethod
    def bounded_warnings(cls, values):
        if any(len(value) > 5000 for value in values):
            raise ValueError("Текст попередження задовгий.")
        return values


class ExcelGeneration(BaseModel):
    records: list[ExcelRecord] = Field(min_length=1, max_length=MAX_RECORDS)
    confirmed: bool = False
    update_existing: bool = True


class ActExcelCommon(BaseModel):
    model_config = ConfigDict(extra="ignore", str_max_length=4000)
    supplier: str = ""


class ActExcelItem(BaseModel):
    model_config = ConfigDict(extra="ignore", str_max_length=4000)
    product_name: str = ""
    protocol_number: str = ""
    protocol_date: str = ""
    catalog_number: str = ""
    manufacturer: str = ""
    manufacture_date: str = ""
    batch_size: str = ""
    sample_weight: str | None = None


class ActExcelInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    common: ActExcelCommon = Field(default_factory=ActExcelCommon)
    items: list[ActExcelItem] = Field(min_length=1, max_length=MAX_RECORDS)
    source: str = Field(default="", max_length=1000)


def records_from_act(payload: ActExcelInput, suppliers: list[dict]) -> dict:
    """Map reviewed form fields directly, without generating or reading Word."""
    records = []
    supplier = supplier_with_code(payload.common.supplier, suppliers)
    for index, item in enumerate(payload.items, 1):
        record = Record(f"Акт: {_source_name(payload.source or 'поточна форма')} · позиція {index}")
        v = record.values = {column: "" for column, _ in FIELDS}

        def parsed_date(value, label):
            value = value.strip()
            if not value:
                return ""
            try:
                iso = re.search(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", value)
                parsed = date.fromisoformat(iso[0]).strftime("%d.%m.%Y") if iso else date_text(value)
            except ValueError:
                parsed = ""
            if not parsed:
                record.warnings.append(f"{label}: не вдалося визначити дату з «{value}». Заповніть її вручну.")
            return parsed

        number = protocol_key(item.protocol_number)
        v["D"] = "№" + number if number else ""
        v["E"] = parsed_date(item.protocol_date, "Дата протоколу")
        codes = set(re.findall(r"(?<!\d)\d{4,8}(?!\d)", item.catalog_number))
        v["F"] = next(iter(codes)) if len(codes) == 1 else ""
        if item.catalog_number.strip() and not v["F"]:
            record.warnings.append("Код каталогу: не знайдено однозначний код із 4–8 цифр.")
        v["G"] = clean_product_name(item.product_name, item.catalog_number)
        expiry = re.search(r"\bвжити\s+до\b", item.manufacture_date, re.I)
        manufactured = item.manufacture_date[:expiry.start()] if expiry else item.manufacture_date
        v["H"] = v["I"] = parsed_date(manufactured.strip(" /,;"), "Дата виготовлення")
        if expiry:
            v["J"] = parsed_date(item.manufacture_date[expiry.end():], 'Дата «Вжити до»')
        v["K"] = kg(item.batch_size)
        if item.batch_size.strip() and not v["K"]:
            record.warnings.append("Розмір партії: вкажіть масу з одиницею г або кг.")
        # The total sample weight is not the net weight of one package. Only
        # an explicit per-package mass immediately after «по» establishes L.
        each = re.search(r"\bпо\s+([0-9]+(?:[ \u00a0][0-9]{3})*(?:[,.][0-9]+)?\s*(?:кг|kg|г|g)\b)",
                         item.sample_weight or "", re.I)
        v["L"] = kg(each[1]) if each else ""
        v["M"] = supplier
        if supplier and not re.search(r"(?:код\s*)?[ЄЕ]ДРПОУ\s*:?\s*\d{8}(?:\d{2})?(?!\d)", supplier, re.I):
            record.warnings.append("Постачальник: код ЄДРПОУ не знайдено однозначно; перевірте довідник або заповніть поле вручну.")
        manufacturer_codes = set(re.findall(r"(?<!\d)\d{8}(?!\d)", item.manufacturer))
        v["N"] = next(iter(manufacturer_codes)) if len(manufacturer_codes) == 1 else ""
        if item.manufacturer.strip() and not v["N"]:
            record.warnings.append("Виробник: не знайдено однозначний код ЄДРПОУ з 8 цифр.")
        records.append({"id": uuid4().hex, "source": record.source[:1000], "values": v,
                        "warnings": record.warnings, "missing": record.missing()})
    return {"records": records, "warnings": [
        "Дані перенесено з поточної форми акта. Перевірте номер і дату протоколу та решту полів перед збереженням Excel."
    ]}


def _source_name(filename):
    return Path(str(filename or "Протокол").replace("\\", "/")).name[:1000]


def _read_record(data: bytes, filename: str) -> Record:
    if filename.lower().endswith(".docx"):
        return read_docx(data, filename)
    if filename.lower().endswith(".pdf"):
        from .protocol_pdf import read_protocol_pdf
        text, warnings = read_protocol_pdf(data)
        result = extract_text(text, filename)
        result.warnings = warnings + result.warnings
        return result
    raise ValueError("Підтримуються протоколи DOCX і PDF.")


def import_protocols(files: list[tuple[str, bytes]], suppliers: list[dict]) -> dict:
    """Read ZIP members in memory; archive paths are never extracted to disk."""
    records, warnings = [], []
    protocol_count, expanded = 0, 0

    def append(filename, data):
        nonlocal protocol_count, expanded
        protocol_count += 1
        expanded += len(data)
        if protocol_count > MAX_RECORDS or expanded > MAX_EXPANDED:
            raise ValueError("Комплект перевищує 100 протоколів або 150 МБ після розпакування.")
        if not data or len(data) > MAX_FILE:
            warnings.append(filename + ": файл порожній або перевищує 25 МБ.")
            return
        try:
            record = _read_record(data, filename)
            record.values["M"] = supplier_with_code(record.values.get("M", ""), suppliers)
            records.append({"id": uuid4().hex, "source": record.source, "values": record.values,
                            "warnings": record.warnings, "missing": record.missing()})
        except ValueError as exc:
            warnings.append(filename + ": " + str(exc))

    for filename, data in files:
        if not filename.lower().endswith(".zip"):
            append(filename, data)
            continue
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > 3000 or sum(member.file_size for member in members) > MAX_EXPANDED:
                    raise ValueError("ZIP надто великий після розпакування.")
                if len({member.filename for member in members}) != len(members):
                    raise ValueError("ZIP містить дублікати файлів.")
                documents = [member for member in members if not member.is_dir()
                             and member.filename.lower().endswith((".docx", ".pdf"))]
                if not documents:
                    raise ValueError("У ZIP немає протоколів DOCX або PDF.")
                if protocol_count + len(documents) > MAX_RECORDS:
                    raise ValueError("Комплект перевищує 100 протоколів.")
                for member in documents:
                    if member.flag_bits & 1:
                        warnings.append(_source_name(member.filename) + ": файл захищений паролем.")
                    elif member.file_size > MAX_FILE:
                        warnings.append(_source_name(member.filename) + ": файл перевищує 25 МБ.")
                    else:
                        append(_source_name(member.filename), archive.read(member))
        except (BadZipFile, RuntimeError, OSError, EOFError, ValueError) as exc:
            warnings.append(filename + ": " + str(exc))
    if not records:
        raise ValueError("Не вдалося прочитати протоколи. " + " ".join(warnings[:4]))
    return {"records": records, "warnings": warnings}


def excel_router(settings: SettingsStore) -> APIRouter:
    router = APIRouter(prefix="/api/excel", tags=["excel"])

    @router.get("/fields")
    def fields():
        return {"fields": [{"column": column, "label": label,
                            "type": "date" if column in DATES else "number" if column in NUMBERS else "text"}
                           for column, label in FIELDS], "source_url": SOURCE_URL}

    @router.post("/import")
    async def read_protocols(files: list[UploadFile] = File(...)):
        uploaded, total = [], 0
        try:
            if not 1 <= len(files) <= MAX_RECORDS:
                raise ValueError("Оберіть від 1 до 100 файлів.")
            for file in files:
                filename = _source_name(file.filename)
                if not filename.lower().endswith((".docx", ".pdf", ".zip")):
                    raise ValueError("Підтримуються DOCX, PDF та ZIP із протоколами.")
                data = await file.read(MAX_FILE + 1)
                total += len(data)
                if not data or len(data) > MAX_FILE or total > MAX_TOTAL:
                    raise ValueError("Кожен файл має бути до 25 МБ, загальний розмір — до 100 МБ.")
                uploaded.append((filename, data))
            return await run_in_threadpool(import_protocols, uploaded, settings.all()["suppliers"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            for file in files:
                await file.close()

    @router.post("/from-act")
    def from_act(payload: ActExcelInput):
        return records_from_act(payload, settings.all()["suppliers"])

    @router.post("/generate")
    async def generate(payload: str = Form(...), template: UploadFile | None = File(None)):
        try:
            if len(payload) > 3 * 1024 * 1024:
                raise ValueError("Дані комплекту завеликі.")
            values = ExcelGeneration.model_validate_json(payload)
            if not values.confirmed:
                raise ValueError("Підтвердьте перевірку даних і попереджень перед створенням Excel.")
            workbook = None
            if template is not None:
                if not (template.filename or "").lower().endswith(".xlsx"):
                    raise ValueError("Вихідна таблиця має бути у форматі .xlsx.")
                workbook = await template.read(MAX_FILE + 1)
                if not workbook or len(workbook) > MAX_FILE:
                    raise ValueError("Таблиця порожня або перевищує 25 МБ.")
            suppliers = settings.all()["suppliers"]
            records = []
            for value in values.records:
                cells = dict(value.values)
                cells["M"] = supplier_with_code(cells.get("M", ""), suppliers)
                records.append(Record(value.source, cells, list(value.warnings)))
            data, report = await run_in_threadpool(write_excel_bytes, workbook, records,
                                                   "update" if values.update_existing else "skip")
            if len(data) > MAX_FILE:
                raise ValueError("Результат перевищує 25 МБ. Розділіть комплект на менші частини.")
            return {"filename": "Протоколи.xlsx", "workbook_base64": base64.b64encode(data).decode("ascii"),
                    "report_text": report_text(report),
                    "counts": {key: len(report[key]) for key in ("added", "updated", "skipped")}}
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            if template is not None:
                await template.close()

    return router
