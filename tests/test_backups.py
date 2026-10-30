"""Real snapshots, archive validation, and rollback of persistent local data."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import stat
from zipfile import ZipFile, ZipInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import backups
from app.backups import BackupError, BackupStore, MANIFEST, backup_router
from app.numbering import NumberingStore


def docx():
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    return output.getvalue()


@pytest.fixture
def data(tmp_path):
    directory = tmp_path / "Дані користувача"
    directory.mkdir()
    (directory / "settings.json").write_text(json.dumps({
        "suppliers": [{"id": "supplier", "name": "ТОВ «Тест»", "code": "015", "edrpou": "00123456"}],
        "customers": [], "issuers": [], "default_issuer_id": "",
    }, ensure_ascii=False), encoding="utf-8")
    templates = directory / "templates"
    templates.mkdir()
    (templates / "library.json").write_text('[{"id":"custom","name":"Мій шаблон"}]', encoding="utf-8")
    (templates / "custom.docx").write_bytes(docx())
    (directory / "workspace-drafts.json").write_text('{"schema_version":1,"sections":{}}', encoding="utf-8")
    NumberingStore(directory / "numbering.sqlite3").initialize(1397)
    return directory


def rewritten(raw, *, replace=None, add=None, change_manifest=None):
    result = BytesIO()
    with ZipFile(BytesIO(raw)) as original, ZipFile(result, "w") as output:
        for entry in original.infolist():
            content = original.read(entry)
            if replace and entry.filename in replace:
                content = replace[entry.filename]
            if entry.filename == MANIFEST and change_manifest:
                manifest = json.loads(content)
                change_manifest(manifest)
                content = json.dumps(manifest).encode("utf-8")
            output.writestr(entry, content)
        if add:
            for name, content in add:
                output.writestr(name, content)
    return result.getvalue()


def archive_with_file(name, content=b"data", *, info=None):
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr(MANIFEST, json.dumps({
            "format": "oztausya-backup", "format_version": 1, "app_version": "1.1.0",
            "created_at": "2026-10-30T00:00:00Z", "reason": "manual",
            "files": {name: {"size": len(content), "sha256": sha256(content).hexdigest()}},
        }))
        archive.writestr(info or "data/" + name, content)
    return result.getvalue()


def test_snapshot_reads_committed_wal_and_excludes_runtime_files(data):
    connection = sqlite3.connect(data / "numbering.sqlite3")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("UPDATE numbering_state SET next_number=1401")
    connection.commit()
    (data / "tessdata").mkdir()
    (data / "tessdata/ukr.traineddata").write_bytes(b"runtime model")
    (data / ".workspace-drafts.lock").touch()
    (data / "temporary.tmp").write_text("unfinished", encoding="utf-8")
    try:
        raw = BackupStore(data).export(version="1.1.0")
    finally:
        connection.close()
    with ZipFile(BytesIO(raw)) as archive:
        names = archive.namelist()
        assert not any("tessdata" in name or name.endswith(("-wal", "-shm", ".tmp", ".lock")) for name in names)
        database = data.parent / "snapshot.sqlite3"
        database.write_bytes(archive.read("data/numbering.sqlite3"))
        with sqlite3.connect(database) as snapshot:
            assert snapshot.execute("SELECT next_number FROM numbering_state").fetchone() == (1401,)
        manifest = json.loads(archive.read(MANIFEST))
        assert manifest["app_version"] == "1.1.0"
        assert "workspace-drafts.json" in manifest["files"]


def test_restore_replaces_data_and_preserves_runtime_and_recovery_copy(data):
    store = BackupStore(data)
    original = store.export()
    NumberingStore(data / "numbering.sqlite3").set_next_number(1800)
    (data / "settings.json").write_text('{"suppliers":[],"issuers":[],"customers":[]}', encoding="utf-8")
    (data / "tessdata").mkdir()
    (data / "tessdata/eng.traineddata").write_bytes(b"verified runtime")
    (data / ".workspace-drafts.lock").write_text("lock", encoding="utf-8")
    result = store.restore(original, confirmed=True)
    assert result["restored"] is True
    assert NumberingStore(data / "numbering.sqlite3").status() == {"next_number": 1397}
    assert json.loads((data / "settings.json").read_text(encoding="utf-8"))["suppliers"][0]["edrpou"] == "00123456"
    assert (data / "tessdata/eng.traineddata").read_bytes() == b"verified runtime"
    assert (data / ".workspace-drafts.lock").read_text(encoding="utf-8") == "lock"
    recovery = store.backup_directory / result["recovery_backup"]
    assert recovery.is_file()
    with ZipFile(recovery) as archive:
        snapshot = data.parent / "previous.sqlite3"
        snapshot.write_bytes(archive.read("data/numbering.sqlite3"))
    assert NumberingStore(snapshot).status() == {"next_number": 1800}


def test_restore_requires_explicit_confirmation_without_changes(data):
    store = BackupStore(data)
    raw = store.export()
    before = (data / "settings.json").read_bytes()
    with pytest.raises(BackupError, match="Підтвердьте"):
        store.restore(raw, confirmed=False)
    assert (data / "settings.json").read_bytes() == before
    assert not store.backup_directory.exists()


@pytest.mark.parametrize("name", ["../outside.json", "/outside.json", "C:/outside.json", "a\\outside.json", "a/../outside.json", "CON.json", "a./data.json", "tessdata/eng.traineddata", "TessData/eng.traineddata", ".workspace-drafts.lock"])
def test_unsafe_paths_cannot_restore_even_with_matching_checksum(data, name):
    before = (data / "settings.json").read_bytes()
    with pytest.raises(BackupError):
        BackupStore(data).restore(archive_with_file(name), confirmed=True)
    assert (data / "settings.json").read_bytes() == before
    assert not (data.parent / "outside.json").exists()


def test_symlink_archive_entries_are_rejected(data):
    info = ZipInfo("data/link.json")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(BackupError, match="спеціальні"):
        BackupStore(data).restore(archive_with_file("link.json", info=info), confirmed=True)


def test_checksum_mismatch_cannot_replace_live_data(data):
    store = BackupStore(data)
    raw = rewritten(store.export(), replace={"data/settings.json": b"{}"})
    with pytest.raises(BackupError):
        store.restore(raw, confirmed=True)
    assert json.loads((data / "settings.json").read_text(encoding="utf-8"))["suppliers"]


@pytest.mark.parametrize("name,content", [
    ("settings.json", b"[]"),
    ("Settings.JSON", b"[]"),
    ("workspace-drafts.json", b"not json"),
    ("numbering.sqlite3", b"not sqlite"),
    ("NUMBERING.SQLITE3", b"not sqlite"),
    ("templates/library.json", b'[{"id":"../escape"}]'),
    ("templates/invalid.docx", b"not a Word document"),
])
def test_valid_checksums_do_not_bypass_content_validation(data, name, content):
    before = (data / "settings.json").read_bytes()
    with pytest.raises(BackupError):
        BackupStore(data).restore(archive_with_file(name, content), confirmed=True)
    assert (data / "settings.json").read_bytes() == before


def test_callback_failure_rolls_back_and_keeps_recovery_archive(data):
    store = BackupStore(data)
    raw = store.export()
    NumberingStore(data / "numbering.sqlite3").set_next_number(2000)

    def fail():
        assert NumberingStore(data / "numbering.sqlite3").status() == {"next_number": 1397}
        raise RuntimeError("migration failed")

    with pytest.raises(BackupError, match="Початкові дані збережено"):
        store.restore(raw, confirmed=True, after_restore=fail)
    assert NumberingStore(data / "numbering.sqlite3").status() == {"next_number": 2000}
    assert len(store.status()["backups"]) == 1
    assert not list(data.parent.glob(".restore-*"))


def test_failed_atomic_swap_rolls_back_without_losing_original(data, monkeypatch):
    store = BackupStore(data)
    raw = store.export()
    before = (data / "settings.json").read_bytes()
    replace = backups.os.replace

    def fail_stage(source, destination):
        if Path(source).name.startswith(".restore-stage-") and Path(destination) == data:
            raise OSError("simulated file lock")
        return replace(source, destination)

    monkeypatch.setattr(backups.os, "replace", fail_stage)
    with pytest.raises(BackupError):
        store.restore(raw, confirmed=True)
    assert (data / "settings.json").read_bytes() == before
    assert NumberingStore(data / "numbering.sqlite3").status() == {"next_number": 1397}
    assert store.status()["latest"]["reason"] == "before-restore"


def test_staged_validation_prevents_any_live_mutation(data):
    store = BackupStore(data)
    raw = store.export()

    def validate(stage):
        assert stage != data and (stage / "settings.json").is_file()
        raise ValueError("invalid draft schema")

    with pytest.raises(BackupError):
        store.restore(raw, confirmed=True, validate_staged=validate)
    assert not store.backup_directory.exists()
    assert NumberingStore(data / "numbering.sqlite3").status() == {"next_number": 1397}


def test_automatic_backup_is_once_per_version_and_retains_recent_versions(data):
    store = BackupStore(data)
    first = store.automatic("1.1.0", keep=2)
    assert first["reason"] == "before-version-change"
    assert store.automatic("1.1.0", keep=2) is None
    store.automatic("1.1.1", keep=2)
    store.automatic("1.1.2", keep=2)
    entries = store.status()["backups"]
    assert {entry["version"] for entry in entries} == {"1.1.1", "1.1.2"}
    assert "directory" not in store.status()


def test_routes_export_restore_and_download_only_known_local_backups(data):
    app = FastAPI()
    app.include_router(backup_router(data, version="1.1.0"))
    with TestClient(app) as client:
        exported = client.get("/api/backups/export")
        assert exported.status_code == 200 and exported.content.startswith(b"PK")
        assert client.post("/api/backups/restore", files={"file": ("copy.zip", exported.content)}).status_code == 422
        restored = client.post("/api/backups/restore", files={"file": ("copy.zip", exported.content)}, data={"confirmed": "true"})
        assert restored.status_code == 200, restored.text
        entries = client.get("/api/backups/status").json()["backups"]
        assert len(entries) == 1
        assert client.get("/api/backups/files/" + entries[0]["name"]).status_code == 200
        assert client.get("/api/backups/files/not-a-backup.zip").status_code == 404
