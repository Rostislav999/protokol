"""Assignment generation for three departments with durable sample numbers."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from typing import Literal
from zoneinfo import ZoneInfo
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .assignment_library import AssignmentLibrary, DEPARTMENTS, SCOPES
from .assignment_matching import rank_assignments
from .assignments_documents import render_assignment
from .documents import TemplateError
from .numbering import NumberingStore, NumberingError
from .settings import SettingsStore, SettingsError
from .printing import PrintError, print_assignments

Scope = Literal["", "family", "first_course", "jam", "ready_second_cereals_potatoes", "ready_second_all"]


def kyiv_today() -> str:
    return datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()


class AssignmentMatch(BaseModel):
    product_name: str = Field(max_length=12000)
    standard: str = Field(default="", max_length=12000)


class AssignmentSelection(BaseModel):
    template_id: str = Field(min_length=1, max_length=200)
    sample_weight: str | None = Field(default=None, max_length=2000)
    allow_standard_override: bool = False


class AssignmentItem(BaseModel):
    model_config = ConfigDict(extra="ignore", str_max_length=12000)
    id: str = Field(min_length=1, max_length=200)
    product_name: str
    standard: str
    sample_weight: str = ""
    departments: dict[str, AssignmentSelection] = Field(min_length=1, max_length=3)


class AssignmentGeneration(BaseModel):
    job_id: str = Field(min_length=1, max_length=200)
    confirmed: bool = False
    start_number: int | None = Field(default=None, ge=1, le=2147483647, strict=True)
    items: list[AssignmentItem] = Field(min_length=1, max_length=100)
    issuer_id: str = Field(default="", max_length=200)


class AssignmentPrint(BaseModel):
    generation: AssignmentGeneration
    copies: Literal[2] = 2
    confirmed: bool = False


class NumberSetting(BaseModel):
    next_number: int = Field(ge=1, le=2147483647, strict=True)


class NumberPreview(BaseModel):
    sample_ids: list[str] = Field(default_factory=list, max_length=100)


class AssignmentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    product_scope: Scope | None = None


def _aliases(value):
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or len(v) > 300 for v in value):
        raise ValueError("Ключові назви мають бути списком коротких рядків.")
    return list(dict.fromkeys(v.strip() for v in value if v.strip()))


def assignments_router(directory: Path, settings: SettingsStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/assignments", tags=["assignments"])
    library = AssignmentLibrary(directory / "assignment_templates")
    numbering = NumberingStore(directory / "numbering.sqlite3")
    settings = settings or SettingsStore(directory)

    @router.get("/templates")
    def templates():
        return {"templates": library.all(), "departments": [{"id": key, "name": label} for key, label in DEPARTMENTS.items()], "today": kyiv_today()}

    @router.post("/templates")
    async def add_template(file: UploadFile = File(...), name: str = Form(""), aliases: str = Form("[]"),
                           department: str = Form(""), product_scope: str = Form("")):
        filename = file.filename or ""
        if not filename.lower().endswith(".docx") or len(name) > 300 or product_scope not in SCOPES:
            raise HTTPException(422, "Перевірте формат .docx, назву й область застосування шаблону.")
        try:
            try:
                data = await file.read(25 * 1024 * 1024 + 1)
            finally:
                await file.close()
            if not data or len(data) > 25 * 1024 * 1024:
                raise ValueError("Файл порожній або перевищує 25 МБ.")
            return library.add(data, filename, name, _aliases(json.loads(aliases)), department, product_scope)
        except (ValueError, TemplateError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.patch("/templates/{template_id}")
    def edit_template(template_id: str, values: AssignmentUpdate):
        try:
            fields = values.model_dump(exclude_none=True)
            if not fields["name"].strip():
                raise ValueError("Укажіть назву шаблону.")
            fields["aliases"] = _aliases(fields["aliases"])
            return library.update(template_id, fields)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/templates/{template_id}")
    def delete_template(template_id: str):
        try:
            library.delete(template_id)
            return {"deleted": True}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/templates/{template_id}/download")
    def download_template(template_id: str):
        try:
            entry = library.get(template_id)
            return FileResponse(library.directory / (entry["id"] + ".docx"), filename=entry["filename"],
                                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/match")
    def match(values: AssignmentMatch):
        return rank_assignments(values.product_name, values.standard, library.all())

    @router.get("/numbering")
    def number_status():
        return numbering.status()

    @router.post("/numbering/preview")
    def number_preview(values: NumberPreview):
        try:
            return {**numbering.status(), "numbers": numbering.get_sample_numbers(values.sample_ids)}
        except NumberingError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/numbering")
    def number_setting(values: NumberSetting):
        try:
            return numbering.set_next_number(values.next_number)
        except NumberingError as exc:
            raise HTTPException(422, str(exc)) from exc

    def archive_response(archive: bytes, numbers: list[str]) -> Response:
        # A repeat download keeps the original issue date, including tomorrow.
        with ZipFile(io.BytesIO(archive)) as bundle:
            issued = json.loads(bundle.read("НУМЕРАЦІЯ.json"))["issue_date"]
        return Response(archive, media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="assignments.zip"',
            "X-Next-Sample-Number": str(numbering.status()["next_number"]), "X-Assignment-Date": issued,
            "X-Sample-Numbers": json.dumps(numbers)})

    @router.post("/print")
    def print_generated_assignments(values: AssignmentPrint):
        if not values.confirmed or not values.generation.confirmed:
            raise HTTPException(422, "Підтвердьте перевірку завдань та друк двох примірників кожного документа.")
        generation = values.generation
        fingerprint = sha256(json.dumps(generation.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        try:
            cached = numbering.get_cached(generation.job_id, [item.id for item in generation.items], payload_hash=fingerprint)
            if cached is None:
                raise NumberingError("Спочатку створіть та перевірте завдання. Друк не присвоює нових номерів.")
            return print_assignments(cached[0], copies=values.copies)
        except (NumberingError, PrintError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/generate")
    def generate(values: AssignmentGeneration):
        if not values.confirmed:
            raise HTTPException(422, "Підтвердьте перевірку зразків, відділень, НД та шаблонів завдань.")
        if len({item.id for item in values.items}) != len(values.items):
            raise HTTPException(422, "Кожна позиція повинна мати окремий ідентифікатор зразка.")
        fingerprint = sha256(json.dumps(values.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        try:
            cached = numbering.get_cached(values.job_id, [item.id for item in values.items], payload_hash=fingerprint)
        except NumberingError as exc:
            raise HTTPException(422, str(exc)) from exc
        if cached is not None:
            return archive_response(*cached)
        try:
            issuer = settings.issuer(values.issuer_id) if values.issuer_id else None
        except SettingsError as exc:
            raise HTTPException(422, str(exc)) from exc
        selected = []
        for index, item in enumerate(values.items, 1):
            if not item.product_name.strip() or not item.standard.strip():
                raise HTTPException(422, f"Позиція {index}: укажіть назву зразка та НД з акта.")
            candidates = rank_assignments(item.product_name, item.standard, library.all())
            for department, selection in item.departments.items():
                if department not in DEPARTMENTS:
                    raise HTTPException(422, "Невідоме відділення.")
                try:
                    template = library.get(selection.template_id)
                except KeyError as exc:
                    raise HTTPException(422, str(exc)) from exc
                if template["department"] != department:
                    raise HTTPException(422, f"Позиція {index}: шаблон належить іншому відділенню.")
                exact_ids = {c["id"] for c in candidates["departments"][department]}
                review_ids = {c["id"] for c in candidates.get("review_departments", {}).get(department, [])}
                if template["id"] not in exact_ids:
                    if template["id"] not in review_ids or not selection.allow_standard_override:
                        raise HTTPException(422, f"Позиція {index}: шаблон не відповідає продукту та НД. Відмінний НД потребує окремого підтвердження.")
                selected.append((index - 1, department, template, selection))
        today = kyiv_today()

        def builder(numbers: list[str]) -> bytes:
            output = io.BytesIO()
            summary = [f"Дата видачі завдань: {today} (Europe/Kyiv).", "Один номер зразка використовується для всіх його відділень.",
                       "Методи досліджень, підписи та НД збережені з обраного шаблону. Перевірте вагу й кількість проб.", ""]
            if issuer:
                summary.insert(2, "Сформулював та видав завдання: " + " ".join(value for value in
                               (issuer["position"], issuer["name"]) if value))
            with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
                for item_index, department, template, selection in selected:
                    item = values.items[item_index]
                    # Explicit edits, including a cleared field, take priority.
                    # API clients may omit the weight to use the form's default.
                    weight = (selection.sample_weight.strip() if selection.sample_weight is not None
                              else item.sample_weight.strip() or template.get("sample_weight", "").strip())
                    fields = {
                        "product_name": item.product_name, "sample_number": numbers[item_index],
                        "sample_weight": weight, "standard": template["standard"], "issue_date": today}
                    if issuer:
                        fields.update(issuer_name=issuer["name"], issuer_position=issuer["position"])
                    data = render_assignment(library.read(template["id"]), fields)
                    safe_name = re.sub(r"[^\w .-]", "_", item.product_name, flags=re.U)[:80].strip('. ')
                    department_name = {"vet": "Ветсан", "bacteriology": "Бактеріологія", "chemistry": "Хімтокс"}[department]
                    filename = f"{numbers[item_index]} {department_name} {safe_name}.docx"
                    bundle.writestr(filename, data)
                    summary.extend([filename, "НД з акта: " + item.standard, "НД завдання: " + template["standard"],
                                    "Вага: " + (weight or "[НЕ ЗАПОВНЕНО]"), ""])
                bundle.writestr("ПЕРЕВІРИТИ.txt", "\n".join(summary))
                bundle.writestr("НУМЕРАЦІЯ.json", json.dumps({"issue_date": today, "issuer": issuer, "samples": [
                    {"id": item.id, "number": number, "product_name": item.product_name}
                    for item, number in zip(values.items, numbers)]}, ensure_ascii=False, indent=2))
            return output.getvalue()

        try:
            archive, numbers = numbering.generate_with_numbers(values.job_id, [item.id for item in values.items],
                                                             values.start_number, builder, payload_hash=fingerprint)
        except (NumberingError, TemplateError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return archive_response(archive, numbers)

    return router
