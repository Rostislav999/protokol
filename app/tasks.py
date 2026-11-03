"""Bounded local import jobs; polling and cancellation never take the data lock."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock
import time
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import progress


class Job:
    def __init__(self, operation):
        self.id, self.operation = uuid4().hex, operation
        self.cancelled = Event()
        self.lock = RLock()
        self.status, self.result, self.error, self.future = 'queued', None, '', None
        self.created = time.monotonic()
        self.state = dict(file_index=0, files_total=0, filename='', page=0, pages=0, phase='У черзі')

    def check(self):
        if self.cancelled.is_set():
            raise progress.TaskCancelled()

    def update(self, **values):
        self.check()
        with self.lock:
            self.state.update(values)

    def public(self):
        with self.lock:
            state = dict(self.state)
            completed = max(0, state['file_index'] - 1)
            fraction = (state['page'] - 1) / state['pages'] if state['pages'] and state['page'] else 0
            percent = min(99, int(100 * (completed + fraction) / max(state['files_total'], 1)))
            return dict(id=self.id, operation=self.operation, status=self.status, error=self.error,
                        percent=100 if self.status == 'completed' else percent, **state)


class TaskManager:
    def __init__(self, processors):
        self.processors = processors
        self.jobs = {}
        self.lock = RLock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='protokol-import')

    def start(self, operation, files, options):
        with self.lock:
            self.jobs = {key: job for key, job in self.jobs.items() if job.status in {'queued', 'running', 'cancelling'} or time.monotonic() - job.created < 3600}
            if sum(job.status in {'queued', 'running', 'cancelling'} for job in self.jobs.values()) >= 4:
                raise HTTPException(429, 'У черзі вже чотири завдання. Дочекайтеся завершення або скасуйте одне.')
            if len(self.jobs) >= 50:
                oldest = next((key for key, job in self.jobs.items() if job.status not in {'queued', 'running', 'cancelling'}), None)
                if oldest:
                    self.jobs.pop(oldest)
            job = Job(operation)
            self.jobs[job.id] = job
            job.future = self.executor.submit(self._run, job, files, options)
            return job.public()

    def _run(self, job, files, options):
        token = progress.current.set(job)
        try:
            job.check()
            with job.lock:
                job.status = 'running'
            result = self.processors[job.operation](files, options)
            job.check()
            with job.lock:
                job.result, job.status = result, 'completed'
                job.state['phase'] = 'Готово'
        except progress.TaskCancelled:
            with job.lock:
                job.status, job.result = 'cancelled', None
                job.state['phase'] = 'Обробку скасовано'
        except Exception as exc:
            with job.lock:
                if job.cancelled.is_set():
                    job.status, job.result = 'cancelled', None
                    job.state['phase'] = 'Обробку скасовано'
                else:
                    job.error = str(exc)[:3000] if isinstance(exc, (ValueError, RuntimeError, OSError)) else 'Не вдалося опрацювати документи. Збережіть технічний звіт.'
                    job.status, job.result = 'failed', None
                    job.state['phase'] = 'Помилка обробки'
        finally:
            progress.current.reset(token)

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise HTTPException(404, 'Завдання не знайдено. Повторіть імпорт файлів.')
            return job

    def cancel(self, job_id):
        job = self.get(job_id)
        with job.lock:
            if job.status in {'queued', 'running', 'cancelling'}:
                job.cancelled.set()
                job.status = 'cancelled' if job.future.cancel() else 'cancelling'
                job.state['phase'] = 'Скасовуємо обробку…'
            return job.public()

    def close(self):
        for key in list(self.jobs):
            self.cancel(key)
        self.executor.shutdown(wait=False, cancel_futures=True)


def tasks_router(manager):
    router = APIRouter(prefix='/api/tasks', tags=['processing'])

    @router.post('/{operation}')
    async def start(operation: str, files: list[UploadFile] = File(...), exact_pages: bool = Form(True)):
        uploaded, total = [], 0
        try:
            if operation not in manager.processors:
                raise HTTPException(404, 'Невідомий спосіб обробки документів.')
            if not 1 <= len(files) <= (1 if operation == 'act' else 101):
                raise HTTPException(422, 'Оберіть один акт або до 100 протоколів та одну XLSX.')
            extensions = {'.docx', '.pdf', '.zip'} | ({'.xlsx'} if operation == 'cover_letters' else set())
            if operation == 'act':
                extensions = {'.docx', '.pdf', '.txt', '.csv', '.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.webp'}
            for file in files:
                name = Path((file.filename or '').replace('\\', '/')).name
                raw = await file.read(25 * 1024 * 1024 + 1)
                total += len(raw)
                if not raw or len(raw) > 25 * 1024 * 1024 or total > 100 * 1024 * 1024:
                    raise HTTPException(422, 'До 25 МБ на файл і 100 МБ на комплект.')
                if Path(name).suffix.lower() not in extensions:
                    raise HTTPException(422, 'Цей формат файла не підтримується для обраної операції.')
                uploaded.append((name, raw))
            return manager.start(operation, uploaded, {'exact_pages': exact_pages})
        finally:
            for file in files:
                await file.close()

    @router.get('/{job_id}')
    def status(job_id: str):
        return manager.get(job_id).public()

    @router.post('/{job_id}/cancel')
    def cancel(job_id: str):
        return manager.cancel(job_id)

    @router.get('/{job_id}/result')
    def result(job_id: str):
        job = manager.get(job_id)
        with job.lock:
            if job.status == 'completed':
                return job.result
            raise HTTPException(409, 'Обробку скасовано.' if job.status == 'cancelled' else 'Результат ще не готовий.')
    return router
