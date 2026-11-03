from threading import Event
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from app import progress
from app.main import create_app
from app.tasks import TaskManager
from app.storage_lock import data_guard


def wait_for(function, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(.01)
    pytest.fail('Background processing did not reach the expected state')


def test_import_jobs_return_real_results_and_validate_uploads(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        started = client.post('/api/tasks/act', files={'files': ('акт.txt', 'Замовник: військова частина\nАкт №125 від 01.10.2026'.encode())})
        assert started.status_code == 200
        job_id = started.json()['id']
        state = wait_for(lambda: (result if (result := client.get('/api/tasks/' + job_id).json())['status'] in {'completed', 'failed'} else None))
        assert state['status'] == 'completed', state
        result = client.get(f'/api/tasks/{job_id}/result').json()
        assert 'Замовник' in result['text'] and result['common']['act_number'] == '125'
        assert client.get('/api/workspace/drafts').json()['sections'] == {}
        assert client.post('/api/tasks/act', files={'files': ('bad.exe', b'bad')}).status_code == 422
        assert client.post('/api/tasks/act', files={'files': ('empty.txt', b'')}).status_code == 422
        assert client.get('/api/tasks/not-found').status_code == 404


def test_progress_and_cancellation_remain_available_while_the_data_lock_is_held(tmp_path):
    app = create_app(tmp_path)
    entered = Event()
    def processor(files, options):
        progress.file(1, 3, files[0][0]); progress.page(2, 4); entered.set()
        while True:
            progress.check(); time.sleep(.01)
    app.state.tasks.processors['act'] = processor
    with TestClient(app) as client:
        job = client.post('/api/tasks/act', files={'files': ('act.txt', b'contents')}).json()
        assert entered.wait(2)
        with data_guard(tmp_path):
            state = client.get('/api/tasks/' + job['id']).json()
            assert state['page'] == 2 and state['files_total'] == 3 and 0 < state['percent'] < 100
            assert client.post('/api/tasks/' + job['id'] + '/cancel').status_code == 200
            wait_for(lambda: client.get('/api/tasks/' + job['id']).json()['status'] == 'cancelled')
        assert client.get('/api/tasks/' + job['id'] + '/result').status_code == 409
        assert not (tmp_path / 'numbering.sqlite3').exists() or client.get('/api/assignments/numbering').json()['next_number'] is None


def test_ocr_cancellation_terminates_only_the_child_started_by_the_task():
    entered = Event()
    def processor(files, options):
        entered.set()
        return progress.run_process([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=60, capture_output=True)
    manager = TaskManager({'act': processor})
    try:
        job = manager.start('act', [], {})
        assert entered.wait(2)
        manager.cancel(job['id'])
        wait_for(lambda: manager.get(job['id']).status == 'cancelled', timeout=3)
        assert manager.get(job['id']).result is None
    finally:
        manager.close()


def test_queue_is_bounded_and_failed_jobs_never_return_partial_data():
    entered = Event()
    def processor(files, options):
        entered.set()
        while True:
            progress.check(); time.sleep(.01)
    manager = TaskManager({'act': processor})
    try:
        jobs = [manager.start('act', [], {}) for _ in range(4)]
        assert entered.wait(2)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as failure:
            manager.start('act', [], {})
        assert failure.value.status_code == 429
        for job in jobs:
            manager.cancel(job['id'])
        wait_for(lambda: all(manager.get(job['id']).status == 'cancelled' for job in jobs))
    finally:
        manager.close()


def test_private_office_worker_receives_cooperative_cancel_and_other_processes_keep_running(tmp_path):
    flag, ready, result = tmp_path / 'cancel.requested', tmp_path / 'ready', tmp_path / 'result.json'
    script = tmp_path / 'worker.py'
    script.write_text("import sys,time\nfrom pathlib import Path\nPath(sys.argv[2]).touch()\nwhile not Path(sys.argv[1]).exists(): time.sleep(.01)\nPath(sys.argv[3]).write_text('{\"ok\":true,\"documents\":[]}')\n")
    def processor(files, options):
        return progress.office_process([sys.executable, str(script), str(flag), str(ready), str(result)],
                                       result_path=result, cancel_path=flag, documents=['protocol.docx'], timeout=10, capture_output=True)
    manager = TaskManager({'act': processor})
    with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as unrelated:
        try:
            job = manager.start('act', [], {})
            wait_for(ready.exists)
            manager.cancel(job['id'])
            wait_for(lambda: manager.get(job['id']).status == 'cancelled', timeout=3)
            assert flag.exists() and unrelated.poll() is None
        finally:
            manager.close()
            unrelated.terminate(); unrelated.wait(timeout=3)
