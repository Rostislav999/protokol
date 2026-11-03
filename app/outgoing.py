"""Transactional local outgoing-letter register with original document downloads."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal
from urllib.parse import quote
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .word_tools import OfficeError, export_pdf, _validate_docx


class JournalError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


class JournalUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    status: Literal['draft', 'ready', 'sent']
    notes: str = Field(default='', max_length=3000)
    expected_updated_at: str | None = Field(default=None, max_length=100)


class JournalStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('CREATE TABLE IF NOT EXISTS outgoing_batches '
                               '(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)')
            connection.execute('''CREATE TABLE IF NOT EXISTS outgoing_letters (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES outgoing_batches(id),
                identity TEXT NOT NULL, sequence INTEGER NOT NULL, title TEXT NOT NULL,
                filename TEXT NOT NULL, protocols TEXT NOT NULL, outgoing_number TEXT NOT NULL,
                number_key TEXT NOT NULL, outgoing_date TEXT NOT NULL, day TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('draft','ready','sent')), notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sent_at TEXT NOT NULL DEFAULT '',
                docx BLOB NOT NULL, pdf BLOB, UNIQUE(batch_id,identity))''')
            connection.execute('CREATE INDEX IF NOT EXISTS outgoing_search_date ON outgoing_letters(day, status)')
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _archive(connection, batch_id):
        out = BytesIO()
        with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
            for row in connection.execute('SELECT filename,docx FROM outgoing_letters WHERE batch_id=? ORDER BY sequence', (batch_id,)):
                info = ZipInfo(row['filename'], date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                archive.writestr(info, bytes(row['docx']))
        return out.getvalue()

    def register(self, batch_id, fingerprint, payload, builder, *, status='ready'):
        """Only successful document builds commit; repeated downloads reuse rows."""
        with self.transaction() as connection:
            old = connection.execute('SELECT fingerprint FROM outgoing_batches WHERE id=?', (batch_id,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise JournalError('Дані цього комплекту змінилися. Оновіть формування перед збереженням.', 409)
                if status == 'ready':
                    connection.execute("UPDATE outgoing_letters SET status='ready',updated_at=? WHERE batch_id=? AND status='draft'", (_now(), batch_id))
                return self._archive(connection, batch_id)
            documents = builder()
            timestamp = _now()
            connection.execute('INSERT INTO outgoing_batches VALUES (?,?,?,?)',
                               (batch_id, fingerprint, json.dumps(payload, ensure_ascii=False), timestamp))
            for index, document in enumerate(documents):
                identity = document['identity']
                entry_id = sha256((batch_id + '\0' + identity).encode()).hexdigest()
                number = document.get('number', '')
                date = document.get('date', '')
                number_key = ' '.join(number.casefold().split())
                if number and connection.execute(
                        'SELECT id FROM outgoing_letters WHERE number_key=? AND substr(outgoing_date,1,4)=?',
                        (number_key, date[:4])).fetchone():
                    raise JournalError('Вихідний номер «' + number + '» уже є в журналі за цей рік. Перевірте номер або відкрийте збережений лист.')
                _validate_docx(document['docx'])
                day = date or datetime.now(ZoneInfo('Europe/Kyiv')).date().isoformat()
                connection.execute('''INSERT INTO outgoing_letters
                    (id,batch_id,identity,sequence,title,filename,protocols,outgoing_number,number_key,
                     outgoing_date,day,status,created_at,updated_at,docx)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (entry_id, batch_id, identity, index, document['title'], document['filename'],
                     json.dumps(document['protocols'], ensure_ascii=False), number, number_key, date, day,
                     status, timestamp, timestamp, document['docx']))
            return self._archive(connection, batch_id)

    @staticmethod
    def _public(row):
        keys = ('id', 'batch_id', 'identity', 'title', 'filename', 'outgoing_number', 'outgoing_date',
                'day', 'status', 'notes', 'created_at', 'updated_at', 'sent_at')
        result = {key: row[key] for key in keys}
        result['protocols'] = json.loads(row['protocols'])
        result['has_pdf'] = bool(row['has_pdf'])
        return result

    def list(self, *, query='', status='', date_from='', date_to='', offset=0, limit=50):
        if not self.path.exists():
            return {'entries': [], 'total': 0, 'offset': offset, 'limit': limit}
        with self.transaction() as connection:
            clauses, arguments = [], []
            if status:
                clauses.append('status=?'); arguments.append(status)
            for field, value in [('day>=?', date_from), ('day<=?', date_to)]:
                if value:
                    clauses.append(field); arguments.append(value)
            where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
            columns = 'id,batch_id,identity,title,filename,protocols,outgoing_number,outgoing_date,day,status,notes,created_at,updated_at,sent_at'
            rows = connection.execute('SELECT ' + columns + ',pdf IS NOT NULL AS has_pdf FROM outgoing_letters' + where + ' ORDER BY created_at DESC,sequence', arguments)
            # Unicode casefold matches Ukrainian/Russian names consistently;
            # SQLite's default LOWER/LIKE only fold ASCII characters.
            terms = query.casefold().split()
            matches = []
            for row in rows:
                searchable = ' '.join(str(row[key]) for key in ('title', 'protocols', 'outgoing_number', 'notes', 'day')).casefold()
                if all(term in searchable for term in terms):
                    matches.append(self._public(row))
            return {'entries': matches[offset:offset + limit], 'total': len(matches), 'offset': offset, 'limit': limit}

    def get(self, entry_id):
        if not re.fullmatch(r'[a-f0-9]{64}', entry_id) or not self.path.exists():
            raise JournalError('Лист у журналі не знайдено.', 404)
        with self.transaction() as connection:
            row = connection.execute('SELECT *,pdf IS NOT NULL AS has_pdf FROM outgoing_letters WHERE id=?', (entry_id,)).fetchone()
            if row is None:
                raise JournalError('Лист у журналі не знайдено.', 404)
            result = self._public(row)
            result.update(docx=bytes(row['docx']), pdf=bytes(row['pdf']) if row['pdf'] is not None else None)
            result['payload'] = json.loads(connection.execute('SELECT payload FROM outgoing_batches WHERE id=?', (row['batch_id'],)).fetchone()[0])
            return result

    def update(self, entry_id, values: JournalUpdate):
        self.get(entry_id)
        with self.transaction() as connection:
            row = connection.execute('SELECT updated_at,sent_at FROM outgoing_letters WHERE id=?', (entry_id,)).fetchone()
            if values.expected_updated_at and values.expected_updated_at != row['updated_at']:
                raise JournalError('Запис змінено в іншому вікні. Оновіть журнал і повторіть дію.', 409)
            sent_at = (row['sent_at'] or _now()) if values.status == 'sent' else ''
            connection.execute('UPDATE outgoing_letters SET status=?,notes=?,updated_at=?,sent_at=? WHERE id=?',
                               (values.status, values.notes, _now(), sent_at, entry_id))
        item = self.get(entry_id)
        return {key: value for key, value in item.items() if key not in ('docx', 'pdf', 'payload')}

    def pdf(self, entry_id):
        item = self.get(entry_id)
        if item['pdf'] is not None:
            return item['pdf']
        raw, _ = export_pdf(item['docx'], item['filename'])
        with self.transaction() as connection:
            connection.execute('UPDATE outgoing_letters SET pdf=? WHERE id=?', (raw, entry_id))
        return raw

    @staticmethod
    def validate(path):
        """Validate a staged backup without migrating or changing that copy."""
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok':
                raise ValueError('Пошкоджено базу журналу.')
            for table, columns in {
                'outgoing_batches': {'id', 'fingerprint', 'payload', 'created_at'},
                'outgoing_letters': {'id', 'batch_id', 'identity', 'sequence', 'title', 'filename', 'protocols',
                                     'outgoing_number', 'number_key', 'outgoing_date', 'day', 'status', 'notes',
                                     'created_at', 'updated_at', 'sent_at', 'docx', 'pdf'},
            }.items():
                if not columns <= {row[1] for row in connection.execute('PRAGMA table_info(' + table + ')')}:
                    raise ValueError('Несумісна структура журналу.')
            from .cover_letters import LetterBatch, LetterRegistration
            for row in connection.execute('SELECT * FROM outgoing_batches'):
                LetterBatch.model_validate_json(row['payload'])
            for row in connection.execute('SELECT * FROM outgoing_letters'):
                if not re.fullmatch(r'[a-f0-9]{64}', row['id']) or row['status'] not in ('draft', 'ready', 'sent'):
                    raise ValueError('Некоректний запис журналу.')
                LetterRegistration(number=row['outgoing_number'], date=row['outgoing_date'])
                protocols = json.loads(row['protocols'])
                if not isinstance(protocols, list) or not all(isinstance(number, str) for number in protocols):
                    raise ValueError('Некоректний перелік протоколів журналу.')
                if Path(row['filename']).name != row['filename'] or not row['filename'].endswith('.docx'):
                    raise ValueError('Некоректне ім’я документа журналу.')
                _validate_docx(bytes(row['docx']))
            if connection.execute('SELECT id FROM outgoing_letters WHERE batch_id NOT IN (SELECT id FROM outgoing_batches) LIMIT 1').fetchone():
                raise ValueError('Неповний комплект у журналі.')
        finally:
            connection.close()


def outgoing_router(store: JournalStore) -> APIRouter:
    router = APIRouter(prefix='/api/outgoing', tags=['outgoing'])

    def failure(exc):
        return HTTPException(getattr(exc, 'status', 422), str(exc))

    @router.get('')
    def entries(q: str = Query(default='', max_length=300),
                status: Literal['', 'draft', 'ready', 'sent'] = '',
                date_from: str = Query(default='', pattern=r'^(?:\d{4}-\d{2}-\d{2})?$'),
                date_to: str = Query(default='', pattern=r'^(?:\d{4}-\d{2}-\d{2})?$'),
                offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        try:
            for value in (date_from, date_to):
                if value:
                    datetime.strptime(value, '%Y-%m-%d')
            if date_from and date_to and date_from > date_to:
                raise ValueError('Invalid range')
        except ValueError as exc:
            raise HTTPException(422, 'Укажіть коректний діапазон дат журналу.') from exc
        return store.list(query=q, status=status, date_from=date_from, date_to=date_to, offset=offset, limit=limit)

    @router.get('/{entry_id}')
    def detail(entry_id: str):
        try:
            return {key: value for key, value in store.get(entry_id).items() if key not in ('docx', 'pdf')}
        except JournalError as exc:
            raise failure(exc) from exc

    @router.patch('/{entry_id}')
    def update(entry_id: str, values: JournalUpdate):
        try:
            return store.update(entry_id, values)
        except JournalError as exc:
            raise failure(exc) from exc

    @router.get('/{entry_id}/{format}')
    def download(entry_id: str, format: Literal['docx', 'pdf']):
        try:
            item = store.get(entry_id)
            raw = store.pdf(entry_id) if format == 'pdf' else item['docx']
            filename = str(Path(item['filename']).with_suffix('.' + format))
            media = 'application/pdf' if format == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            return Response(raw, media_type=media, headers={
                'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename), 'Cache-Control': 'no-store',
            })
        except (JournalError, OfficeError) as exc:
            raise failure(exc) from exc

    return router
