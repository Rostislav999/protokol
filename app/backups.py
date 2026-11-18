"""Local, checksummed backups and transactional restoration of application data.

Callers must serialize requests that mutate the same data directory while a
snapshot or restore is running. The store lock also serializes its own calls.
SQLite's backup API captures committed WAL data without copying live sidecars.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from threading import RLock
from typing import BinaryIO, Callable
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

FORMAT_VERSION = 1
MANIFEST = "backup-manifest.json"
MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_UNCOMPRESSED_BYTES = 8 * 1024**3
MAX_JSON_BYTES = 512 * 1024**2
COPY_CHUNK = 1024**2
MAX_FILES = 10000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
EXCLUDED_DIRECTORIES = {"tessdata", "backups", "logs", "__pycache__", ".local", ".pytest_cache"}
_BACKUP_NAME = re.compile(r"backup-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{12}\.zip")
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
_LOCKS: dict[str, RLock] = {}
_LOCKS_GUARD = RLock()
LOG = logging.getLogger(__name__)


class BackupError(ValueError):
    """An archive or operation cannot safely restore application data."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(raw: bytes | str):
    def invalid_constant(value):
        raise BackupError("Некоректне числове значення в JSON резервної копії.")

    try:
        return json.loads(raw, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("Резервна копія містить пошкоджений JSON.") from exc


def _relative(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise BackupError("Некоректний шлях у резервній копії.")
    path = PurePosixPath(name)
    parts = name.split("/")
    if path.is_absolute() or any(part in ("", ".", "..") for part in parts):
        raise BackupError("Резервна копія містить небезпечний шлях.")
    for part in parts:
        if part.endswith((".", " ")) or any(char in part for char in ':<>"|?*') or any(ord(char) < 32 for char in part):
            raise BackupError("Некоректне ім’я файла в резервній копії.")
        if part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise BackupError("Зарезервоване ім’я файла в резервній копії.")
    return path.as_posix()


def _excluded(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    name = parts[-1].casefold()
    return (any(part.casefold() in EXCLUDED_DIRECTORIES for part in parts[:-1])
            or name.endswith((".tmp", ".pyc", ".lock", "-wal", "-shm", "-journal"))
            or name.startswith((".restore-", ".backup-")))


@contextmanager
def _sqlite_snapshot(source: Path, work_directory: Path):
    with tempfile.TemporaryDirectory(prefix=".backup-sqlite-", dir=work_directory) as temporary:
        target = Path(temporary) / "snapshot.sqlite3"
        try:
            with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as connection:
                with closing(sqlite3.connect(target)) as destination:
                    connection.backup(destination)
        except sqlite3.Error as exc:
            raise BackupError("Не вдалося створити узгоджену копію бази даних.") from exc
        yield target


def _copy_with_checksum(source: BinaryIO, target: BinaryIO, limit: int) -> dict:
    checksum = sha256()
    size = 0
    while block := source.read(COPY_CHUNK):
        size += len(block)
        if size > limit:
            raise BackupError("Розмір даних резервної копії перевищує дозволений.")
        target.write(block)
        checksum.update(block)
    return {"sha256": checksum.hexdigest(), "size": size}


def _validate_numbering(path: Path) -> None:
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("База нумерації у резервній копії пошкоджена.")
            required = {
                "numbering_state": {"singleton", "next_number"},
                "sample_allocations": {"sample_key", "number"},
                "generation_jobs": {"job_id", "payload_hash", "sample_keys", "numbers", "archive", "created_at"},
            }
            for table, columns in required.items():
                available = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if not columns <= available:
                    raise BackupError("База нумерації має несумісну структуру.")
            rows = connection.execute("SELECT singleton, next_number FROM numbering_state").fetchall()
            if len(rows) != 1 or rows[0][0] != 1 or (rows[0][1] is not None and (type(rows[0][1]) is not int or rows[0][1] < 1)):
                raise BackupError("Некоректний лічильник номерів у резервній копії.")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise BackupError("Не вдалося прочитати базу нумерації з резервної копії.") from exc


def _validate_docx(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 4000 or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
                raise BackupError("Завеликий вміст Word-шаблону в резервній копії.")
            info = archive.getinfo("word/document.xml")
            if info.file_size > 10 * 1024 * 1024:
                raise BackupError("Завеликий Word-шаблон у резервній копії.")
            # Parse XML with no external entity or network resolution.
            from lxml import etree
            etree.fromstring(archive.read(info), etree.XMLParser(resolve_entities=False, no_network=True))
    except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
        raise BackupError("Пошкоджений Word-шаблон у резервній копії.") from exc
    except Exception as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("Некоректний XML Word-шаблону в резервній копії.") from exc


def _validate_json_file(relative: str, data, files: set[str]) -> None:
    normalized = relative.casefold()
    if normalized in {"templates/library.json", "assignment_templates/library.json"}:
        if not isinstance(data, list):
            raise BackupError("Некоректний покажчик бібліотеки шаблонів.")
        directory = relative.split("/", 1)[0]
        identifiers = set()
        for entry in data:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not _ID.fullmatch(entry["id"]):
                raise BackupError("Некоректний ідентифікатор шаблону в резервній копії.")
            if entry["id"] in identifiers or f"{directory}/{entry['id']}.docx" not in files:
                raise BackupError("Резервна копія містить неповну або неоднозначну бібліотеку шаблонів.")
            identifiers.add(entry["id"])
            if any(key in entry and not isinstance(entry[key], str) for key in ("name", "filename", "product_name", "category", "department", "standard", "sample_weight")):
                raise BackupError("Некоректні відомості про шаблон у резервній копії.")
            for key in ("aliases", "available_fields"):
                if key in entry and (not isinstance(entry[key], list) or any(not isinstance(value, str) for value in entry[key])):
                    raise BackupError("Некоректні поля шаблону у резервній копії.")
    elif normalized in {"templates/seeded.json", "assignment_templates/seeded.json"}:
        if not isinstance(data, list) or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in data):
            raise BackupError("Некоректний перелік початкових шаблонів.")
    elif normalized == "settings.json":
        if not isinstance(data, dict):
            raise BackupError("Некоректний довідник у резервній копії.")
        for kind in ("customers", "suppliers", "issuers"):
            entries = data.get(kind, [])
            if not isinstance(entries, list):
                raise BackupError("Некоректний довідник у резервній копії.")
            identifiers = set()
            for entry in entries:
                if (not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]
                        or entry["id"] in identifiers or not isinstance(entry.get("name"), str) or not entry["name"].strip()):
                    raise BackupError("Некоректний запис довідника у резервній копії.")
                identifiers.add(entry["id"])
                if kind == "suppliers":
                    if not isinstance(entry.get("code"), str) or not re.fullmatch(r"[0-9]{1,10}", entry["code"]):
                        raise BackupError("Некоректний код постачальника у резервній копії.")
                    if entry.get("edrpou") and (not isinstance(entry["edrpou"], str) or not re.fullmatch(r"[0-9]{8}(?:[0-9]{2})?", entry["edrpou"])):
                        raise BackupError("Некоректний ЄДРПОУ у резервній копії.")
        if not isinstance(data.get("default_issuer_id", ""), str):
            raise BackupError("Некоректний вибір особи, що видає завдання.")
    elif normalized in {'signers.json', 'appearance.json'}:
        from .preferences import SignerState, Appearance
        try:
            (SignerState if normalized == 'signers.json' else Appearance).model_validate(data)
        except ValueError as exc:
            raise BackupError('Некоректні профілі підписантів або налаштування вигляду.') from exc


class BackupStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.backup_directory = self.directory.parent / (self.directory.name + "-backups")
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.directory), RLock())

    def _files(self) -> list[tuple[str, Path]]:
        if not self.directory.exists():
            return []
        result = []
        for current, directories, names in os.walk(self.directory, followlinks=False):
            base = Path(current)
            directories[:] = [name for name in directories if name.casefold() not in EXCLUDED_DIRECTORIES]
            for name in directories:
                if (base / name).is_symlink():
                    raise BackupError("Резервна копія не підтримує символічні посилання в даних.")
            for name in names:
                source = base / name
                relative = source.relative_to(self.directory).as_posix()
                if _excluded(relative):
                    continue
                _relative(relative)
                if source.is_symlink() or not source.is_file():
                    raise BackupError("Резервна копія не підтримує спеціальні файли в даних.")
                result.append((relative, source))
        return sorted(result)

    def _write_archive(self, output: BinaryIO, *, version: str, reason: str) -> dict:
        with self.lock:
            files = self._files()
            if len(files) > MAX_FILES:
                raise BackupError("Забагато файлів для однієї резервної копії.")
            total = 0
            checksums = {}
            with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=1) as archive:
                for relative, source in files:
                    remaining = MAX_UNCOMPRESSED_BYTES - total
                    if source.stat().st_size > remaining:
                        raise BackupError("Дані перевищують ліміт резервної копії.")
                    # A committed SQLite snapshot is staged on the same volume
                    # and streamed into the ZIP like every other file.
                    snapshot = (_sqlite_snapshot(source, self.directory.parent)
                                if source.suffix.lower() == ".sqlite3" else nullcontext(source))
                    with snapshot as path, path.open("rb") as reader:
                        if path.stat().st_size > remaining:
                            raise BackupError("Дані перевищують ліміт резервної копії.")
                        with archive.open("data/" + relative, "w", force_zip64=True) as member:
                            details = _copy_with_checksum(reader, member, remaining)
                    checksums[relative] = details
                    total += details["size"]
                    if output.tell() > MAX_ARCHIVE_BYTES:
                        raise BackupError("Стиснена резервна копія перевищує ліміт.")
                manifest = {
                    "format": "oztausya-backup", "format_version": FORMAT_VERSION,
                    "app_version": version, "created_at": _now(), "reason": reason, "files": checksums,
                }
                index = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                if len(index) > MAX_MANIFEST_BYTES:
                    raise BackupError("Покажчик резервної копії завеликий.")
                archive.writestr(MANIFEST, index)
            if output.tell() > MAX_ARCHIVE_BYTES:
                raise BackupError("Стиснена резервна копія перевищує ліміт.")
            return manifest

    def export(self, *, version: str = "", reason: str = "manual") -> bytes:
        output = BytesIO()
        self._write_archive(output, version=version, reason=reason)
        return output.getvalue()

    def prepare_archive(self, *, version: str = "", reason: str = "manual") -> tuple[Path, dict]:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".backup-", suffix=".tmp", dir=self.backup_directory, delete=False) as output:
                temporary = Path(output.name)
                manifest = self._write_archive(output, version=version, reason=reason)
            return temporary, manifest
        except BaseException:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise

    def _save_copy(self, *, version: str = "", reason: str) -> dict:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"backup-{timestamp}-{uuid4().hex[:12]}.zip"
        target = self.backup_directory / filename
        temporary, manifest = self.prepare_archive(version=version, reason=reason)
        try:
            size = temporary.stat().st_size
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"filename": filename, "created_at": manifest["created_at"], "reason": manifest["reason"],
                "name": filename, "app_version": manifest["app_version"], "version": manifest["app_version"],
                "size": size, "files": len(manifest["files"])}

    def automatic(self, version: str, *, keep: int = 7) -> dict | None:
        with self.lock:
            state_path = self.backup_directory / ".automatic-state.json"
            previous = _json(state_path.read_bytes()) if state_path.exists() else {}
            if not isinstance(previous, dict):
                raise BackupError("Пошкоджено стан автоматичного резервного копіювання.")
            if previous.get("app_version") == version:
                return None
            result = self._save_copy(version=version, reason="before-version-change") if self._files() else None
            self.backup_directory.mkdir(parents=True, exist_ok=True)
            temporary = state_path.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps({"app_version": version, "checked_at": _now()}), encoding="utf-8")
                os.replace(temporary, state_path)
            finally:
                temporary.unlink(missing_ok=True)
            automatic = [entry for entry in self.status()["backups"] if entry["reason"] == "before-version-change"]
            for entry in automatic[max(1, keep):]:
                try:
                    (self.backup_directory / entry["filename"]).unlink(missing_ok=True)
                except OSError:
                    LOG.warning("Could not remove an old automatic backup", exc_info=True)
            return result

    def status(self) -> dict:
        with self.lock:
            entries = []
            if self.backup_directory.exists():
                for path in sorted(self.backup_directory.glob("backup-*.zip"), reverse=True):
                    if not _BACKUP_NAME.fullmatch(path.name) or path.is_symlink():
                        continue
                    try:
                        with ZipFile(path) as archive:
                            info = archive.getinfo(MANIFEST)
                            if info.file_size > MAX_MANIFEST_BYTES:
                                continue
                            manifest = _json(archive.read(info))
                        entries.append({"filename": path.name, "name": path.name, "created_at": manifest["created_at"],
                                        "reason": manifest["reason"], "app_version": manifest["app_version"],
                                        "version": manifest["app_version"],
                                        "size": path.stat().st_size, "files": len(manifest["files"])})
                    except (OSError, BadZipFile, KeyError, BackupError, TypeError):
                        continue
            return {"backups": entries, "latest": entries[0] if entries else None,
                    "max_archive_bytes": MAX_ARCHIVE_BYTES}

    def _unpack(self, raw: bytes | BinaryIO, stage: Path) -> dict:
        source = BytesIO(raw) if isinstance(raw, bytes) else raw
        try:
            if not 0 < source.seek(0, os.SEEK_END) <= MAX_ARCHIVE_BYTES:
                raise BackupError("Оберіть ZIP резервної копії розміром до 8 ГБ.")
            with ZipFile(source) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_FILES + 1 or len({entry.filename.casefold() for entry in entries}) != len(entries):
                    raise BackupError("Забагато або повторні файли в резервній копії.")
                manifest_info = archive.getinfo(MANIFEST)
                if sum(entry.file_size for entry in entries) - manifest_info.file_size > MAX_UNCOMPRESSED_BYTES:
                    raise BackupError("Розпаковані дані перевищують ліміт 8 ГБ.")
                if manifest_info.file_size > MAX_MANIFEST_BYTES:
                    raise BackupError("Покажчик резервної копії завеликий.")
                manifest = _json(archive.read(manifest_info))
                if not isinstance(manifest, dict) or manifest.get("format") != "oztausya-backup" or manifest.get("format_version") != FORMAT_VERSION:
                    raise BackupError("Цей ZIP не є підтримуваною резервною копією ОЗтаУСЯ.")
                files = manifest.get("files")
                if not isinstance(files, dict) or len(files) > MAX_FILES:
                    raise BackupError("Некоректний покажчик резервної копії.")
                expected_names = {MANIFEST, *("data/" + name for name in files if isinstance(name, str))}
                if {entry.filename for entry in entries} != expected_names:
                    raise BackupError("Склад ZIP не відповідає покажчику резервної копії.")
                for entry in entries:
                    if entry.flag_bits & 1 or stat.S_IFMT(entry.external_attr >> 16) in {stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK}:
                        raise BackupError("Зашифровані або спеціальні файли в резервній копії не підтримуються.")
                for name, details in files.items():
                    _relative(name)
                    if _excluded(name) or any(part.casefold() in EXCLUDED_DIRECTORIES for part in PurePosixPath(name).parts):
                        raise BackupError("Резервна копія містить файли поза даними програми.")
                    if not isinstance(details, dict) or type(details.get("size")) is not int or not 0 <= details["size"] <= MAX_UNCOMPRESSED_BYTES:
                        raise BackupError("Некоректний розмір файла в покажчику копії.")
                    info = archive.getinfo("data/" + name)
                    if info.file_size != details["size"]:
                        raise BackupError("Розмір файла не відповідає покажчику резервної копії.")
                    target = stage.joinpath(*PurePosixPath(name).parts)
                    if target.suffix.lower() == ".json" and info.file_size > MAX_JSON_BYTES:
                        raise BackupError("JSON-файл резервної копії завеликий.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as stream, target.open("xb") as output:
                        copied = _copy_with_checksum(stream, output, details["size"])
                    if copied["size"] != details["size"] or copied["sha256"] != details.get("sha256"):
                        raise BackupError("Контрольна сума файла резервної копії не збігається.")
                for name in files:
                    target = stage.joinpath(*PurePosixPath(name).parts)
                    if target.suffix.lower() == ".json":
                        _validate_json_file(name, _json(target.read_bytes()), set(files))
                    elif name.casefold() == "numbering.sqlite3":
                        _validate_numbering(target)
                    elif name.casefold() == 'outgoing.sqlite3':
                        from .outgoing import JournalStore
                        try:
                            JournalStore.validate(target)
                        except (ValueError, sqlite3.Error, KeyError) as exc:
                            raise BackupError('Некоректна база журналу вихідних листів.') from exc
                    elif name.casefold() == 'workbench.sqlite3':
                        from .workbench_store import WorkStore
                        try:
                            WorkStore.validate(target)
                        except (ValueError, sqlite3.Error, KeyError) as exc:
                            raise BackupError('Некоректна база пакетів документів та історії листів.') from exc
                    elif target.suffix.lower() == ".docx":
                        _validate_docx(target)
                return manifest
        except (BadZipFile, KeyError, OSError, RuntimeError, EOFError) as exc:
            raise BackupError("Не вдалося прочитати ZIP резервної копії.") from exc

    def restore(self, raw: bytes | BinaryIO, *, confirmed: bool, validate_staged: Callable[[Path], None] | None = None,
                after_restore: Callable[[], None] | None = None) -> dict:
        if not confirmed:
            raise BackupError("Підтвердьте заміну поточних даних резервною копією. Перед заміною буде збережено поточний стан.")
        with self.lock:
            self.directory.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=self.directory.parent))
            old = self.directory.parent / (".restore-old-" + uuid4().hex)
            failed = self.directory.parent / (".restore-failed-" + uuid4().hex)
            recovery = None
            moved_old = False
            restored = False
            try:
                manifest = self._unpack(raw, stage)
                if validate_staged is not None:
                    validate_staged(stage)
                # Preserve local runtime models/cache, never import them from ZIP.
                if self.directory.exists():
                    for source in self.directory.iterdir():
                        if source.name.casefold() in EXCLUDED_DIRECTORIES and source.is_dir():
                            shutil.copytree(source, stage / source.name, symlinks=True)
                        elif source.name.casefold().endswith(".lock") and source.is_file() and not source.is_symlink():
                            shutil.copy2(source, stage / source.name)
                recovery = self._save_copy(reason="before-restore")
                if self.directory.exists():
                    os.replace(self.directory, old)
                    moved_old = True
                try:
                    os.replace(stage, self.directory)
                    restored = True
                    if after_restore is not None:
                        after_restore()
                except BaseException:
                    if restored:
                        os.replace(self.directory, failed)
                    if moved_old:
                        os.replace(old, self.directory)
                        moved_old = False
                    if failed.exists():
                        shutil.rmtree(failed)
                    raise
                cleanup_pending = False
                if old.exists():
                    try:
                        shutil.rmtree(old)
                    except OSError:
                        # The restore has committed; a locked old file must not
                        # turn success into a misleading partial-failure report.
                        cleanup_pending = True
                moved_old = False
                return {"restored": True, "files": len(manifest["files"]), "created_at": manifest.get("created_at"),
                        "recovery_backup": recovery["filename"], "reload_required": True,
                        "cleanup_pending": cleanup_pending}
            except BackupError:
                raise
            except Exception as exc:
                message = "Не вдалося відновити копію. Початкові дані збережено."
                if moved_old:
                    message = "Не вдалося завершити відновлення. Не видаляйте резервні каталоги даних; закрийте інші копії програми."
                if recovery:
                    message += " Копія перед відновленням: " + recovery["filename"]
                raise BackupError(message) from exc
            finally:
                if stage.exists():
                    shutil.rmtree(stage)


