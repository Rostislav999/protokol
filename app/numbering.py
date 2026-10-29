"""Transactional, persistent sample numbers and idempotent document downloads.

Number previews never reserve numbers. A generation commits its numbers and ZIP
only after the document builder succeeds, so a failed Word render cannot consume
the next sample number. Every department receives the same number for a sample.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Callable, Iterator

MAX_SAMPLE_NUMBER = 2_147_483_647
MAX_SAMPLES_PER_JOB = 100
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024


class NumberingError(ValueError):
    """A numbering request needs user correction, rather than silent renumbering."""


def _number(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAMPLE_NUMBER:
        raise NumberingError(f"Номер зразка має бути цілим числом від 1 до {MAX_SAMPLE_NUMBER}.")
    return value


def _identifier(value: str, label: str, *, maximum: int = 200) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 for char in value)):
        raise NumberingError(f"Некоректний {label}.")
    return value


def _job_keys(job_id: str, sample_keys: list[str], payload_hash: str) -> str:
    """Validate the identity shared by cached reads and new generations."""
    _identifier(job_id, "ідентифікатор формування")
    _identifier(payload_hash, "відбиток даних формування", maximum=256)
    if not isinstance(sample_keys, list) or not 1 <= len(sample_keys) <= MAX_SAMPLES_PER_JOB:
        raise NumberingError(f"Потрібно від 1 до {MAX_SAMPLES_PER_JOB} записів зразків.")
    for key in sample_keys:
        _identifier(key, "ідентифікатор зразка")
    return json.dumps(sample_keys, ensure_ascii=False, separators=(",", ":"))


def _cached_result(previous, serialized_keys: str, payload_hash: str) -> tuple[bytes, list[str]] | None:
    if previous is None:
        return None
    if previous[0] != payload_hash or previous[1] != serialized_keys:
        raise NumberingError("Це формування вже збережено з іншими даними. Створіть нове формування для змінених даних.")
    return bytes(previous[3]), json.loads(previous[2])


class NumberingStore:
    """SQLite store shared safely by requests, threads and restarted servers.

    ``sample_keys`` are persistent sample identities, independent of a document
    generation or department. Adding another department or correcting a draft
    retains the sample's original number; only previously unseen identities
    reserve new numbers. Reuse ``job_id`` and the payload fingerprint to download
    the exact same generation again, including its original issue date.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS numbering_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    next_number INTEGER CHECK (next_number IS NULL OR next_number > 0)
                )
            """)
            connection.execute("INSERT OR IGNORE INTO numbering_state(singleton, next_number) VALUES (1, NULL)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS sample_allocations (
                    sample_key TEXT PRIMARY KEY,
                    number INTEGER NOT NULL CHECK (number > 0)
                )
            """)
            self._allow_manual_number_reuse(connection)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    job_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    sample_keys TEXT NOT NULL,
                    numbers TEXT NOT NULL,
                    archive BLOB NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    @staticmethod
    def _allow_manual_number_reuse(connection: sqlite3.Connection) -> None:
        """Upgrade old databases without changing issued sample identities.

        The counter still advances atomically during normal generation. An
        explicit starting-number reset may intentionally reuse an earlier
        number, so only the old uniqueness constraint on ``number`` is removed.
        Both this migration and all data copies share the startup transaction.
        """
        unique_numbers = set()
        for index in connection.execute("PRAGMA index_list(sample_allocations)").fetchall():
            if index[2] and [row[2] for row in connection.execute(
                    'PRAGMA index_info("' + index[1].replace('"', '""') + '")').fetchall()] == ["number"]:
                unique_numbers.add(index[1])
        if not unique_numbers:
            return
        # Preserve explicit indexes and triggers unrelated to number uniqueness.
        objects = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE tbl_name = 'sample_allocations' "
            "AND type IN ('index', 'trigger') AND sql IS NOT NULL"
        ).fetchall()
        connection.execute("""
            CREATE TABLE sample_allocations_manual_numbers (
                sample_key TEXT PRIMARY KEY,
                number INTEGER NOT NULL CHECK (number > 0)
            )
        """)
        connection.execute("INSERT INTO sample_allocations_manual_numbers(sample_key, number) "
                           "SELECT sample_key, number FROM sample_allocations")
        connection.execute("DROP TABLE sample_allocations")
        connection.execute("ALTER TABLE sample_allocations_manual_numbers RENAME TO sample_allocations")
        for name, sql in objects:
            if name not in unique_numbers:
                connection.execute(sql)

    def _connect(self) -> sqlite3.Connection:
        # One connection per operation avoids sqlite's cross-thread connection
        # restrictions; BEGIN IMMEDIATE serializes only the generation writes.
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _next(connection: sqlite3.Connection) -> int | None:
        return connection.execute("SELECT next_number FROM numbering_state WHERE singleton = 1").fetchone()[0]

    def status(self) -> dict[str, int | None]:
        """Read the next number without reserving or incrementing it."""
        connection = self._connect()
        try:
            return {"next_number": self._next(connection)}
        finally:
            connection.close()

    @staticmethod
    def _sample_numbers(connection: sqlite3.Connection, sample_keys: list[str]) -> dict[str, str]:
        if not sample_keys:
            return {}
        keys = list(dict.fromkeys(sample_keys))
        placeholders = ",".join("?" for _ in keys)
        rows = connection.execute(
            f"SELECT sample_key, number FROM sample_allocations WHERE sample_key IN ({placeholders})",
            keys,
        ).fetchall()
        found = {key: str(number) for key, number in rows}
        return {key: found[key] for key in keys if key in found}

    def get_sample_numbers(self, sample_keys: list[str]) -> dict[str, str]:
        """Read assigned numbers for a preview; unknown samples stay unreserved."""
        if not isinstance(sample_keys, list) or len(sample_keys) > MAX_SAMPLES_PER_JOB:
            raise NumberingError(f"Не більше {MAX_SAMPLES_PER_JOB} записів зразків.")
        for key in sample_keys:
            _identifier(key, "ідентифікатор зразка")
        connection = self._connect()
        try:
            return self._sample_numbers(connection, sample_keys)
        finally:
            connection.close()

    def initialize(self, next_number: int) -> dict[str, int | None]:
        """Set the starting number once; repeating the same setup is harmless."""
        value = _number(next_number)
        with self._transaction() as connection:
            current = self._next(connection)
            if current is not None and current != value:
                raise NumberingError("Лічильник уже налаштовано. Для зміни використайте явне налаштування наступного номера.")
            connection.execute("UPDATE numbering_state SET next_number = ? WHERE singleton = 1", (value,))
            return {"next_number": value}

    def set_next_number(self, value: int) -> dict[str, int | None]:
        """Explicitly set any positive starting number for future new samples.

        Existing sample allocations and cached archives retain their original
        numbers. Choosing an earlier value deliberately permits number reuse.
        """
        value = _number(value)
        with self._transaction() as connection:
            connection.execute("UPDATE numbering_state SET next_number = ? WHERE singleton = 1", (value,))
            return {"next_number": value}

    def get_cached(
        self,
        job_id: str,
        sample_keys: list[str],
        *,
        payload_hash: str,
    ) -> tuple[bytes, list[str]] | None:
        """Read an existing generation before consulting mutable templates.

        A matching cached archive remains downloadable after a template is
        edited or removed and keeps its original issue date. This operation
        neither reserves numbers nor changes the counter. A previously unused
        job ID returns ``None``; conflicting reuse is rejected consistently with
        :meth:`generate_with_numbers`.
        """
        serialized_keys = _job_keys(job_id, sample_keys, payload_hash)
        connection = self._connect()
        try:
            previous = connection.execute(
                "SELECT payload_hash, sample_keys, numbers, archive FROM generation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return _cached_result(previous, serialized_keys, payload_hash)
        finally:
            connection.close()

    def generate_with_numbers(
        self,
        job_id: str,
        sample_keys: list[str],
        start_number: int | None,
        builder: Callable[[list[str]], bytes],
        *,
        payload_hash: str,
    ) -> tuple[bytes, list[str]]:
        """Build and cache documents atomically with their assigned numbers.

        ``sample_keys`` must remain stable when correcting the same samples or
        adding department documents. Use new identities for genuinely new
        samples. Existing identities retain their numbers across different jobs.

        ``payload_hash`` must fingerprint all generation inputs, including
        template selections and user edits. A job ID cannot be reused for
        different inputs. Do not include a newly calculated current date in a
        retry fingerprint; the stored ZIP preserves its original issue date.

        If supplied, ``start_number`` initializes the store or verifies a UI
        preview is still current. A stale preview is rejected instead of moving
        the counter backwards or quietly changing visible numbers.
        """
        serialized_keys = _job_keys(job_id, sample_keys, payload_hash)
        if start_number is not None:
            _number(start_number)
        if not callable(builder):
            raise TypeError("builder must be callable")
        with self._transaction() as connection:
            previous = connection.execute(
                "SELECT payload_hash, sample_keys, numbers, archive FROM generation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            cached = _cached_result(previous, serialized_keys, payload_hash)
            if cached is not None:
                return cached

            unique_keys = list(dict.fromkeys(sample_keys))
            assigned = self._sample_numbers(connection, unique_keys)
            new_keys = [key for key in unique_keys if key not in assigned]
            current = self._next(connection)
            if current is None:
                if start_number is None:
                    raise NumberingError("Спочатку вкажіть початковий номер зразка.")
                current = start_number
            elif start_number is not None and start_number != current:
                raise NumberingError(f"Наступний номер уже змінився на {current}. Оновіть попередній перегляд перед формуванням.")

            if new_keys and current + len(new_keys) - 1 > MAX_SAMPLE_NUMBER:
                raise NumberingError("Досягнуто межу нумерації зразків.")
            assigned.update({key: str(current + index) for index, key in enumerate(new_keys)})
            numbers = [assigned[key] for key in sample_keys]
            # The counter is unchanged if rendering, validation, or saving fails.
            archive = builder(list(numbers))
            if not isinstance(archive, (bytes, bytearray)) or not archive:
                raise NumberingError("Формування не повернуло готовий архів. Номери не використані.")
            archive = bytes(archive)
            if len(archive) > MAX_ARCHIVE_BYTES:
                raise NumberingError("Архів перевищує 25 МБ. Розділіть акт на менші формування; номери не використані.")
            connection.executemany(
                "INSERT INTO sample_allocations(sample_key, number) VALUES (?, ?)",
                [(key, int(assigned[key])) for key in new_keys],
            )
            connection.execute(
                "INSERT INTO generation_jobs(job_id, payload_hash, sample_keys, numbers, archive) VALUES (?, ?, ?, ?, ?)",
                (job_id, payload_hash, serialized_keys,
                 json.dumps(numbers, separators=(",", ":")), sqlite3.Binary(archive)),
            )
            connection.execute("UPDATE numbering_state SET next_number = ? WHERE singleton = 1",
                               (current + len(new_keys),))
            return archive, numbers
