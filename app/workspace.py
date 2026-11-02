"""Local, atomic workspace drafts, independent of a browser's temporary origin."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from threading import RLock
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
SECTIONS = frozenset({"acts", "assignments", "excel", "cover_letters"})
MAX_SECTION_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024 + 65536
MAX_ROWS = 100
MAX_DEPTH = 40
MAX_REVISION = 9007199254740991  # JavaScript's largest exact integer.
_APPROVAL_KEYS = frozenset({"confirmed", "reviewConfirmed", "review_confirmed"})
_LOCKS: dict[str, RLock] = {}
_LOCKS_GUARD = RLock()


class WorkspaceError(ValueError):
    """A draft cannot be read or updated without risking existing work."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise WorkspaceError("Чернетка має містити лише коректні JSON-дані без NaN та нескінченності.") from exc


def _draft_data(value: dict, *, depth: int = 0) -> dict:
    """Keep editable work while ensuring an approval never survives a restart."""
    def clean(item: Any, level: int) -> Any:
        if level > MAX_DEPTH:
            raise WorkspaceError("Структура чернетки надто вкладена.")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise WorkspaceError("Назви полів чернетки мають бути текстом.")
            return {key: False if key in _APPROVAL_KEYS else clean(child, level + 1)
                    for key, child in item.items()}
        if isinstance(item, list):
            return [clean(child, level + 1) for child in item]
        return item

    if not isinstance(value, dict):
        raise WorkspaceError("Дані розділу чернетки мають бути об’єктом.")
    for key in ("items", "records", "rows"):
        if isinstance(value.get(key), list) and len(value[key]) > MAX_ROWS:
            raise WorkspaceError("Чернетка може містити не більше 100 позицій у розділі.")
    if len(_encode(value)) > MAX_SECTION_BYTES:
        raise WorkspaceError("Чернетка розділу завелика: дозволено до 4 МБ.", 413)
    data = clean(value, depth)
    if len(_encode(data)) > MAX_SECTION_BYTES:
        raise WorkspaceError("Чернетка розділу завелика: дозволено до 4 МБ.", 413)
    return data


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DraftStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "workspace-drafts.json"
        self.lock_path = self.directory / ".workspace-drafts.lock"
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.path.resolve()), RLock())

    @contextmanager
    def _exclusive_write(self):
        # Separate windows can run separate local servers. The file lock keeps
        # revision comparison and replacement atomic across those processes.
        with self.lock:
            with self.lock_path.open("a+b") as handle:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except OSError as exc:
                    raise WorkspaceError("Чернетку змінює інше вікно. Спробуйте ще раз.", 409) from exc
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"schema_version": SCHEMA_VERSION, "sections": {}, "revisions": {}}
        try:
            if self.path.stat().st_size > MAX_FILE_BYTES:
                raise WorkspaceError("Файл чернеток завеликий. Відновіть його з резервної копії.", 409)
            state = json.loads(self.path.read_text(encoding="utf-8"),
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise WorkspaceError("Не вдалося прочитати чернетки. Файл збережено без змін; відновіть резервну копію.", 409) from exc
        if not isinstance(state, dict) or type(state.get("schema_version")) is not int:
            raise WorkspaceError("Невідомий формат чернеток. Файл збережено без змін.", 409)
        if state["schema_version"] != SCHEMA_VERSION:
            raise WorkspaceError("Чернетки створено іншою версією програми. Файл збережено без змін.", 409)
        sections = state.get("sections")
        revisions = state.setdefault("revisions", {})
        if not isinstance(sections, dict) or not isinstance(revisions, dict):
            raise WorkspaceError("Пошкоджено структуру чернеток. Файл збережено без змін.", 409)
        if any(not isinstance(key, str) or type(revision) is not int or not 0 <= revision < MAX_REVISION
               for key, revision in revisions.items()):
            raise WorkspaceError("Пошкоджено версії чернеток. Файл збережено без змін.", 409)
        for key, entry in sections.items():
            if key not in SECTIONS:
                # Preserve extension sections from compatible versions rather
                # than silently dropping work this version cannot edit.
                continue
            if (not isinstance(entry, dict) or not isinstance(entry.get("data"), dict)
                    or type(entry.get("revision")) is not int or not 1 <= entry["revision"] < MAX_REVISION
                    or not isinstance(entry.get("updated_at"), str)):
                raise WorkspaceError("Пошкоджено розділ чернетки. Файл збережено без змін.", 409)
            try:
                _draft_data(entry["data"])
                datetime.fromisoformat(entry["updated_at"].replace("Z", "+00:00"))
            except (ValueError, TypeError) as exc:
                raise WorkspaceError("Пошкоджено дані чернетки. Файл збережено без змін.", 409) from exc
            revisions[key] = max(revisions.get(key, 0), entry["revision"])
        return state

    def all(self) -> dict:
        # os.replace presents either complete old or complete new JSON to a
        # reader. A read does not need to create a lock file or a draft file.
        with self.lock:
            return deepcopy(self._read())

    def _write(self, state: dict) -> None:
        encoded = _encode(state)
        if len(encoded) > MAX_FILE_BYTES:
            raise WorkspaceError("Загальний розмір чернеток завеликий.", 413)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix="workspace-drafts-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _section(section: str) -> None:
        if section not in SECTIONS:
            raise WorkspaceError("Невідомий розділ чернетки.", 404)

    @staticmethod
    def _expect(state: dict, section: str, expected_revision: int | None) -> int:
        revision = state["revisions"].get(section, 0)
        if revision + 1 >= MAX_REVISION:
            raise WorkspaceError("Перевищено підтримуваний діапазон версій чернетки.", 409)
        if expected_revision is not None and expected_revision != revision:
            raise WorkspaceError("Чернетку вже змінено в іншому вікні. Оновіть її перед збереженням.", 409)
        return revision

    def save(self, section: str, data: dict, expected_revision: int | None = None) -> dict:
        self._section(section)
        clean = _draft_data(data)
        with self._exclusive_write():
            state = self._read()
            revision = self._expect(state, section, expected_revision) + 1
            entry = {"data": clean, "revision": revision, "updated_at": _timestamp()}
            state["sections"][section] = entry
            state["revisions"][section] = revision
            self._write(state)
            return deepcopy(entry)

    def delete(self, section: str, expected_revision: int | None = None) -> dict:
        self._section(section)
        with self._exclusive_write():
            state = self._read()
            revision = self._expect(state, section, expected_revision) + 1
            state["sections"].pop(section, None)
            state["revisions"][section] = revision
            self._write(state)
            return {"deleted": True, "revision": revision, "updated_at": _timestamp()}

    def rebase_after_restore(self, previous_revisions: dict[str, int]) -> None:
        """Invalidate every open window without changing restored field values."""
        with self._exclusive_write():
            state = self._read()
            floor = max([int(time.time() * 1000), *previous_revisions.values(), *state["revisions"].values()])
            if floor + 1 >= MAX_REVISION:
                raise WorkspaceError("Версії чернеток у резервній копії виходять за підтримуваний діапазон.")
            for section in SECTIONS:
                revision = floor + 1
                state["revisions"][section] = revision
                if section in state["sections"]:
                    state["sections"][section]["revision"] = revision
            self._write(state)


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any]
    expected_revision: int | None = Field(default=None, ge=0, strict=True)


def workspace_router(directory: Path) -> APIRouter:
    router = APIRouter(prefix="/api/workspace/drafts", tags=["workspace"])
    store = DraftStore(directory)

    def perform(function, *args):
        try:
            return function(*args)
        except WorkspaceError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, "Не вдалося зберегти чернетку на диску. Перевірте доступ і вільне місце.") from exc

    @router.get("")
    def get_drafts():
        return perform(store.all)

    @router.put("/{section}")
    def save_draft(section: str, values: DraftUpdate):
        return perform(store.save, section, values.data, values.expected_revision)

    @router.delete("/{section}")
    def delete_draft(section: str, expected_revision: int | None = Query(default=None, ge=0)):
        return perform(store.delete, section, expected_revision)

    return router
