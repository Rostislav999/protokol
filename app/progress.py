"""Cooperative progress/cancellation shared by local import workers and OCR."""
from contextvars import ContextVar
import json
import subprocess
import time

current = ContextVar('protokol_processing_task', default=None)


class TaskCancelled(Exception):
    pass


def check():
    task = current.get()
    if task:
        task.check()


def file(index, total, filename):
    task = current.get()
    if task:
        task.update(file_index=index, files_total=total, filename=filename, page=0, pages=0, phase='Читаємо файл')


def page(index, total):
    task = current.get()
    if task:
        task.update(page=index, pages=total, phase='Читаємо сторінку')


def phase(value):
    task = current.get()
    if task:
        task.update(phase=value)


def run_process(command, *, timeout, **kwargs):
    """Only terminate the OCR child created by this call, never a user's Office."""
    check()
    capture = kwargs.pop('capture_output', False)
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    with subprocess.Popen(command, **kwargs) as process:
        try:
            while True:
                check()
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(.15, remaining))
                    check()
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            raise


def office_process(command, *, result_path, cancel_path, documents, timeout, **kwargs):
    """Ask the private Word worker to stop between documents and close itself."""
    task = current.get()
    if task is None:
        return subprocess.run(command, timeout=timeout, **kwargs)
    check()
    if kwargs.pop('capture_output', False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    with subprocess.Popen(command, **kwargs) as process:
        try:
            while True:
                with task.lock:
                    if task.cancelled.is_set():
                        cancel_path.touch(exist_ok=True)
                        task.state['phase'] = 'Скасовуємо після поточного документа у Word…'
                    else:
                        try:
                            result = json.loads(result_path.read_text(encoding='utf-8-sig')) if result_path.exists() else {}
                            index = min(len(result.get('documents', [])), len(documents) - 1)
                            task.state.update(file_index=index + 1, files_total=len(documents), filename=documents[index], page=0, pages=0,
                                              phase='Рахуємо аркуші у Microsoft Word')
                        except (OSError, ValueError):
                            pass
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(.2, remaining))
                    check()
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.communicate()
            raise
