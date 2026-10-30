"""Serialize data access across windows, including streamed response bodies."""
from contextlib import contextmanager
import os
from pathlib import Path
from threading import Lock
import anyio

from starlette.concurrency import run_in_threadpool

_locks: dict[str, Lock] = {}
_registry_lock = Lock()


class _Lease:
    def __init__(self, handle, lock):
        self.handle, self.lock = handle, lock

    def close(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
            self.lock.release()


def acquire(directory: Path) -> _Lease:
    # The lock must survive atomic replacement of the data directory on restore.
    parent = directory.parent / (directory.name + "-backups")
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / ".data-access.lock"
    with _registry_lock:
        lock = _locks.setdefault(str(path.resolve()), Lock())
    lock.acquire()
    handle = None
    try:
        handle = path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            # LK_LOCK retries only ten times. Keep waiting for another window's
            # legitimate OCR/export/backup request instead of failing at 10 s.
            import time
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (13, 11, 36):
                        raise
                    time.sleep(0.1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        return _Lease(handle, lock)
    except BaseException:
        if handle is not None:
            handle.close()
        lock.release()
        raise


@contextmanager
def data_guard(directory: Path):
    lease = acquire(Path(directory))
    try:
        yield
    finally:
        lease.close()


class DataAccessMiddleware:
    def __init__(self, app, directory: Path):
        self.app, self.directory = app, Path(directory)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        lease = None
        try:
            with anyio.CancelScope(shield=True):
                lease = await run_in_threadpool(acquire, self.directory)
            await self.app(scope, receive, send)
        finally:
            if lease is not None:
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(lease.close)
