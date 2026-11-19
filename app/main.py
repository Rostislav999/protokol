"""Local document workspace, embedded in the native desktop application."""
from __future__ import annotations

import io
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import Literal
import re
import shutil
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .documents import FIELD_LABELS, TemplateError, render_protocol
from .extraction import extract_text, parse_act
from .library import Library, ROOT
from .matching import infer_subcategory, rank_templates
from .assignments_api import assignments_router
from .protocol_text import clean_product_name, normalize_sampling_description
from .settings import SettingsStore, settings_router
from .printing import printing_status
from .sample_details import first_seal_number, sample_weight_value
from .supplier_details import supplier_with_code
from .excel_api import excel_router
from .cover_letters import cover_letters_router
from .outgoing import JournalStore, outgoing_router
from .preferences import preferences_router
from .packages import packages_router
from .tasks import TaskManager, tasks_router
from .diagnostics import Diagnostics, DiagnosticMiddleware, diagnostics_router
from . import progress
from .word_tools import word_router
from .backups import backup_router, create_automatic_backup
from .workspace import DraftStore, WorkspaceError, workspace_router
from .storage_lock import DataAccessMiddleware, data_guard
from .assignment_library import AssignmentLibrary

MAX_UPLOAD = 25 * 1024 * 1024
local_tessdata = ROOT / ".local/tessdata"
if (local_tessdata / "ukr.traineddata").exists():
    os.environ.setdefault("TESSDATA_PREFIX", str(local_tessdata))


class Common(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=12000)
    customer: str = ""
    sampling_description: str = ""
    supplier: str = ""
    contract: str = ""
    act_number: str = ""
    act_date: str = ""
    seal_number: str = ""


class Item(BaseModel):
    model_config = ConfigDict(extra="ignore", str_max_length=12000)
    id: str = ""
    product_name: str = ""
    protocol_number: str = ""
    protocol_date: str = ""
    catalog_number: str = ""
    manufacturer: str = ""
    manufacture_date: str = ""
    batch_size: str = ""
    template_id: str | None = None
    standard: str = ""
    seal_number: str = ""
    sample_weight: str | None = None


class Generation(BaseModel):
    common: Common
    items: list[Item] = Field(min_length=1, max_length=100)
    confirmed: bool = False


class TextInput(BaseModel):
    text: str = Field(max_length=250000)


class MatchInput(BaseModel):
    product_name: str = Field(max_length=12000)


class TemplateUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    category: str = Field(default="Інше", max_length=100)
    subcategory: Literal["", "legumes", "cereals", "potatoes", "other"] = ""


def validate_aliases(aliases):
    if not isinstance(aliases, list) or len(aliases) > 100 or any(
        not isinstance(v, str) or len(v) > 300 for v in aliases
    ):
        raise HTTPException(422, "Ключові назви мають бути списком коротких рядків.")
    return list(dict.fromkeys(v.strip() for v in aliases if v.strip()))


async def read_upload(file: UploadFile) -> bytes:
    try:
        data = await file.read(MAX_UPLOAD + 1)
    finally:
        await file.close()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Файл завеликий. Максимальний розмір — 25 МБ.")
    if not data:
        raise HTTPException(422, "Завантажено порожній файл.")
    return data


