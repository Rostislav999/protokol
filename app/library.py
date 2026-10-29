"""Persistent, user-editable template library, seeded with the supplied originals."""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .documents import FIELD_LABELS, TemplateError, inspect_template

ROOT = Path(__file__).resolve().parent.parent


class Library:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index = directory / "library.json"
        self.lock = RLock()
        manifest = json.loads((ROOT / "assets/library.json").read_text(encoding="utf-8"))
        seed_record = directory / "seeded.json"
        entries = self.all() if self.index.exists() else []
        if seed_record.exists():
            known = set(json.loads(seed_record.read_text(encoding="utf-8")))
        else:
            # Upgrade from the first release: old built-ins may have been
            # deliberately deleted; never silently restore them.
            known = {s["id"] for s in manifest if s.get("seed_version", 1) == 1} if self.index.exists() else set()
        for sample in manifest:
            if sample["id"] not in known and not any(e["id"] == sample["id"] for e in entries):
                raw = (ROOT / "assets/templates" / sample["filename"]).read_bytes()
                info = inspect_template(raw)
                entry = {**sample, "product_name": info["product_name"],
                         "available_fields": info["available_fields"], "sample_weight": info["fields"].get("sample_weight", ""),
                         "protocol_fields_version": 3}
                (directory / (entry["id"] + ".docx")).write_bytes(raw)
                entries.append(entry)
            # Introduce the subgroup field without overwriting user settings.
            for entry in entries:
                if entry["id"] == sample["id"]:
                    entry.setdefault("subcategory", sample.get("subcategory", ""))
        # Discover new supported rows in existing originals without resetting
        # user names, aliases, categories or deliberately deleted templates.
        for entry in entries:
            if entry.get("protocol_fields_version") != 3:
                try:
                    raw = (directory / (entry["id"] + ".docx")).read_bytes()
                    info = inspect_template(raw)
                    entry["available_fields"] = info["available_fields"]
                    entry["sample_weight"] = info["fields"].get("sample_weight", "")
                    entry["protocol_fields_version"] = 3
                except (OSError, TemplateError):
                    pass
        self._save(entries)
        seed_record.write_text(json.dumps(sorted(known | {s["id"] for s in manifest})), encoding="utf-8")

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
        raise KeyError("Шаблон не знайдено в бібліотеці.")

    def read(self, template_id: str) -> bytes:
        entry = self.get(template_id)
        return (self.directory / (entry["id"] + ".docx")).read_bytes()

    def add(self, data: bytes, filename: str, name: str, aliases: list[str], category: str, subcategory: str = "") -> dict:
        info = inspect_template(data)
        required = {"protocol_number", "protocol_date", "product_name", "customer", "sampling_description"}
        missing = required - set(info["available_fields"])
        if missing:
            labels = ", ".join(FIELD_LABELS[key] for key in sorted(missing))
            raise TemplateError("Додайте до шаблону обов’язкові поля перед контрольною фразою: " + labels)
        entry = {"id": uuid4().hex, "filename": Path(filename).name,
                 "name": name.strip() or info["product_name"] or Path(filename).stem,
                 "product_name": info["product_name"], "aliases": aliases,
                 "category": category.strip() or "Інше", "subcategory": subcategory,
                 "available_fields": info["available_fields"], "sample_weight": info["fields"].get("sample_weight", ""),
                 "protocol_fields_version": 3}
        with self.lock:
            entries = self.all()
            (self.directory / (entry["id"] + ".docx")).write_bytes(data)
            self._save([*entries, entry])
        return entry

    def update(self, template_id: str, values: dict) -> dict:
        with self.lock:
            entries = self.all()
            entry = self.get(template_id)
            entry.update(values)
            self._save([entry if e["id"] == template_id else e for e in entries])
            return entry

    def delete(self, template_id: str):
        with self.lock:
            entry = self.get(template_id)
            self._save([e for e in self.all() if e["id"] != template_id])
            (self.directory / (entry["id"] + ".docx")).unlink(missing_ok=True)
