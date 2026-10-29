from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading

import pytest

from app.numbering import MAX_SAMPLE_NUMBER, NumberingError, NumberingStore


def build(numbers):
    return ("archive:" + ",".join(numbers)).encode()


def generate(store, job="job-1", keys=None, start=None, builder=build, fingerprint="payload-1"):
    return store.generate_with_numbers(job, keys or ["sample-a"], start, builder, payload_hash=fingerprint)


def test_preview_is_read_only_and_initial_number_is_required(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    assert store.status() == {"next_number": None}
    assert store.status() == {"next_number": None}
    with pytest.raises(NumberingError, match="початковий"):
        generate(store)
    assert store.status() == {"next_number": None}


def test_numbers_are_shared_across_departments_and_advance_per_sample(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    archive, numbers = generate(store, keys=["a", "a", "a", "b", "b", "b"], start=1330)
    assert numbers == ["1330", "1330", "1330", "1331", "1331", "1331"]
    assert archive == build(numbers)
    assert store.status() == {"next_number": 1332}
    assert generate(store, "job-2", ["c"])[1] == ["1332"]


def test_failed_build_does_not_initialize_or_consume_numbers(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    def failure(numbers):
        assert numbers == ["700"]
        raise ValueError("Invalid selected template")
    with pytest.raises(ValueError, match="Invalid selected"):
        generate(store, start=700, builder=failure)
    assert store.status() == {"next_number": None}
    assert generate(store, start=700)[1] == ["700"]
    def later_failure(numbers):
        raise RuntimeError("Document rendering failed")
    with pytest.raises(RuntimeError):
        generate(store, "job-2", ["sample-b"], builder=later_failure)
    assert store.status() == {"next_number": 701}
    assert generate(store, "job-2", ["sample-b"])[1] == ["701"]


def test_repeat_download_is_cached_across_restarts_without_running_builder(tmp_path):
    path = tmp_path / "numbering.sqlite3"
    original = generate(NumberingStore(path), start=1330)
    def forbidden(_):
        pytest.fail("Cached download re-rendered")
    restarted = NumberingStore(path)
    assert generate(restarted, start=1330, builder=forbidden) == original
    assert restarted.status() == {"next_number": 1331}


@pytest.mark.parametrize("fingerprint,keys", [("changed-data", ["sample-a"]), ("payload-1", ["different-sample"])])
def test_same_job_id_cannot_silently_change_payload_or_sample_identity(tmp_path, fingerprint, keys):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    generate(store, start=100)
    with pytest.raises(NumberingError, match="іншими даними"):
        generate(store, keys=keys, fingerprint=fingerprint)
    assert store.status() == {"next_number": 101}


def test_stale_preview_is_rejected_without_counter_change(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    store.initialize(1330)
    generate(store, start=1330)
    with pytest.raises(NumberingError, match="1331"):
        generate(store, "job-2", start=1330)
    assert store.status() == {"next_number": 1331}


def test_explicit_counter_setting_can_move_both_directions_and_initialization_is_repeatable(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    assert store.initialize(100) == {"next_number": 100}
    assert store.initialize(100) == {"next_number": 100}
    with pytest.raises(NumberingError, match="налаштовано"):
        store.initialize(101)
    generate(store)
    assert store.set_next_number(100) == {"next_number": 100}
    assert generate(store, "manual-reset", ["reset-sample"])[1] == ["100"]
    assert store.get_sample_numbers(["sample-a", "reset-sample"]) == {"sample-a": "100", "reset-sample": "100"}
    assert store.set_next_number(250) == {"next_number": 250}
    assert generate(store, "job-2", ["sample-b"])[1] == ["250"]


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "1330", None, MAX_SAMPLE_NUMBER + 1])
def test_invalid_initial_numbers_are_rejected(tmp_path, value):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    with pytest.raises(NumberingError):
        store.initialize(value)
    assert store.status() == {"next_number": None}


def test_number_limit_does_not_partially_allocate(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    store.initialize(MAX_SAMPLE_NUMBER)
    with pytest.raises(NumberingError, match="межу"):
        generate(store, keys=["a", "b"])
    assert store.status() == {"next_number": MAX_SAMPLE_NUMBER}


@pytest.mark.parametrize("output", [None, b"", "not bytes"])
def test_invalid_builder_result_does_not_consume_numbers(tmp_path, output):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    with pytest.raises(NumberingError, match="готовий архів"):
        generate(store, start=100, builder=lambda _: output)
    assert store.status() == {"next_number": None}


def test_builder_cannot_change_stored_number_list(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    def mutating(numbers):
        numbers[0] = "WRONG"
        return b"archive"
    assert generate(store, start=100, builder=mutating)[1] == ["100"]
    assert generate(store, start=100)[1] == ["100"]


def test_simultaneous_jobs_receive_disjoint_numbers(tmp_path):
    path = tmp_path / "numbering.sqlite3"
    NumberingStore(path).initialize(1330)
    barrier = threading.Barrier(6)
    def worker(index):
        store = NumberingStore(path)
        barrier.wait(timeout=10)
        return generate(store, f"job-{index}", [f"sample-{index}"])[1][0]
    with ThreadPoolExecutor(max_workers=6) as pool:
        numbers = list(pool.map(worker, range(6)))
    assert set(numbers) == {str(n) for n in range(1330, 1336)}
    assert NumberingStore(path).status() == {"next_number": 1336}


def test_simultaneous_retry_runs_builder_only_once(tmp_path):
    path = tmp_path / "numbering.sqlite3"
    NumberingStore(path).initialize(50)
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    calls = []
    def builder(numbers):
        with lock:
            calls.append(numbers)
        return b"one-archive"
    def worker(_):
        store = NumberingStore(path)
        barrier.wait(timeout=10)
        return generate(store, builder=builder)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(worker, range(4)))
    assert outputs == [(b"one-archive", ["50"])] * 4
    assert calls == [["50"]]
    assert NumberingStore(path).status() == {"next_number": 51}


def test_database_saving_failure_rolls_back_counter_and_job(tmp_path):
    path = tmp_path / "numbering.sqlite3"
    store = NumberingStore(path)
    store.initialize(1330)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TRIGGER reject_job BEFORE INSERT ON generation_jobs BEGIN SELECT RAISE(ABORT, 'disk failure simulation'); END")
    connection.close()
    with pytest.raises(sqlite3.DatabaseError):
        generate(store)
    assert store.status() == {"next_number": 1330}
    assert store.get_sample_numbers(["sample-a"]) == {}
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER reject_job")
    connection.close()
    assert generate(store)[1] == ["1330"]


def test_cached_archive_survives_removed_template_and_restart_without_counter_change(tmp_path):
    path = tmp_path / "numbering.sqlite3"
    source = tmp_path / "template.docx"
    source.write_bytes(b"original template, original issue date")
    store = NumberingStore(path)
    original = generate(store, start=1330, builder=lambda _: source.read_bytes())
    source.unlink()
    restarted = NumberingStore(path)
    assert restarted.get_cached("job-1", ["sample-a"], payload_hash="payload-1") == original
    assert restarted.status() == {"next_number": 1331}


def test_cache_miss_is_read_only_and_does_not_initialize_numbering(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    assert store.get_cached("job-1", ["sample-a"], payload_hash="payload-1") is None
    assert store.status() == {"next_number": None}


@pytest.mark.parametrize("fingerprint,keys", [("changed-data", ["sample-a"]), ("payload-1", ["different-sample"])])
def test_cache_lookup_rejects_conflicting_request_identity(tmp_path, fingerprint, keys):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    generate(store, start=1330)
    with pytest.raises(NumberingError, match="іншими даними"):
        store.get_cached("job-1", keys, payload_hash=fingerprint)
    assert store.status() == {"next_number": 1331}


@pytest.mark.parametrize("job,keys,fingerprint", [("", ["sample-a"], "payload-1"), ("job-1", [], "payload-1"), ("job-1", [""], "payload-1"), ("job-1", ["sample-a"], "")])
def test_cache_lookup_validates_request_before_reading_database(tmp_path, job, keys, fingerprint):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    with pytest.raises(NumberingError):
        store.get_cached(job, keys, payload_hash=fingerprint)
    assert store.status() == {"next_number": None}


def test_adding_department_with_new_job_retains_original_sample_number(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    first = generate(store, "vet-job", ["sample-a"], start=1330)
    assert first[1] == ["1330"]
    second = generate(store, "chemistry-job", ["sample-a"], start=1331, fingerprint="chemistry")
    assert second[1] == ["1330"]
    assert store.status() == {"next_number": 1331}
    assert store.get_sample_numbers(["sample-a"]) == {"sample-a": "1330"}


def test_mixed_old_and_new_samples_only_allocate_numbers_for_new_identities(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    assert generate(store, "job-a", ["a", "b"], start=100)[1] == ["100", "101"]
    assert generate(store, "job-b", ["b", "c", "a", "c", "d"], start=102)[1] == ["101", "102", "100", "102", "103"]
    assert store.status() == {"next_number": 104}
    assert store.get_sample_numbers(["d", "a", "new", "b"]) == {"d": "103", "a": "100", "b": "101"}


def test_sample_numbers_persist_across_restart_and_new_jobs(tmp_path):
    path = tmp_path / "numbering.sqlite3"
    generate(NumberingStore(path), "old-job", ["same-sample"], start=1397)
    restarted = NumberingStore(path)
    assert restarted.get_sample_numbers(["same-sample"]) == {"same-sample": "1397"}
    assert generate(restarted, "new-job", ["same-sample"], fingerprint="edited-data")[1] == ["1397"]
    assert restarted.status() == {"next_number": 1398}


def test_failed_mixed_generation_does_not_reserve_new_sample_identity(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    generate(store, "original", ["existing"], start=200)
    def broken(numbers):
        assert numbers == ["200", "201"]
        raise ValueError("Template failed")
    with pytest.raises(ValueError):
        generate(store, "mixed", ["existing", "new"], builder=broken)
    assert store.get_sample_numbers(["existing", "new"]) == {"existing": "200"}
    assert store.status() == {"next_number": 201}
    assert generate(store, "mixed", ["existing", "new"])[1] == ["200", "201"]


def test_preview_of_assigned_numbers_never_reserves_unknown_samples(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    assert store.get_sample_numbers([]) == {}
    assert store.get_sample_numbers(["unknown"]) == {}
    assert store.status() == {"next_number": None}


def test_concurrent_different_jobs_for_same_sample_share_one_number(tmp_path):
    path = tmp_path / "numbering.sqlite3"
    NumberingStore(path).initialize(1330)
    barrier = threading.Barrier(4)
    def worker(index):
        store = NumberingStore(path)
        barrier.wait(timeout=10)
        return generate(store, f"department-{index}", ["one-sample"], fingerprint=f"department-{index}")[1]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(worker, range(4))) == [["1330"]] * 4
    assert NumberingStore(path).status() == {"next_number": 1331}


def test_already_numbered_sample_can_be_rendered_when_number_range_is_exhausted(tmp_path):
    store = NumberingStore(tmp_path / "numbering.sqlite3")
    generate(store, "last-number", ["last-sample"], start=MAX_SAMPLE_NUMBER)
    assert store.status() == {"next_number": MAX_SAMPLE_NUMBER + 1}
    assert generate(store, "another-dept", ["last-sample"])[1] == [str(MAX_SAMPLE_NUMBER)]
    assert store.status() == {"next_number": MAX_SAMPLE_NUMBER + 1}
