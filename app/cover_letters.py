"""Reviewable cover letters for a selected batch of protocols; no compliance inference."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from lxml import etree as E

from .excel_api import _read_record
from .protocol_excel import _parts, xml, cell_text, part_path, NS, R, W, protocol_key, date_text, supplier_code

ASSETS = Path(__file__).resolve().parent.parent / 'assets/cover_letters'
WN = {'w': W}
Q = lambda name: '{' + W + '}' + name


class LetterRecord(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, str_max_length=2000)
    source: str = ''
    number: str = Field(min_length=1, max_length=100)
    date: str = Field(min_length=1, max_length=100)
    supplier: str = Field(min_length=1, max_length=500)
    code: str = Field(default='', max_length=10)
    pages: int = Field(ge=1, le=10000)

    @field_validator('date')
    @classmethod
    def valid_date(cls, value):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return datetime.strptime(value, '%Y-%m-%d').strftime('%d.%m.%Y')
        result = date_text(value)
        if not result:
            raise ValueError('Укажіть коректну дату протоколу.')
        return result

    @field_validator('supplier')
    @classmethod
    def named_supplier(cls, value):
        if value == '[НЕ ЗАПОВНЕНО]' or re.fullmatch(r'[\d\s]+', value):
            raise ValueError('Укажіть назву фірми, а не лише її код.')
        return value


class LetterBatch(BaseModel):
    records: list[LetterRecord] = Field(min_length=1, max_length=100)
    confirmed: bool = False
    general_position: str = Field(default='Командир військової частини А0873', min_length=1, max_length=1000)
    general_signer: str = Field(default='полковник Олександр КАЛЮЖНИЙ', min_length=1, max_length=500)
    company_position: str = Field(default='Тимчасово виконуючий обов’язки начальника лабораторії безпечності харчових продуктів - заступника командира військової частини А0873', min_length=1, max_length=1000)
    company_signer: str = Field(default='майор Ростислав АДРІАНОВ', min_length=1, max_length=500)
    contact: str = Field(default='', max_length=500)
    extra_attachments: str = Field(default='', max_length=3000)
    include_excel: bool = True


def workbook_records(data):
    parts, _ = _parts(data)
    book = xml(parts['xl/workbook.xml'])
    rels = {r.get('Id'): part_path('xl/workbook.xml', r.get('Target')) for r in xml(parts['xl/_rels/workbook.xml.rels'])}
    strings = []
    if 'xl/sharedStrings.xml' in parts:
        strings = [''.join(si.itertext()) for si in xml(parts['xl/sharedStrings.xml'])]
    result = {}
    for sheet in book.findall('s:sheets/s:sheet', NS):
        if sheet.get('state', 'visible') != 'visible':
            continue
        for row in xml(parts[rels[sheet.get('{'+R+'}id')]]).findall('s:sheetData/s:row', NS):
            cells = {re.sub(r'\d+$', '', c.get('r', '')): c for c in row}
            values = {col: cell_text(c, strings) for col, c in cells.items()}
            number = protocol_key(values.get('D', ''))
            if not re.fullmatch(r'\d+(?:-\d+){2,}', number):
                continue
            if number in result:
                raise ValueError('В Excel повторюється номер протоколу: ' + number)
            raw = values.get('E', '')
            if raw and cells['E'].get('t') not in ('s', 'inlineStr'):
                try:
                    epoch = datetime(1904, 1, 1) if book.find('s:workbookPr', NS) is not None and book.find('s:workbookPr', NS).get('date1904') in ('1','true') else datetime(1899, 12, 30)
                    values['E'] = (epoch + timedelta(days=float(raw))).strftime('%d.%m.%Y')
                except (ValueError, OverflowError):
                    pass
            result[number] = values
    if not result:
        raise ValueError('В Excel не знайдено протоколів у стовпцях D (номер), E (дата), M (постачальник).')
    return result


def prepare(files, suppliers):
    records, warnings, documents, books = [], [], [], []
    expanded = 0
    for name, data in files:
        if name.lower().endswith('.xlsx'):
            books.append(data)
        elif name.lower().endswith('.zip'):
            with ZipFile(BytesIO(data)) as z:
                infos = z.infolist()
                if len(infos) > 3000 or sum(i.file_size for i in infos) > 150*1024*1024:
                    raise ValueError('ZIP надто великий після розпакування.')
                if len({i.filename for i in infos}) != len(infos):
                    raise ValueError('ZIP містить дублікати файлів.')
                for i in infos:
                    if not i.is_dir() and i.filename.lower().endswith(('.docx','.pdf')):
                        if i.file_size > 25*1024*1024 or i.flag_bits & 1:
                            raise ValueError('Завеликий або захищений файл у ZIP.')
                        documents.append((Path(i.filename.replace('\\','/')).name, z.read(i)))
        else:
            documents.append((name, data))
    if len(books) > 1 or not 1 <= len(documents) <= 100:
        raise ValueError('Оберіть 1–100 протоколів та не більше однієї таблиці XLSX.')
    table = workbook_records(books[0]) if books else {}
    seen = set()
    for name, data in documents:
        expanded += len(data)
        if expanded > 150*1024*1024:
            raise ValueError('Комплект перевищує 150 МБ після розпакування.')
        record = _read_record(data, name)
        values = record.values
        number = protocol_key(values.get('D',''))
        if number in seen:
            raise ValueError('Повторюється номер протоколу: ' + number)
        seen.add(number)
        cells = table.get(number, {})
        if books and not cells:
            warnings.append(name + ': номер не знайдено в Excel; перевірте дані з протоколу.')
        for col in ('E','M'):
            if cells.get(col) and values.get(col) and cells[col] != values[col]:
                warnings.append(name + ': значення ' + col + ' в Excel та протоколі відрізняються; показано Excel.')
        supplier = cells.get('M') or values.get('M','')
        code = number.split('-')[1] if re.fullmatch(r'\d+(?:-\d+){2,}', number) else ''
        matches = [s for s in suppliers if (s.get('edrpou') and s['edrpou'] == supplier_code(supplier)) or s.get('code') == code]
        if len({s['name'] for s in matches}) == 1:
            supplier = matches[0]['name']
        if supplier_code(supplier) == supplier.strip() or supplier == '[НЕ ЗАПОВНЕНО]':
            supplier = ''
        pages = None
        if name.lower().endswith('.pdf'):
            import fitz
            with fitz.open(stream=data, filetype='pdf') as pdf:
                pages = len(pdf)
        else:
            parts, _ = _parts(data)
            if 'docProps/app.xml' in parts:
                nodes = xml(parts['docProps/app.xml']).xpath('//*[local-name()="Pages"]/text()')
                if nodes and nodes[0].isdigit() and int(nodes[0]) > 0:
                    pages = int(nodes[0])
            warnings.append(name + ': перевірте кількість аркушів DOCX за оригіналом; метадані можуть бути застарілими.')
        records.append(dict(source=name, number=number, date=cells.get('E') or values.get('E',''), supplier=supplier, code=code, pages=pages))
    return dict(records=records, warnings=warnings, has_excel=bool(books))


def _paragraph_text(paragraph):
    return ''.join(paragraph.xpath('.//w:t/text()', namespaces=WN))


def _replace_text_range(paragraph, start, end, value):
    """Edit text across Word runs without removing their formatting or tabs."""
    nodes = paragraph.findall('.//w:t', WN)
    if not nodes:
        run = E.SubElement(paragraph, Q('r'))
        properties = paragraph.find('w:pPr/w:rPr', WN)
        if properties is not None:
            run.append(deepcopy(properties))
        nodes = [E.SubElement(run, Q('t'))]
    offset = 0
    inserted = False
    for node in nodes:
        old = node.text or ''
        node_start, node_end = offset, offset + len(old)
        offset = node_end
        if start == end:
            if inserted or not node_start <= start <= node_end:
                continue
        elif node_end <= start or node_start >= end:
            continue
        left, right = max(0, start - node_start), min(len(old), end - node_start)
        node.text = old[:left] + (value if not inserted else '') + old[right:]
        if node.text[:1].isspace() or node.text[-1:].isspace():
            node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        inserted = True


def _fill_text(paragraph, value):
    """Keep unchanged prefixes/suffixes in their original runs."""
    old = _paragraph_text(paragraph)
    if old == value:
        return
    left = 0
    while left < min(len(old), len(value)) and old[left] == value[left]:
        left += 1
    right = 0
    while (right < min(len(old), len(value)) - left
           and old[len(old) - right - 1] == value[len(value) - right - 1]):
        right += 1
    _replace_text_range(paragraph, left, len(old) - right, value[left:len(value) - right])


def _fill_signer(paragraph, value, text_width):
    # Leave the supplied signature intact; edited ranks/names need a real tab
    # stop instead of a fixed number of spaces, which can wrap a longer name.
    if ' '.join(_paragraph_text(paragraph).split()) == ' '.join(value.split()):
        return
    new = re.fullmatch(r'(.+?)\s+([A-ZА-ЯІЇЄҐ].*)', value.strip())
    if new is None:
        new = re.fullmatch(r'(\S+)\s+(.+)', value.strip())
    if new is None:
        _fill_text(paragraph, value)
        return
    runs = paragraph.findall('w:r', WN)
    first_properties, last_properties = runs[0].find('w:rPr', WN), runs[-1].find('w:rPr', WN)
    for run in runs:
        paragraph.remove(run)
    for i, (text, properties) in enumerate(zip(new.groups(), (first_properties, last_properties))):
        run = E.SubElement(paragraph, Q('r'))
        if properties is not None:
            run.append(deepcopy(properties))
        E.SubElement(run, Q('t')).text = text
        if i == 0:
            E.SubElement(run, Q('tab'))
    properties = paragraph.find('w:pPr', WN)
    tabs = properties.find('w:tabs', WN)
    if tabs is None:
        tabs = E.Element(Q('tabs'))
        properties.insert(0, tabs)
    else:
        for tab in list(tabs):
            tabs.remove(tab)
    E.SubElement(tabs, Q('tab'), {Q('val'): 'right', Q('pos'): str(text_width)})


def _attachment_paragraph(source, value):
    paragraph = deepcopy(source)
    # IDs and editing bookmarks belong to the source paragraph, not its copies.
    for key in ('paraId', 'textId'):
        paragraph.attrib.pop('{http://schemas.microsoft.com/office/word/2010/wordml}' + key, None)
    for bookmark in paragraph.xpath('.//w:bookmarkStart | .//w:bookmarkEnd', namespaces=WN):
        bookmark.getparent().remove(bookmark)
    _fill_text(paragraph, value)
    return paragraph


def render_letter(records, batch, general):
    parts, infos = _parts((ASSETS / ('general.docx' if general else 'company.docx')).read_bytes())
    root = xml(parts['word/document.xml'])
    body = root.find('w:body', WN)
    paragraphs = body.findall('w:p', WN)
    introduction = next(p for p in paragraphs if _paragraph_text(p).startswith('Надсилаю на Вашу адресу'))
    first = next(p for p in paragraphs if _paragraph_text(p).startswith('Додаток:'))
    start = paragraphs.index(first)
    attachments = []
    for p in paragraphs[start:]:
        if not re.match(r'^(?:Додаток:\s*)?\d+\.\s', _paragraph_text(p)):
            break
        attachments.append(p)
    continuation = attachments[1]
    extra = next((p for p in attachments if 'Інформація про харчові продукти' in _paragraph_text(p)), continuation)
    excel = next((p for p in attachments if 'Зведена таблиця' in _paragraph_text(p)), continuation)

    # Keep the entire stationery, including the floating stamp, recipient
    # placement, blank paragraphs, section margins, and original run properties.
    stamp = body.find('w:tbl', WN)
    email = next(p for p in stamp.findall('.//w:p', WN) if 'E-mail:' in _paragraph_text(p))
    _fill_text(email.getnext(), 'Код ЄДРПОУ 26614722')
    if general:
        pairs = list(dict.fromkeys((r.supplier, r.code) for r in records))
        suppliers = ', '.join('постачальника ' + name + (' – код постачальника ' + code if code else '')
                              for name, code in pairs)
        text = _paragraph_text(introduction).split(', постачальника', 1)[0]
        _fill_text(introduction, text + ', ' + suppliers + '.')
    else:
        recipient = next(p for p in paragraphs[:paragraphs.index(introduction)] if _paragraph_text(p))
        _fill_text(recipient, records[0].supplier)

    items = [(first if i == 0 else continuation,
              f'Протокол випробувань №{r.number} від {r.date} року — на {r.pages} арк. в 1 прим.')
             for i, r in enumerate(records)]
    if general:
        items += [(extra, s.strip()) for s in batch.extra_attachments.splitlines() if s.strip()]
        if batch.include_excel:
            items.append((excel, 'Зведена таблиця у форматі Excel «Зведена інформація щодо протоколів випробувань».'))
    # Rebuild only the variable list, cloning its first/continuation styles.
    # Include any bookmarks between those paragraphs in the replaced range.
    insert_at, end = body.index(first), body.index(attachments[-1])
    for child in list(body)[insert_at:end + 1]:
        body.remove(child)
    for i, (source, text) in enumerate(items, 1):
        value = ('Додаток: ' if i == 1 else '') + f'{i}. ' + text
        body.insert(insert_at + i - 1, _attachment_paragraph(source, value))

    position, signer, contact = [p for p in paragraphs[start + len(attachments):] if _paragraph_text(p)]
    _fill_text(position, batch.general_position if general else batch.company_position)
    section = body.find('w:sectPr', WN)
    margins = section.find('w:pgMar', WN)
    text_width = (int(section.find('w:pgSz', WN).get(Q('w')))
                  - int(margins.get(Q('left'))) - int(margins.get(Q('right'))))
    _fill_signer(signer, batch.general_signer if general else batch.company_signer, text_width)
    # Let Word paginate a long list without separating the position and signature.
    for paragraph in (position, signer):
        properties = paragraph.find('w:pPr', WN)
        if properties.find('w:keepLines', WN) is None:
            properties.insert(0, E.Element(Q('keepLines')))
        if paragraph is position and properties.find('w:keepNext', WN) is None:
            properties.insert(0, E.Element(Q('keepNext')))
    _fill_text(contact, batch.contact)
    parts['word/document.xml'] = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as z:
        for info in infos:
            z.writestr(info, parts[info.filename])
    return out.getvalue()


def generate(batch):
    if not batch.confirmed: raise ValueError('Підтвердьте перевірку реквізитів і кількості аркушів.')
    numbers=[protocol_key(r.number) for r in batch.records]
    if any(not re.fullmatch(r'\d+(?:-\d+){2,}', n) for n in numbers) or len(numbers)!=len(set(numbers)):
        raise ValueError('Номери протоколів мають бути коректними й унікальними.')
    groups=OrderedDict()
    for r,n in zip(batch.records,numbers):
        r.number=n
        groups.setdefault(' '.join(r.supplier.casefold().split()),[]).append(r)
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        z.writestr('Загальна_супровідна.docx',render_letter(batch.records,batch,True))
        for i,records in enumerate(groups.values(),1):
            name=re.sub(r'[^\w .«»()-]','_',records[0].supplier)[:100].strip(' .')
            z.writestr(f'{i:02d}_Супровід_{name}.docx',render_letter(records,batch,False))
    return out.getvalue()


def cover_letters_router(settings):
    router=APIRouter(prefix='/api/cover-letters',tags=['cover-letters'])

    @router.post('/import')
    async def read(files: list[UploadFile]=File(...)):
        try:
            if not 1<=len(files)<=101: raise ValueError('Оберіть до 100 протоколів та одну XLSX.')
            uploaded=[];total=0
            for f in files:
                name=Path((f.filename or '').replace('\\','/')).name
                if not name.lower().endswith(('.docx','.pdf','.zip','.xlsx')): raise ValueError('Підтримуються DOCX, PDF, ZIP і XLSX.')
                data=await f.read(25*1024*1024+1);total+=len(data)
                if not data or len(data)>25*1024*1024 or total>100*1024*1024: raise ValueError('До 25 МБ на файл і 100 МБ на комплект.')
                uploaded.append((name,data))
            return await run_in_threadpool(prepare,uploaded,settings.all()['suppliers'])
        except (ValueError, KeyError, IndexError, OSError, BadZipFile, RuntimeError) as exc:
            raise HTTPException(422,str(exc)) from exc
        finally:
            for f in files: await f.close()

    @router.post('/generate')
    async def export(payload: str=Form(...)):
        try:
            if len(payload)>3*1024*1024: raise ValueError('Дані комплекту завеликі.')
            batch=LetterBatch.model_validate_json(payload)
            data=await run_in_threadpool(generate,batch)
            return Response(data,media_type='application/zip',headers={'Content-Disposition':"attachment; filename*=UTF-8''%D0%A1%D1%83%D0%BF%D1%80%D0%BE%D0%B2%D1%96%D0%B4%D0%BD%D1%96.zip"})
        except (ValueError,ValidationError) as exc:
            raise HTTPException(422,str(exc)) from exc
    return router
