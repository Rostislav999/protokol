"""An explicit reset affects future samples and never rewrites issued files."""
import json
import sqlite3

import pytest

from app.numbering import MAX_SAMPLE_NUMBER, NumberingError, NumberingStore


def generate(store, job, keys, start=None, builder=None):
    return store.generate_with_numbers(job, keys, start,
                                       builder or (lambda numbers: (job + ":" + ",".join(numbers)).encode()),
                                       payload_hash=job)


def make_legacy_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE numbering_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                next_number INTEGER CHECK (next_number IS NULL OR next_number > 0)
            );
            INSERT INTO numbering_state VALUES (1, 1402);
            CREATE TABLE sample_allocations (
                sample_key TEXT PRIMARY KEY,
                number INTEGER UNIQUE NOT NULL CHECK (number > 0)
            );
            INSERT INTO sample_allocations VALUES ('old-a', 1400), ('old-b', 1401);
            CREATE INDEX allocation_number_lookup ON sample_allocations(number);
            CREATE TRIGGER reject_blocked_sample BEFORE INSERT ON sample_allocations
                WHEN NEW.sample_key = 'blocked'
                BEGIN SELECT RAISE(ABORT, 'blocked sample'); END;
            CREATE TABLE generation_jobs (
                job_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                sample_keys TEXT NOT NULL,
                numbers TEXT NOT NULL,
                archive BLOB NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        connection.execute("INSERT INTO generation_jobs(job_id,payload_hash,sample_keys,numbers,archive) "
                           "VALUES (?,?,?,?,?)",
                           ("old-job", "old-hash", json.dumps(["old-a"], separators=(",", ":")),
                            '["1400"]', sqlite3.Binary(b"original-issued-zip")))


def test_lower_start_and_explicit_reuse_survive_restart_without_changing_old_files(tmp_path):
    path = tmp_path / "numbering.sqlite3"
    store = NumberingStore(path)
    original = generate(store, "original", ["old-a", "old-b"], start=500)
    assert store.set_next_number(90) == {"next_number": 90}
    assert generate(store, "lower-series", ["low-a", "low-b"])[1] == ["90", "91"]
    restarted = NumberingStore(path)
    assert restarted.status() == {"next_number": 92}
    assert restarted.set_next_number(500) == {"next_number": 500}
    assert generate(restarted, "reused-series", ["new-a", "new-b"])[1] == ["500", "501"]
    assert restarted.get_sample_numbers(["old-a", "old-b", "new-a", "new-b"]) == {
        "old-a": "500", "old-b": "501", "new-a": "500", "new-b": "501"}
    assert restarted.get_cached("original", ["old-a", "old-b"], payload_hash="original") == original
    assert generate(restarted, "corrected-original", ["old-a", "old-b"])[1] == ["500", "501"]
    assert restarted.status() == {"next_number": 502}


def test_legacy_migration_keeps_allocations_archives_indexes_and_triggers(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    make_legacy_database(path)
    store = NumberingStore(path)
    assert store.status() == {"next_number": 1402}
    assert store.get_sample_numbers(["old-a", "old-b"]) == {"old-a": "1400", "old-b": "1401"}
    assert store.get_cached("old-job", ["old-a"], payload_hash="old-hash") == (b"original-issued-zip", ["1400"])
    store.set_next_number(1400)
    assert generate(store, "manual-reuse", ["new-a"])[1] == ["1400"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='allocation_number_lookup'").fetchone()
    with pytest.raises(sqlite3.DatabaseError, match="blocked sample"):
        generate(store, "blocked-job", ["blocked"])
    assert store.status() == {"next_number": 1401}
    assert store.get_sample_numbers(["blocked"]) == {}
    assert NumberingStore(path).get_cached("old-job", ["old-a"], payload_hash="old-hash") == (b"original-issued-zip", ["1400"])


def test_migration_failure_rolls_back_schema_and_all_existing_data(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    make_legacy_database(path)
    migrate = NumberingStore._allow_manual_number_reuse

    def interrupted(connection):
        migrate(connection)
        raise RuntimeError("migration interrupted")

    monkeypatch.setattr(NumberingStore, "_allow_manual_number_reuse", staticmethod(interrupted))
    with pytest.raises(RuntimeError, match="migration interrupted"):
        NumberingStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT sample_key,number FROM sample_allocations ORDER BY sample_key").fetchall() == [
            ("old-a", 1400), ("old-b", 1401)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO sample_allocations VALUES ('still-unique', 1400)")
        assert connection.execute("SELECT archive FROM generation_jobs WHERE job_id='old-job'").fetchone()[0] == b"original-issued-zip"
    monkeypatch.setattr(NumberingStore, "_allow_manual_number_reuse", staticmethod(migrate))
    assert NumberingStore(path).set_next_number(1) == {"next_number": 1}


def test_failed_generation_after_lower_reset_does_not_consume_requested_number(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    original = generate(store, "issued", ["old"], start=200)
    store.set_next_number(10)

    def failed(_):
        raise ValueError("broken Word form")

    with pytest.raises(ValueError, match="broken Word form"):
        generate(store, "failed", ["new"], builder=failed)
    assert store.status() == {"next_number": 10}
    assert store.get_sample_numbers(["new"]) == {}
    assert store.get_cached("issued", ["old"], payload_hash="issued") == original
    assert generate(store, "retry", ["new"])[1] == ["10"]


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.2, "90", None, MAX_SAMPLE_NUMBER + 1])
def test_invalid_explicit_resets_never_change_existing_counter(tmp_path, value):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    store.initialize(1400)
    with pytest.raises(NumberingError):
        store.set_next_number(value)
    assert store.status() == {"next_number": 1400}