def create_automatic_backup(directory: Path, version: str, *, keep: int = 7) -> dict | None:
    """Snapshot existing data once before initializing a different app version."""
    return BackupStore(directory).automatic(version, keep=keep)


def backup_router(directory: Path, *, validate_staged: Callable[[Path], None] | None = None,
                  after_restore: Callable[[], None] | None = None, version: str = "",
                  automatic_error: str = "") -> APIRouter:
    store = BackupStore(directory)
    router = APIRouter(prefix="/api/backups", tags=["backups"])

    @router.get("/status")
    def status():
        return {**store.status(), "automatic_error": automatic_error}

    @router.get("/export")
    def export():
        try:
            path, _ = store.prepare_archive(version=version)
        except BackupError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            LOG.warning("Manual backup could not be saved", exc_info=True)
            raise HTTPException(503, "Не вдалося зберегти резервну копію. Перевірте вільне місце та доступ до диска.") from exc
        filename = "OZtaUSYa-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".zip"
        return FileResponse(path, filename=filename, media_type="application/zip",
                            background=BackgroundTask(path.unlink, missing_ok=True))

    @router.get("/download/{filename}")
    @router.get("/files/{filename}")
    def download(filename: str):
        if not _BACKUP_NAME.fullmatch(filename):
            raise HTTPException(404, "Резервну копію не знайдено.")
        path = store.backup_directory / filename
        known = {entry["name"] for entry in store.status()["backups"]}
        if filename not in known or not path.is_file() or path.is_symlink():
            raise HTTPException(404, "Резервну копію не знайдено.")
        return FileResponse(path, filename=filename, media_type="application/zip")

    @router.post("/restore")
    async def restore(file: UploadFile = File(...), confirmed: bool = Form(False)):
        try:
            return await run_in_threadpool(store.restore, file.file, confirmed=confirmed,
                                           validate_staged=validate_staged, after_restore=after_restore)
        except BackupError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            await file.close()

    return router
