"""Durable document packages, letter revisions and personal template choices."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .workspace import SECTIONS, MAX_REVISION, _draft_data, _encode

WORKSPACE = 'workspace'
MAX_FILE = 150 * 1024 * 1024
MAX_PACKAGE_FILES = 500
HISTORY_LIMIT = 100
ID = re.compile(r'^[a-f0-9]{32}$')
KINDS = {'source', 'excel_template', 'output_word', 'output_pdf', 'output_excel', 'attachment'}
EXTENSIONS = {'.docx', '.pdf', '.zip', '.xlsx', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.txt', '.csv', '.bmp', '.webp'}
FILE_COLUMNS = 'id,section,kind,filename,digest,size,fingerprint,active,created_at'


class WorkbenchError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def fingerprint(data):
    """UI selection/provenance do not invalidate a document's contents."""
    ignored = {'selectedLetterId', 'generation_id', 'job_id', 'warnings', 'sources', 'sourceHadFile', 'confirmed', 'reviewConfirmed', 'review_confirmed'}
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(child) for key, child in value.items() if key not in ignored and not key.startswith('_')}
        return [clean(child) for child in value] if isinstance(value, list) else value
    return sha256(json.dumps(clean(data), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def safe_filename(value):
    name = str(value).replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', name).strip('. ')[:200]
    if not name or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        name = 'Документ_' + name
    return name


def letter_content(value):
    arrays = {'recipients', 'body', 'attachments'}
    strings = {'position', 'signer', 'contact'}
    if not isinstance(value, dict) or set(value) - arrays - strings:
        raise WorkbenchError('Некоректний вміст версії листа.')
    for key, item in value.items():
        if key in arrays and (not isinstance(item, list) or any(not isinstance(line, str) for line in item)):
            raise WorkbenchError('Абзаци листа мають бути текстовими рядками.')
        if key in strings and not isinstance(item, str):
            raise WorkbenchError('Поля підпису мають бути текстом.')
    return value


class WorkStore:
    def __init__(self, directory: Path):
        self.path = Path(directory) / 'workbench.sqlite3'

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS packages (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, customer TEXT NOT NULL, reference TEXT NOT NULL,
                    notes TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','ready','archived')),
                    revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS package_drafts (
                    package_id TEXT NOT NULL REFERENCES packages(id) ON DELETE CASCADE, section TEXT NOT NULL,
                    data TEXT, revision INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(package_id,section));
                CREATE TABLE IF NOT EXISTS package_files (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, section TEXT NOT NULL, kind TEXT NOT NULL,
                    filename TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL,
                    fingerprint TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS files_scope ON package_files(scope,section,kind,active);
                CREATE TABLE IF NOT EXISTS letter_versions (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, letter_id TEXT NOT NULL, title TEXT NOT NULL,
                    label TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS versions_scope ON letter_versions(scope,letter_id,created_at);
                CREATE TABLE IF NOT EXISTS template_choices (
                    family TEXT NOT NULL, template_id TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT NOT NULL DEFAULT '', uses INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(family,template_id));
                CREATE TABLE IF NOT EXISTS workbench_preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def scope(connection, scope, *, editing=False):
        if scope == WORKSPACE:
            return None
        row = connection.execute('SELECT * FROM packages WHERE id=?', (scope,)).fetchone()
        if row is None:
            raise WorkbenchError('Пакет документів не знайдено.', 404)
        if editing and row['status'] == 'archived':
            raise WorkbenchError('Поверніть пакет з архіву в роботу перед редагуванням.', 409)
        return row

    @staticmethod
    def touch(connection, scope):
        if scope != WORKSPACE:
            connection.execute("UPDATE packages SET updated_at=?,revision=revision+CASE WHEN status='ready' THEN 1 ELSE 0 END,"
                               "status=CASE WHEN status='ready' THEN 'draft' ELSE status END WHERE id=?", (now(), scope))

    def packages(self, query='', status=''):
        with self.transaction() as connection:
            rows = [dict(row) for row in connection.execute('SELECT * FROM packages ORDER BY updated_at DESC,id')]
            terms = query.casefold().split()
            for row in rows:
                row['files'] = connection.execute('SELECT COUNT(*) FROM package_files WHERE scope=?', (row['id'],)).fetchone()[0]
                row['sections'] = [item[0] for item in connection.execute('SELECT section FROM package_drafts WHERE package_id=? AND data IS NOT NULL', (row['id'],))]
            return [row for row in rows if (not status or row['status'] == status)
                    and all(term in ' '.join(str(row[key]) for key in ('title', 'customer', 'reference', 'notes')).casefold() for term in terms)]

    def create(self, values, drafts=None, copy_scope=None):
        with self.transaction() as connection:
            if connection.execute('SELECT COUNT(*) FROM packages').fetchone()[0] >= 1000:
                raise WorkbenchError('Дозволено до 1000 пакетів документів.')
            package_id, stamp = uuid4().hex, now()
            connection.execute('INSERT INTO packages VALUES (?,?,?,?,?,?,?,?,?)',
                               (package_id, values['title'], values.get('customer', ''), values.get('reference', ''), values.get('notes', ''), 'draft', 1, stamp, stamp))
            for section, data in (drafts or {}).items():
                self._save_draft(connection, package_id, section, data, 0, capture=False)
            if copy_scope:
                self.scope(connection, copy_scope)
                for row in connection.execute('SELECT * FROM package_files WHERE scope=? AND (?<>? OR active=1)', (copy_scope, copy_scope, WORKSPACE)).fetchall():
                    if copy_scope == WORKSPACE:
                        section = (drafts or {}).get(row['section'])
                        if row['kind'].startswith('output_') and (section is None or row['fingerprint'] != fingerprint(section)):
                            continue
                        if row['kind'] == 'source':
                            if not section:
                                continue
                            if row['section'] in {'acts', 'assignments'}:
                                if section.get('method') == 'manual' or not section.get('source'):
                                    continue
                                if section.get('sourceGroup') and row['fingerprint'] != section['sourceGroup']:
                                    continue
                                if not section.get('sourceGroup') and row['filename'] != section['source']:
                                    continue
                        if row['kind'] == 'excel_template' and not (section or {}).get('templateWasSelected'):
                            continue
                    connection.execute('INSERT INTO package_files VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                       (uuid4().hex, package_id, row['section'], row['kind'], row['filename'], row['digest'], row['size'], row['data'], row['fingerprint'], row['active'], row['created_at']))
                for row in connection.execute('SELECT * FROM letter_versions WHERE scope=? AND ?<>?', (copy_scope, copy_scope, WORKSPACE)).fetchall():
                    connection.execute('INSERT INTO letter_versions VALUES (?,?,?,?,?,?,?,?)',
                                       (uuid4().hex, package_id, row['letter_id'], row['title'], row['label'], row['data'], row['digest'], row['created_at']))
            if (drafts or {}).get('cover_letters'):
                self._capture(connection, package_id, _draft_data(drafts['cover_letters']))
            return dict(connection.execute('SELECT * FROM packages WHERE id=?', (package_id,)).fetchone())

    def get(self, package_id):
        if not ID.fullmatch(package_id):
            raise WorkbenchError('Пакет документів не знайдено.', 404)
        with self.transaction() as connection:
            return dict(self.scope(connection, package_id))

    def update(self, package_id, values, expected_revision):
        if not ID.fullmatch(package_id):
            raise WorkbenchError('Пакет документів не знайдено.', 404)
        with self.transaction() as connection:
            old = self.scope(connection, package_id)
            if old['revision'] != expected_revision:
                raise WorkbenchError('Пакет змінено в іншому вікні. Оновіть відомості перед збереженням.', 409)
            connection.execute('UPDATE packages SET title=?,customer=?,reference=?,notes=?,status=?,revision=revision+1,updated_at=? WHERE id=?',
                               (values['title'], values['customer'], values['reference'], values['notes'], values['status'], now(), package_id))
            return dict(connection.execute('SELECT * FROM packages WHERE id=?', (package_id,)).fetchone())

    def drafts(self, package_id):
        if not ID.fullmatch(package_id):
            raise WorkbenchError('Пакет документів не знайдено.', 404)
        with self.transaction() as connection:
            self.scope(connection, package_id)
            rows = connection.execute('SELECT * FROM package_drafts WHERE package_id=?', (package_id,)).fetchall()
            return {'schema_version': 1, 'revisions': {row['section']: row['revision'] for row in rows},
                    'sections': {row['section']: {'revision': row['revision'], 'updated_at': row['updated_at'], 'data': _draft_data(json.loads(row['data']))}
                                 for row in rows if row['data'] is not None}}

    def _save_draft(self, connection, package_id, section, data, expected_revision, capture=True):
        if section not in SECTIONS:
            raise WorkbenchError('Невідомий розділ чернетки.')
        clean = _draft_data(data) if data is not None else None
        row = connection.execute('SELECT revision,data FROM package_drafts WHERE package_id=? AND section=?', (package_id, section)).fetchone()
        revision = row['revision'] if row else 0
        if expected_revision is not None and expected_revision != revision:
            raise WorkbenchError('Чернетку вже змінено в іншому вікні. Оновіть її перед збереженням.', 409)
        if revision + 1 >= MAX_REVISION:
            raise WorkbenchError('Перевищено підтримуваний діапазон версій чернетки.', 409)
        stamp = now()
        if section == 'cover_letters' and clean and capture:
            self._capture(connection, package_id, clean)
        connection.execute('INSERT INTO package_drafts VALUES (?,?,?,?,?) ON CONFLICT(package_id,section) DO UPDATE SET '
                           'data=excluded.data,revision=excluded.revision,updated_at=excluded.updated_at',
                           (package_id, section, _encode(clean).decode() if clean is not None else None, revision + 1, stamp))
        previous = json.loads(row['data']) if row and row['data'] is not None else None
        if fingerprint(previous) != fingerprint(clean):
            self.touch(connection, package_id)
        return {'data': clean, 'revision': revision + 1, 'updated_at': stamp} if clean is not None else {'deleted': True, 'revision': revision + 1, 'updated_at': stamp}

    def save_draft(self, package_id, section, data, expected_revision=None):
        if not ID.fullmatch(package_id):
            raise WorkbenchError('Пакет документів не знайдено.', 404)
        with self.transaction() as connection:
            self.scope(connection, package_id, editing=True)
            return self._save_draft(connection, package_id, section, data, expected_revision)

    @staticmethod
    def _capture(connection, scope, data, label=''):
        for letter in data.get('letters', []):
            if not isinstance(letter, dict) or not isinstance(letter.get('id'), str) or not isinstance(letter.get('content'), dict):
                continue
            # A revision restores editable text; official outgoing numbers are never reused.
            if not 1 <= len(letter['id']) <= 100:
                raise WorkbenchError('Некоректний ідентифікатор листа.')
            value = {'id': letter['id'], 'title': str(letter.get('title', 'Супровідна'))[:1000], 'content': letter_content(letter['content'])}
            raw = _encode(_draft_data(value)).decode()
            digest = fingerprint(value)
            previous = connection.execute('SELECT digest,created_at FROM letter_versions WHERE scope=? AND letter_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (scope, value['id'])).fetchone()
            if previous and previous['digest'] == digest and not label:
                continue
            stamp = now()
            if previous and stamp <= previous['created_at']:
                stamp = (datetime.fromisoformat(previous['created_at']) + timedelta(microseconds=1)).isoformat(timespec='microseconds')
            connection.execute('INSERT INTO letter_versions VALUES (?,?,?,?,?,?,?,?)',
                               (uuid4().hex, scope, value['id'], value['title'], label[:150], raw, digest, stamp))
            # Reserve room for named checkpoints so ordinary typing cannot erase them.
            named_limit = min(30, max(1, HISTORY_LIMIT // 3))
            connection.execute("DELETE FROM letter_versions WHERE scope=? AND letter_id=? AND label<>'' AND id NOT IN "
                               "(SELECT id FROM letter_versions WHERE scope=? AND letter_id=? AND label<>'' ORDER BY created_at DESC,id DESC LIMIT ?)",
                               (scope, value['id'], scope, value['id'], named_limit))
            named_count = connection.execute("SELECT COUNT(*) FROM letter_versions WHERE scope=? AND letter_id=? AND label<>''", (scope, value['id'])).fetchone()[0]
            connection.execute("DELETE FROM letter_versions WHERE scope=? AND letter_id=? AND label='' AND id NOT IN "
                               "(SELECT id FROM letter_versions WHERE scope=? AND letter_id=? AND label='' ORDER BY created_at DESC,id DESC LIMIT ?)",
                               (scope, value['id'], scope, value['id'], max(0, HISTORY_LIMIT - named_count)))

    def capture(self, scope, data, label=''):
        clean = _draft_data(data)
        with self.transaction() as connection:
            self.scope(connection, scope, editing=True)
            self._capture(connection, scope, clean, label)

    def versions(self, scope, letter_id):
        with self.transaction() as connection:
            self.scope(connection, scope)
            return [dict(row) for row in connection.execute('SELECT id,letter_id,title,label,created_at FROM letter_versions WHERE scope=? AND letter_id=? ORDER BY created_at DESC,id DESC LIMIT ?', (scope, letter_id, HISTORY_LIMIT))]

    def version(self, scope, version_id):
        with self.transaction() as connection:
            self.scope(connection, scope)
            row = connection.execute('SELECT * FROM letter_versions WHERE id=? AND scope=?', (version_id, scope)).fetchone()
            if row is None:
                raise WorkbenchError('Версію листа не знайдено.', 404)
            return {**dict(row), 'data': json.loads(row['data'])}

    def add_file(self, scope, section, kind, filename, data, draft=None, group=''):
        if section not in SECTIONS or kind not in KINDS:
            raise WorkbenchError('Перевірте розділ і призначення файла.')
        filename = safe_filename(filename)
        if Path(filename).suffix.lower() not in EXTENSIONS or not data or len(data) > MAX_FILE:
            raise WorkbenchError('Підтримуються документи, зображення й ZIP до 150 МБ.')
        digest = sha256(data).hexdigest()
        signature = group if kind == 'source' and group else fingerprint(_draft_data(draft)) if draft is not None else ''
        active = 0 if kind == 'source' and group else 1
        with self.transaction() as connection:
            self.scope(connection, scope, editing=True)
            old = connection.execute('SELECT id FROM package_files WHERE scope=? AND section=? AND kind=? AND digest=? AND fingerprint=? AND filename=?',
                                     (scope, section, kind, digest, signature, filename)).fetchone()
            if old:
                previous = connection.execute('SELECT active FROM package_files WHERE id=?', (old['id'],)).fetchone()
                if kind.startswith('output_') or kind == 'excel_template':
                    connection.execute('UPDATE package_files SET active=0 WHERE scope=? AND section=? AND kind=?', (scope, section, kind))
                    connection.execute('UPDATE package_files SET active=1 WHERE id=?', (old['id'],))
                    if not previous['active']:
                        self.touch(connection, scope)
                return self._file_info(connection.execute(f'SELECT {FILE_COLUMNS} FROM package_files WHERE id=?', (old['id'],)).fetchone())
            if connection.execute('SELECT COUNT(*) FROM package_files WHERE scope=?', (scope,)).fetchone()[0] >= MAX_PACKAGE_FILES:
                raise WorkbenchError('У пакеті вже 500 файлів. Створіть наступний пакет.')
            if kind.startswith('output_') or kind == 'excel_template':
                connection.execute('UPDATE package_files SET active=0 WHERE scope=? AND section=? AND kind=?', (scope, section, kind))
            file_id = uuid4().hex
            connection.execute('INSERT INTO package_files VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (file_id, scope, section, kind, filename, digest, len(data), data, signature, active, now()))
            if active:
                self.touch(connection, scope)
            return self._file_info(connection.execute(f'SELECT {FILE_COLUMNS} FROM package_files WHERE id=?', (file_id,)).fetchone())

    def activate_sources(self, scope, section, group, append=False):
        if section not in SECTIONS or not ID.fullmatch(group):
            raise WorkbenchError('Некоректна група вихідних файлів.')
        with self.transaction() as connection:
            self.scope(connection, scope, editing=True)
            if not connection.execute("SELECT 1 FROM package_files WHERE scope=? AND section=? AND kind='source' AND fingerprint=?", (scope, section, group)).fetchone():
                raise WorkbenchError('Вихідні файли не знайдено.', 404)
            if not append:
                connection.execute("UPDATE package_files SET active=0 WHERE scope=? AND section=? AND kind='source'", (scope, section))
            connection.execute("UPDATE package_files SET active=1 WHERE scope=? AND section=? AND kind='source' AND fingerprint=?", (scope, section, group))
            self.touch(connection, scope)
            return {'saved': True}

    @staticmethod
    def _file_info(row):
        return {key: row[key] for key in ('id', 'section', 'kind', 'filename', 'digest', 'size', 'fingerprint', 'active', 'created_at')}

    def files(self, scope):
        with self.transaction() as connection:
            self.scope(connection, scope)
            return [self._file_info(row) for row in connection.execute(f'SELECT {FILE_COLUMNS} FROM package_files WHERE scope=? ORDER BY created_at DESC,id', (scope,))]

    def file(self, scope, file_id):
        with self.transaction() as connection:
            self.scope(connection, scope)
            row = connection.execute('SELECT * FROM package_files WHERE id=? AND scope=?', (file_id, scope)).fetchone()
            if row is None:
                raise WorkbenchError('Файл у цьому пакеті не знайдено.', 404)
            return {**self._file_info(row), 'data': bytes(row['data'])}

    def session(self, scope=None):
        with self.transaction() as connection:
            if scope is not None:
                self.scope(connection, scope)
                connection.execute('INSERT INTO workbench_preferences VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', ('last_scope', scope))
            row = connection.execute("SELECT value FROM workbench_preferences WHERE key='last_scope'").fetchone()
            return row['value'] if row else WORKSPACE

    def choices(self):
        with self.transaction() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM template_choices ORDER BY last_used_at DESC,template_id')]

    def choose(self, family, template_id, favorite=None, used=False):
        with self.transaction() as connection:
            connection.execute('INSERT OR IGNORE INTO template_choices(family,template_id) VALUES (?,?)', (family, template_id))
            if favorite is not None:
                connection.execute('UPDATE template_choices SET favorite=? WHERE family=? AND template_id=?', (int(favorite), family, template_id))
            if used:
                connection.execute('UPDATE template_choices SET uses=uses+1,last_used_at=? WHERE family=? AND template_id=?', (now(), family, template_id))
            return dict(connection.execute('SELECT * FROM template_choices WHERE family=? AND template_id=?', (family, template_id)).fetchone())

    def rebase(self):
        """Restoring a backup invalidates stale editors in every package."""
        import time
        with self.transaction() as connection:
            floor = max(int(time.time() * 1000), connection.execute('SELECT COALESCE(MAX(revision),0) FROM package_drafts').fetchone()[0]) + 1
            if floor >= MAX_REVISION:
                raise WorkbenchError('Версії пакетів виходять за підтримуваний діапазон.')
            connection.execute('UPDATE package_drafts SET revision=?', (floor,))
            for row in connection.execute('SELECT package_id,section,data FROM package_drafts WHERE data IS NOT NULL').fetchall():
                clean = _draft_data(json.loads(row['data']))
                connection.execute('UPDATE package_drafts SET data=? WHERE package_id=? AND section=?', (_encode(clean).decode(), row['package_id'], row['section']))
            connection.execute('UPDATE packages SET revision=MAX(revision+1,?)', (floor,))

    @staticmethod
    def validate(path):
        """Validate application rows as well as SQLite structure before restore."""
        connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok':
                raise ValueError('Пошкоджена база пакетів документів.')
            required = {'packages': {'id', 'title', 'customer', 'reference', 'notes', 'status', 'revision', 'created_at', 'updated_at'},
                        'package_drafts': {'package_id', 'section', 'data', 'revision', 'updated_at'},
                        'package_files': {'id', 'scope', 'section', 'kind', 'filename', 'digest', 'size', 'data', 'fingerprint', 'active', 'created_at'},
                        'letter_versions': {'id', 'scope', 'letter_id', 'title', 'label', 'data', 'digest', 'created_at'},
                        'template_choices': {'family', 'template_id', 'favorite', 'last_used_at', 'uses'}, 'workbench_preferences': {'key', 'value'}}
            for table, columns in required.items():
                if not columns <= {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}:
                    raise ValueError('Несумісна структура бази пакетів документів.')
            packages = connection.execute('SELECT * FROM packages').fetchall()
            ids = {row['id'] for row in packages}
            if len(packages) != len(ids) or len(ids) > 1000:
                raise ValueError('Забагато або повторні пакети документів.')
            if connection.execute('SELECT 1 FROM package_files GROUP BY scope HAVING COUNT(*)>?', (MAX_PACKAGE_FILES,)).fetchone():
                raise ValueError('Забагато файлів у пакеті.')
            if connection.execute('SELECT 1 FROM letter_versions GROUP BY scope,letter_id HAVING COUNT(*)>?', (HISTORY_LIMIT,)).fetchone():
                raise ValueError('Забагато версій листа.')
            for row in connection.execute('SELECT * FROM packages'):
                if not ID.fullmatch(row['id']) or row['status'] not in {'draft', 'ready', 'archived'} or not 1 <= row['revision'] < MAX_REVISION:
                    raise ValueError('Некоректні відомості про пакет.')
                if any(not isinstance(row[key], str) or len(row[key]) > limit for key, limit in [('title', 200), ('customer', 1000), ('reference', 200), ('notes', 5000)]):
                    raise ValueError('Некоректний опис пакета.')
            for row in connection.execute('SELECT * FROM package_drafts'):
                if row['package_id'] not in ids or row['section'] not in SECTIONS or not 1 <= row['revision'] < MAX_REVISION:
                    raise ValueError('Некоректна чернетка пакета.')
                if row['data'] is not None:
                    _draft_data(json.loads(row['data']))
            for row in connection.execute('SELECT * FROM package_files'):
                if not ID.fullmatch(row['id']) or row['scope'] not in ids | {WORKSPACE} or row['section'] not in SECTIONS or row['kind'] not in KINDS or row['active'] not in (0, 1):
                    raise ValueError('Некоректний файл пакета.')
                if row['filename'] != safe_filename(row['filename']) or Path(row['filename']).suffix.lower() not in EXTENSIONS:
                    raise ValueError('Некоректне ім’я файла пакета.')
                if not 0 < row['size'] <= MAX_FILE or len(row['data']) != row['size'] or sha256(row['data']).hexdigest() != row['digest']:
                    raise ValueError('Пошкоджений вміст файла пакета.')
            for row in connection.execute('SELECT * FROM letter_versions'):
                if row['scope'] not in ids | {WORKSPACE} or not ID.fullmatch(row['id']) or not isinstance(row['letter_id'], str) or len(row['letter_id']) > 100:
                    raise ValueError('Некоректна історія листів.')
                value = _draft_data(json.loads(row['data']))
                letter_content(value.get('content'))
                if value.get('id') != row['letter_id'] or fingerprint(value) != row['digest']:
                    raise ValueError('Пошкоджена версія листа.')
            for row in connection.execute('SELECT * FROM template_choices'):
                if row['family'] not in {'protocols', 'assignments'} or not re.fullmatch(r'[\w-]{1,200}', row['template_id']) or row['favorite'] not in (0, 1) or row['uses'] < 0:
                    raise ValueError('Некоректні обрані шаблони.')
            for row in connection.execute('SELECT * FROM workbench_preferences'):
                if row['key'] != 'last_scope' or row['value'] not in ids | {WORKSPACE}:
                    raise ValueError('Некоректний поточний пакет.')
        except (sqlite3.Error, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError('Некоректна база пакетів документів.') from exc
        finally:
            connection.close()
