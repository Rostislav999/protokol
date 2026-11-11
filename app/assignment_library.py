"""Persistent assignment templates, separate from protocol forms."""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .assignments_documents import inspect_assignment_template
from .assignment_forms import create_assignment_form
from .documents import TemplateError
from .library import ROOT

DEPARTMENTS = {
    "vet": "Ветеринарно-санітарна експертиза та іхтіопатологія",
    "bacteriology": "Бактеріологічне відділення",
    "chemistry": "Хіміко-токсикологічне та радіологічне відділення",
}
SCOPES = ("", "family", "first_course", "jam", "ready_second_cereals_potatoes", "ready_second_all")


class AssignmentLibrary:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.index = directory / "library.json"
        self.seed_record = directory / "seeded.json"
        self.lock = RLock()
        entries = self.all() if self.index.exists() else []
        known = set(json.loads(self.seed_record.read_text(encoding="utf-8"))) if self.seed_record.exists() else set()
        manifest = json.loads((ROOT / "assets/assignments.json").read_text(encoding="utf-8"))
        for sample in manifest:
            if sample["id"] not in known and not any(e["id"] == sample["id"] for e in entries):
                data = (ROOT / "assets/assignments" / sample["asset"]).read_bytes()
                info = inspect_assignment_template(data)
                entry = {**sample, "available_fields": info["available_fields"],
                         "sample_weight": info["sample_weight"]}
                (directory / (entry["id"] + ".docx")).write_bytes(data)
                entries.append(entry)
        # Existing libraries, including user uploads, predate weight defaults.
        # Read their own Word files without replacing names, aliases or forms.
        for entry in entries:
            if "sample_weight" not in entry:
                try:
                    data = (directory / (entry["id"] + ".docx")).read_bytes()
                    entry["sample_weight"] = inspect_assignment_template(data)["sample_weight"]
                except (OSError, TemplateError):
                    entry["sample_weight"] = ""
        self._save(entries)
        self.seed_record.write_text(json.dumps(sorted(known | {s["id"] for s in manifest})), encoding="utf-8")

    def _save(self, entries):
        temp = self.index.with_suffix(".tmp")
        temp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.index)

    def all(self) -> list[dict]:
        with self.lock:
            return json.loads(self.index.read_text(encoding="utf-8"))

    def get(self, template_id: str) -> dict:
        for entry in self.all():
            if entry["id"] == template_id:
                return entry
        raise KeyError("Шаблон завдання не знайдено.")

    def read(self, template_id: str) -> bytes:
        entry = self.get(template_id)
        return (self.directory / (entry["id"] + ".docx")).read_bytes()

    def add(self, data: bytes, filename: str, name: str, aliases: list[str], department: str, product_scope: str,
            *, form: dict | None = None):
        info = inspect_assignment_template(data)
        actual = info["department"]
        if actual and department and actual != department:
            raise TemplateError("Обране відділення не відповідає заголовку Word-шаблону.")
        department = actual or department
        if department not in DEPARTMENTS:
            raise TemplateError("Укажіть відділення або додайте до документа заголовок відділення.")
        entry = {"id": uuid4().hex, "name": name.strip() or Path(filename).stem,
                 "filename": Path(filename).name, "department": department,
                 "product_name": info["product_name"], "standard": info["standard"],
                 "sample_weight": info["sample_weight"],
                 "aliases": aliases, "product_scope": product_scope,
                 "available_fields": info["available_fields"]}
        if form is not None:
            entry.update(editable_form=True, form=form)
        if not entry["standard"] and form is None:
            raise TemplateError("Укажіть конкретний НД у Word-шаблоні, щоб програма могла безпечно підібрати завдання.")
        with self.lock:
            entries = self.all()
            (self.directory / (entry["id"] + ".docx")).write_bytes(data)
            self._save([*entries, entry])
        return entry

    def create_form(self, values: dict):
        data = create_assignment_form(values)
        filename = values['name'].strip() + '.docx'
        return self.add(data, filename, values['name'], values['aliases'], values['department'],
                        values['product_scope'], form=values)

    def update_form(self, template_id: str, values: dict):
        data = create_assignment_form(values)
        info = inspect_assignment_template(data)
        with self.lock:
            entry = self.get(template_id)
            if not entry.get('editable_form'):
                raise TemplateError('Цей Word-шаблон редагується у Word. Для нового продукту створіть порожній бланк.')
            entry.update({key: values[key] for key in ('name', 'aliases', 'department', 'product_scope')})
            entry['filename'] = Path(values['name'] + '.docx').name
            entry.update({key: info[key] for key in ('product_name', 'standard', 'sample_weight', 'available_fields')})
            entry['form'] = values
            temporary = self.directory / (entry['id'] + '.docx.tmp')
            temporary.write_bytes(data)
            os.replace(temporary, self.directory / (entry['id'] + '.docx'))
            self._save([entry if e['id'] == template_id else e for e in self.all()])
        return entry

    def update(self, template_id: str, values: dict) -> dict:
        with self.lock:
            entry = self.get(template_id)
            entry.update(values)
            self._save([entry if e["id"] == template_id else e for e in self.all()])
            return entry

    def delete(self, template_id: str):
        with self.lock:
            entry = self.get(template_id)
            self._save([e for e in self.all() if e["id"] != template_id])
            (self.directory / (entry["id"] + ".docx")).unlink(missing_ok=True)
