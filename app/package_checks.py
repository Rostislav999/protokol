"""Conservative comparisons across the documents of one local package."""
from __future__ import annotations

from datetime import date
from io import BytesIO
import re
from zipfile import ZipFile, BadZipFile

from .protocol_excel import protocol_key, date_text, read_docx, supplier_code
from .workbench_store import fingerprint


def company(value):
    value = re.split(r'(?:код\s*)?ЄДРПОУ|ЕДРПОУ', str(value), maxsplit=1, flags=re.I)[0]
    return re.sub(r'[^\w]', '', value.casefold())


def day(value):
    value = str(value).strip()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        try:
            parsed = date_text(value)
        except ValueError:
            return value
        return '-'.join(reversed(parsed.split('.'))) if parsed else value


def check_package(drafts, files, read_file=None):
    sections = {key: value.get('data', value) for key, value in drafts.get('sections', drafts).items() if isinstance(value, dict)}
    issues = []
    def add(code, message, section='cover_letters', level='warning', number=''):
        issue = dict(code=code, message=message, section=section, level=level, number=number)
        if issue not in issues:
            issues.append(issue)
    act = sections.get('acts') or {}
    cover = sections.get('cover_letters') or {}
    excel = sections.get('excel') or {}
    assignments = sections.get('assignments') or {}
    for section, values in sections.items():
        warnings = values.get('warnings') or []
        if isinstance(warnings, str):
            warnings = warnings.splitlines()
        for warning in warnings[:200]:
            if str(warning).strip():
                add('source-warning', str(warning)[:5000], section)
    act_items = act.get('items') or []
    records = cover.get('records') or []
    letters = cover.get('letters') or []
    act_numbers = {}
    for item in act_items:
        number = protocol_key(str(item.get('protocol_number', '')))
        if not number:
            continue
        if number in act_numbers:
            add('duplicate-act-number', f'В акті повторюється номер протоколу №{number}.', 'acts', 'error', number)
        act_numbers[number] = item
    cover_numbers = {}
    for record in records:
        number = protocol_key(str(record.get('number', '')))
        if not number:
            add('empty-cover-number', 'У комплекті супровідних є протокол без номера.', level='error')
            continue
        if number in cover_numbers:
            add('duplicate-cover-number', f'У супровідних повторюється протокол №{number}.', level='error', number=number)
        cover_numbers[number] = record
        try:
            if int(record.get('pages') or 0) < 1:
                raise ValueError()
        except (ValueError, TypeError):
            add('missing-pages', f'Для протоколу №{number} не вказано кількість аркушів.', level='error', number=number)
        source = act_numbers.get(number)
        if source and day(source.get('protocol_date', '')) != day(record.get('date', '')):
            add('act-cover-date', f'Протокол №{number}: дата в акті «{source.get("protocol_date", "")}» і супровідних «{record.get("date", "")}» відрізняється.', level='error', number=number)
        supplier = act.get('common', {}).get('supplier', '')
        if source and supplier and record.get('supplier') and company(supplier) != company(record['supplier']):
            add('act-cover-supplier', f'Протокол №{number}: постачальник в акті та фірма в супровідних відрізняються.', number=number)
        if act_numbers and number not in act_numbers:
            add('cover-outside-act', f'Протокол №{number} є в супровідних, але його немає в поточному акті.', number=number)
    if records:
        for number in act_numbers.keys() - cover_numbers.keys():
            add('act-outside-cover', f'Протокол №{number} з акта не додано до супровідних. Перевірте, чи це часткове відправлення.', number=number)
    for letter in letters:
        expected = records if letter.get('id') == 'general' else [record for record in records if company(record.get('supplier', '')) == company(letter.get('title', ''))]
        attachment_lines = letter.get('content', {}).get('attachments') or []
        counts = {}
        for line in attachment_lines:
            match = re.search(r'№\s*([\w/-]+)', str(line))
            if not match:
                continue
            number = protocol_key(match[1])
            counts[number] = counts.get(number, 0) + 1
            record = cover_numbers.get(number)
            if not record:
                add('unknown-attachment', f'Лист «{letter.get("title", "")}» посилається на протокол №{number}, якого немає в комплекті.', number=number)
                continue
            pages = re.search(r'на\s+(\d+)\s*арк', str(line), re.I)
            if pages and pages[1] != str(record.get('pages', '')):
                add('attachment-pages', f'Лист «{letter.get("title", "")}»: для №{number} у додатку {pages[1]} арк., у реквізитах {record.get("pages")} арк.', level='error', number=number)
            referenced_date = re.search(r'від\s+(\d{2}\.\d{2}\.\d{4})', str(line))
            if referenced_date and day(referenced_date[1]) != day(record.get('date', '')):
                add('attachment-date', f'Лист «{letter.get("title", "")}»: дата протоколу №{number} у додатку не збігається з реквізитами.', level='error', number=number)
        for record in expected:
            number = protocol_key(str(record.get('number', '')))
            if number and number not in counts:
                add('missing-attachment', f'У переліку додатків листа «{letter.get("title", "")}» немає протоколу №{number}.', level='error', number=number)
        for number, count in counts.items():
            if count > 1:
                add('duplicate-attachment', f'У листі «{letter.get("title", "")}» протокол №{number} повторюється в додатках.', level='error', number=number)
    generated = {}
    actual_pages = {}
    for entry in files:
        is_output = entry['kind'].startswith('output_')
        is_cover_source = entry['kind'] == 'source' and entry['section'] == 'cover_letters'
        if not entry.get('active') or not (is_output or is_cover_source):
            continue
        current = sections.get(entry['section'])
        stale = is_output and current is not None and entry.get('fingerprint') != fingerprint(current)
        if stale:
            add('outdated-output', f'Файл «{entry["filename"]}» створено до останніх змін. Сформуйте його повторно.', entry['section'])
            continue
        if not (entry['section'] == 'acts' or is_cover_source) or not read_file:
            continue
        try:
            raw = read_file(entry['id'])
            if entry['filename'].lower().endswith('.zip'):
                with ZipFile(BytesIO(raw)) as archive:
                    members = archive.infolist()
                    if len(members) > 400 or sum(member.file_size for member in members) > 150 * 1024 * 1024:
                        raise ValueError('Завеликий архів створених протоколів.')
                    documents = [(member.filename, archive.read(member)) for member in members if member.filename.lower().endswith(('.docx', '.pdf')) and member.file_size <= 25 * 1024 * 1024]
            else:
                documents = [(entry['filename'], raw)]
            for filename, content in documents:
                if filename.lower().endswith('.docx') and is_output:
                    values = read_docx(content, filename).values
                    number = protocol_key(values.get('D', ''))
                    if number:
                        generated[number] = values
                elif filename.lower().endswith('.pdf'):
                    import fitz
                    with fitz.open(stream=content, filetype='pdf') as document:
                        text = '\n'.join(page.get_text() for page in document)
                        from .protocol_excel import extract_text
                        try:
                            number = protocol_key(extract_text(text, filename).values.get('D', ''))
                        except ValueError:
                            number = ''
                        if not number:
                            from pathlib import PurePosixPath
                            matches = [number for number, record in cover_numbers.items() if PurePosixPath(str(record.get('source', '')).replace('\\', '/')).stem.casefold() == PurePosixPath(filename.replace('\\', '/')).stem.casefold()]
                            number = matches[0] if len(matches) == 1 else ''
                        if number:
                            actual_pages[number] = len(document)
        except (ValueError, KeyError, BadZipFile, RuntimeError) as exc:
            add('unreadable-output', f'Не вдалося зіставити файл «{entry["filename"]}»: {exc}', 'acts' if is_output else 'cover_letters', 'error' if is_output else 'warning')
    for number, record in cover_numbers.items():
        original = generated.get(number)
        if original and day(original.get('E', '')) != day(record.get('date', '')):
            add('generated-cover-date', f'Протокол №{number}: дата у створеному Word-файлі та супровідних відрізняється.', level='error', number=number)
        if number in actual_pages:
            try:
                if int(record.get('pages') or 0) != actual_pages[number]:
                    add('actual-pages', f'Протокол №{number}: у PDF {actual_pages[number]} стор., у супровідних зазначено {record.get("pages")} арк.', level='error', number=number)
            except (ValueError, TypeError):
                pass
        elif record.get('pages_source') not in {'Word', 'LibreOffice', 'PDF'}:
            add('unverified-pages', f'Протокол №{number}: аркуші вказані вручну або з метаданих. Для точної перевірки порахуйте їх у Word/LibreOffice або звірте з PDF.', number=number)
    for record in excel.get('records') or []:
        values = record.get('values') or {}
        number = protocol_key(str(values.get('D', '')))
        other = cover_numbers.get(number)
        act_item = act_numbers.get(number)
        expected_date = other.get('date') if other else act_item.get('protocol_date') if act_item else ''
        if expected_date and day(values.get('E', '')) != day(expected_date):
            add('excel-date', f'Протокол №{number}: дата в Excel не збігається з іншими документами пакета.', 'excel', 'error', number)
        expected_supplier = supplier_code(str(act.get('common', {}).get('supplier', '')))
        if act_item and expected_supplier and values.get('M') and expected_supplier != str(values['M']):
            add('excel-supplier', f'Протокол №{number}: код постачальника в Excel та акті відрізняється.', 'excel', 'error', number)
    products = {re.sub(r'\W', '', str(item.get('product_name', '')).casefold()) for item in act_items}
    for item in assignments.get('items') or []:
        if products and re.sub(r'\W', '', str(item.get('product_name', '')).casefold()) not in products:
            add('assignment-outside-act', f'Зразок «{item.get("product_name", "")}» у завданнях не знайдено в поточному акті.', 'assignments')
    if not any(entry['kind'].startswith('output_') and entry.get('active') for entry in files):
        add('no-output', 'У пакеті ще немає створених документів. Відкрийте роботу й сформуйте потрібні файли.', 'acts')
    else:
        for section, values in sections.items():
            has_data = bool(values.get('items') or values.get('records') or values.get('letters'))
            if has_data and not any(entry['section'] == section and entry['kind'].startswith('output_') and entry.get('active') for entry in files):
                label = {'acts': 'Протоколи', 'assignments': 'Завдання', 'cover_letters': 'Супровідні', 'excel': 'Excel'}.get(section, section)
                add('missing-output', label + ': дані є в редакторі, але готових файлів у пакеті ще немає.', section)
    signature = fingerprint({'sections': sections, 'files': [{key: value for key, value in entry.items() if key != 'created_at'} for entry in files]})
    return {'issues': issues, 'errors': sum(issue['level'] == 'error' for issue in issues),
            'warnings': sum(issue['level'] == 'warning' for issue in issues), 'fingerprint': signature,
            'protocols': len(cover_numbers or act_numbers), 'letters': len(letters), 'verified_page_counts': len(actual_pages)}