def create_app(data_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="ОЗтаУСЯ", version="1.7.1")
    directory = data_dir or Path(os.environ.get("PROTOKOL_DATA_DIR", ROOT / "data"))
    with data_guard(directory):
        create_automatic_backup(directory, "1.7.1")
        library = Library(directory / "templates")
        settings = SettingsStore(directory)
    app.state.library = library
    app.state.data_directory = directory
    app.add_middleware(DataAccessMiddleware, directory=directory)

    def public_template(entry: dict) -> dict:
        return {**entry, "inferred_subcategory": infer_subcategory(entry.get("product_name") or entry["name"])}

    def enriched(text: str, known_templates=None) -> dict:
        result = parse_act(text)
        for item in result["items"]:
            item["id"] = uuid4().hex
            item.setdefault("protocol_date", "")
            item["template_id"] = None  # Never silently confirm a suggestion.
            item["matches"] = rank_templates(item["product_name"], known_templates if known_templates is not None else library.all())[:5]
        return {"text": text, **result}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "templates": len(library.all()),
                "ocr_installed": bool(shutil.which("tesseract")), "local_processing": True}

    @app.get("/api/templates")
    def templates():
        return {"templates": [public_template(entry) for entry in library.all()], "field_labels": FIELD_LABELS}

    @app.get("/api/printing/status")
    def print_status():
        return printing_status()

    @app.post("/api/templates")
    async def add_template(file: UploadFile = File(...), name: str = Form(""),
                           aliases: str = Form("[]"), category: str = Form("Інше"), subcategory: str = Form("")):
        if not (file.filename or "").lower().endswith(".docx"):
            raise HTTPException(422, "Шаблон має бути у форматі .docx.")
        if len(name) > 300 or len(category) > 100:
            raise HTTPException(422, "Назва або категорія надто довга.")
        if subcategory not in ("", "legumes", "cereals", "potatoes", "other"):
            raise HTTPException(422, "Невідома група другої страви.")
        try:
            alias_list = validate_aliases(json.loads(aliases))
            data = await read_upload(file)
            return public_template(library.add(data, file.filename, name, alias_list, category, subcategory))
        except (ValueError, TemplateError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/templates/{template_id}")
    def update_template(template_id: str, values: TemplateUpdate):
        try:
            fields = values.model_dump()
            fields["aliases"] = validate_aliases(fields["aliases"])
            if not fields["name"].strip():
                raise HTTPException(422, "Вкажіть назву шаблону.")
            return public_template(library.update(template_id, fields))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.delete("/api/templates/{template_id}")
    def delete_template(template_id: str):
        try:
            library.delete(template_id)
            return {"deleted": True}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/templates/{template_id}/download")
    def download_template(template_id: str):
        try:
            entry = library.get(template_id)
            return FileResponse(library.directory / (entry["id"] + ".docx"),
                                filename=entry["filename"],
                                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/extract")
    async def extract(file: UploadFile = File(...)):
        filename = file.filename or "scan"
        data = await read_upload(file)
        try:
            result = await run_in_threadpool(extract_text, data, filename)
            parsed = enriched(result["text"])
            parsed["warnings"] = list(dict.fromkeys(result.get("warnings", []) + parsed.get("warnings", [])))
            return {**result, **parsed}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/parse")
    def parse(values: TextInput):
        return enriched(values.text)

    @app.post("/api/match")
    def match(values: MatchInput):
        return {"matches": rank_templates(values.product_name, library.all())[:5]}

    @app.post("/api/generate")
    def generate(values: Generation):
        if not values.confirmed:
            raise HTTPException(422, "Підтвердьте перевірку розпізнаних даних та вибір шаблонів.")
        if not values.common.customer.strip() or not values.common.sampling_description.strip():
            raise HTTPException(422, "Заповніть замовника та адресу й обставини відбору.")
        numbers = []
        for i, item in enumerate(values.items, 1):
            item.protocol_number = re.sub(r"^(?:№\s*)+", "", item.protocol_number.strip()).strip()
            item.product_name = clean_product_name(item.product_name, item.catalog_number)
            if re.fullmatch(r"\d*(?:-\d*){4}", item.protocol_number):
                parts = item.protocol_number.split("-")
                if not all(parts) or not re.fullmatch(r"\d{4}", parts[3]):
                    raise HTTPException(422, f"Позиція {i}: перевірте індивідуальну чотиризначну частину номера протоколу.")
            if any(not (getattr(item, key) or "").strip() for key in
                   ("product_name", "protocol_number", "protocol_date", "template_id")):
                raise HTTPException(422, f"Позиція {i}: заповніть назву, номер і дату протоколу та оберіть шаблон.")
            numbers.append(item.protocol_number.strip())
        if len(numbers) != len(set(numbers)):
            raise HTTPException(422, "Номери протоколів мають бути унікальними для кожної позиції.")
        output = io.BytesIO()
        summary = []
        suppliers = settings.all()["suppliers"]
        with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
            for i, item in enumerate(values.items, 1):
                try:
                    template = library.get(item.template_id)
                    fields = {**values.common.model_dump(), **item.model_dump()}
                    fields["supplier"] = supplier_with_code(fields["supplier"], suppliers)
                    fields["seal_number"] = first_seal_number(item.seal_number.strip() or values.common.seal_number.strip())
                    fields["sample_weight"] = sample_weight_value(
                        item.sample_weight if item.sample_weight is not None else template.get("sample_weight", ""))
                    fields["sampling_description"] = normalize_sampling_description(fields["sampling_description"])
                    unsupported = [label for key, label in FIELD_LABELS.items()
                                   if fields.get(key, "").strip() and key not in template["available_fields"]]
                    if unsupported:
                        raise TemplateError("У шаблоні немає полів для цих даних: " + ", ".join(unsupported)
                                            + ". Додайте відповідні метки до Word-шаблону.")
                    content = render_protocol(library.read(item.template_id), fields)
                except (KeyError, TemplateError) as exc:
                    raise HTTPException(422, f"Позиція {i}: {exc}") from exc
                safe_name = re.sub(r'[^\w\- .]', '_', f"{item.protocol_number} {item.product_name}", flags=re.U)[:130].strip('. ')
                filename = f"{i:02d} {safe_name}.docx"
                bundle.writestr(filename, content)
                missing = [label for key, label in FIELD_LABELS.items() if not fields.get(key, "").strip()]
                summary.append(f"{filename}\nШаблон: {template['name']}\nНе заповнено: {', '.join(missing) or 'немає'}\n")
            bundle.writestr("ПЕРЕВІРИТИ.txt", "Це підготовлені проєкти протоколів.\n"
                            "Оновлено дані зразка у вступі, колонтитулах, таблиці, меті випробувань та визначених полях висновку.\n"
                            "Решта відомостей збережена з шаблону, включно зі старими результатами, датами та підписами.\n"
                            "Червоний текст залишився з шаблону та потребує перевірки; чорний текст підставлено програмою.\n"
                            "Перевірте їх окремо перед використанням документа.\n"
                            "Порожні заповнювані поля позначено [НЕ ЗАПОВНЕНО] замість старих значень.\n\n" + "\n".join(summary))
        return Response(output.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="protocols.zip"'})

    app.include_router(settings_router(settings))
    app.include_router(excel_router(settings))
    journal = JournalStore(directory / 'outgoing.sqlite3')
    app.include_router(cover_letters_router(settings, journal))
    app.include_router(outgoing_router(journal))
    app.include_router(preferences_router(directory))
    app.include_router(word_router())
    with data_guard(directory):
        app.include_router(assignments_router(directory, settings))
    app.include_router(workspace_router(directory))
    def template_exists(family, template_id):
        entries = library.all() if family == 'protocols' else AssignmentLibrary(directory / 'assignment_templates').all()
        return any(entry['id'] == template_id for entry in entries)
    app.include_router(packages_router(directory, template_exists))

    def import_act(files, options):
        name, data = files[0]
        progress.file(1, 1, name)
        with data_guard(directory):
            known_templates = library.all()
        result = extract_text(data, name)
        progress.phase('Визначаємо реквізити та шаблони')
        parsed = enriched(result['text'], known_templates)
        return {**result, **parsed, 'warnings': list(dict.fromkeys(result.get('warnings', []) + parsed.get('warnings', [])))}

    def import_documents(operation, files, options):
        with data_guard(directory):
            suppliers = settings.all()['suppliers']
        if operation == 'cover_letters':
            from .cover_letters import prepare
            return prepare(files, suppliers, options['exact_pages'])
        from .excel_api import import_protocols
        return import_protocols(files, suppliers)

    manager = TaskManager({'act': import_act,
                           'cover_letters': lambda files, options: import_documents('cover_letters', files, options),
                           'excel': lambda files, options: import_documents('excel', files, options)})
    app.state.tasks = manager
    app.include_router(tasks_router(manager))
    recorder = Diagnostics(directory)
    app.add_middleware(DiagnosticMiddleware, recorder=recorder)
    app.include_router(diagnostics_router(app, directory, recorder, manager))

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            manager.close()
    app.router.lifespan_context = lifespan

    def validate_restored(stage: Path):
        try:
            previous_revisions = DraftStore(directory).all()["revisions"]
        except WorkspaceError:
            previous_revisions = {}
        DraftStore(stage).rebase_after_restore(previous_revisions)
        # Complete supported migrations on the staging copy, preserving deleted
        # templates through the existing seed ledgers before committing restore.
        Library(stage / "templates")
        AssignmentLibrary(stage / "assignment_templates")
        SettingsStore(stage).all()
        from .preferences import _read, SignerState, Appearance
        _read(stage / 'signers.json', SignerState)
        _read(stage / 'appearance.json', Appearance)
        from .workbench_store import WorkStore
        if (stage / 'workbench.sqlite3').exists():
            WorkStore(stage).rebase()

    app.include_router(backup_router(directory, version="1.7.1", validate_staged=validate_restored))
    app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
    app.mount("/", StaticFiles(directory=ROOT / "app/static", html=True), name="home")
    return app


app = create_app()
