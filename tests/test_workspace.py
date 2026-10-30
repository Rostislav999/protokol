"""Drafts survive restarts and reject stale writes without losing saved work."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import workspace
from app.workspace import DraftStore, WorkspaceError, workspace_router


def client_for(directory):
    application = FastAPI()
    application.include_router(workspace_router(directory))
    return TestClient(application)


def test_reading_empty_workspace_does_not_create_files(tmp_path):
    directory = tmp_path / "Чернетки"
    with client_for(directory) as client:
        result = client.get("/api/workspace/drafts")
        assert result.status_code == 200
        assert result.json() == {"schema_version": 1, "sections": {}, "revisions": {}}
    assert not list(directory.iterdir())


def test_drafts_persist_unicode_zeroes_and_manual_flags_across_restart(tmp_path):
    data = {"source": "Акт відбору.pdf", "common": {"supplier": "00123456"},
            "items": [{"id": "один", "product_name": "Свинина Ω", "_protocol_number_manual": True}]}
    with client_for(tmp_path) as first:
        saved = first.put("/api/workspace/drafts/acts", json={"data": data, "expected_revision": 0})
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision"] == 1
        assert saved.json()["updated_at"].endswith("Z")
    with client_for(tmp_path) as restarted:
        result = restarted.get("/api/workspace/drafts").json()
        assert result["sections"]["acts"]["data"] == data
        assert result["revisions"] == {"acts": 1}
    assert "Свинина Ω" in (tmp_path / "workspace-drafts.json").read_text(encoding="utf-8")


def test_saved_approvals_are_reset_at_every_level(tmp_path):
    store = DraftStore(tmp_path)
    saved = store.save("excel", {"confirmed": True, "records": [{"reviewConfirmed": True}],
                                  "settings": {"review_confirmed": True, "enabled": True}})
    assert saved["data"] == {"confirmed": False, "records": [{"reviewConfirmed": False}],
                             "settings": {"review_confirmed": False, "enabled": True}}
    assert store.all()["sections"]["excel"] == saved


def test_stale_window_cannot_overwrite_or_delete_newer_work(tmp_path):
    with client_for(tmp_path) as client:
        first = client.put("/api/workspace/drafts/acts", json={"data": {"text": "перша"}, "expected_revision": 0})
        assert first.status_code == 200
        stale = client.put("/api/workspace/drafts/acts", json={"data": {"text": "застаріла"}, "expected_revision": 0})
        assert stale.status_code == 409
        assert client.delete("/api/workspace/drafts/acts?expected_revision=0").status_code == 409
        saved = client.get("/api/workspace/drafts").json()["sections"]["acts"]
        assert saved["data"]["text"] == "перша"
        assert saved["revision"] == 1


def test_delete_keeps_tombstone_revision_and_cannot_resurrect_stale_snapshot(tmp_path):
    store = DraftStore(tmp_path)
    store.save("acts", {"text": "saved"}, 0)
    removed = store.delete("acts", 1)
    assert removed["deleted"] and removed["revision"] == 2
    state = DraftStore(tmp_path).all()
    assert state["sections"] == {}
    assert state["revisions"] == {"acts": 2}
    with pytest.raises(WorkspaceError, match="іншому вікні"):
        store.save("acts", {"text": "stale"}, 0)
    assert store.save("acts", {"text": "new"}, 2)["revision"] == 3


def test_parallel_writers_accept_only_one_matching_revision(tmp_path):
    stores = [DraftStore(tmp_path), DraftStore(tmp_path)]

    def save(index):
        try:
            return stores[index].save("acts", {"writer": index}, 0)
        except WorkspaceError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 1
    assert stores[0].all()["revisions"]["acts"] == 1


@pytest.mark.parametrize("raw", [b"{bad json", b"", b"[]", b'{"schema_version":2,"sections":{}}',
    b'{"schema_version":1,"sections":{"acts":{"data":{},"revision":true,"updated_at":"bad"}}}',
    b'{"schema_version":1,"sections":{},"revisions":{"acts":NaN}}'])
def test_unreadable_or_future_drafts_are_preserved_on_every_operation(tmp_path, raw):
    path = tmp_path / "workspace-drafts.json"
    path.write_bytes(raw)
    with client_for(tmp_path) as client:
        assert client.get("/api/workspace/drafts").status_code == 409
        assert client.put("/api/workspace/drafts/acts", json={"data": {}}).status_code == 409
        assert client.delete("/api/workspace/drafts/acts").status_code == 409
    assert path.read_bytes() == raw


def test_compatible_unknown_sections_and_metadata_are_preserved(tmp_path):
    original = {"schema_version": 1, "sections": {"extension": {"future": "keep"}}, "extra": {"keep": True}}
    (tmp_path / "workspace-drafts.json").write_text(json.dumps(original), encoding="utf-8")
    store = DraftStore(tmp_path)
    store.save("acts", {"text": "new"}, 0)
    state = store.all()
    assert state["sections"]["extension"] == {"future": "keep"}
    assert state["extra"] == {"keep": True}


@pytest.mark.parametrize("section", ["other", "__proto__", "settings.json"])
def test_unknown_sections_are_rejected(tmp_path, section):
    with client_for(tmp_path) as client:
        assert client.put(f"/api/workspace/drafts/{section}", json={"data": {}}).status_code == 404
        assert client.delete(f"/api/workspace/drafts/{section}").status_code == 404
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("data", [{"items": [{}] * 101}, {"records": [{}] * 101}, {"rows": [{}] * 101}])
def test_drafts_observe_existing_hundred_row_limit(tmp_path, data):
    with client_for(tmp_path) as client:
        assert client.put("/api/workspace/drafts/acts", json={"data": data}).status_code == 422
    assert not (tmp_path / "workspace-drafts.json").exists()


def test_oversized_section_does_not_replace_existing_work(tmp_path, monkeypatch):
    store = DraftStore(tmp_path)
    store.save("acts", {"text": "kept"}, 0)
    before = store.path.read_bytes()
    monkeypatch.setattr(workspace, "MAX_SECTION_BYTES", 100)
    with client_for(tmp_path) as client:
        result = client.put("/api/workspace/drafts/acts", json={"data": {"text": "x" * 101}, "expected_revision": 1})
        assert result.status_code == 413
    assert store.path.read_bytes() == before


def test_nonfinite_and_excessively_nested_data_are_rejected(tmp_path):
    store = DraftStore(tmp_path)
    with pytest.raises(WorkspaceError, match="NaN"):
        store.save("acts", {"invalid": float("nan")})
    with pytest.raises(WorkspaceError, match="NaN"):
        store.save("acts", {"confirmed": float("nan")})
    nested = {}
    for _ in range(workspace.MAX_DEPTH + 1):
        nested = {"nested": nested}
    with pytest.raises(WorkspaceError, match="вкладена"):
        store.save("acts", nested)
    assert not list(tmp_path.iterdir())


def test_interrupted_replacement_leaves_old_json_and_removes_temporary_file(tmp_path, monkeypatch):
    store = DraftStore(tmp_path)
    store.save("acts", {"text": "old"}, 0)
    original = store.path.read_bytes()

    def failed_replace(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(workspace.os, "replace", failed_replace)
    with pytest.raises(OSError, match="disk failure"):
        store.save("acts", {"text": "new"}, 1)
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("revision", [-1, True, "0", 1.5])
def test_invalid_revision_values_are_rejected(tmp_path, revision):
    with client_for(tmp_path) as client:
        assert client.put("/api/workspace/drafts/acts", json={"data": {}, "expected_revision": revision}).status_code == 422
