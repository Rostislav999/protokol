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


def paragraph(text='', *, size=28, bold=False, align=None):
    p = E.Element(Q('p')); pr = E.SubElement(p,Q('pPr'))
    E.SubElement(pr,Q('spacing'),{Q('after'):'0'})
    if align:
        E.SubElement(pr,Q('jc'),{Q('val'):align})
    r=E.SubElement(p,Q('r')); rp=E.SubElement(r,Q('rPr'))
    E.SubElement(rp,Q('rFonts'),{Q('ascii'):'Times New Roman',Q('hAnsi'):'Times New Roman',Q('cs'):'Times New Roman'})
    E.SubElement(rp,Q('sz'),{Q('val'):str(size)})
    if bold: E.SubElement(rp,Q('b'))
    E.SubElement(r,Q('t'),{'{http://www.w3.org/XML/1998/namespace}space':'preserve'}).text=text
    return p


def render_letter(records, batch, general):
    parts, _ = _parts((ASSETS / ('general.docx' if general else 'company.docx')).read_bytes())
    root=xml(parts['word/document.xml']); body=root.find('w:body', WN)
    drawing=root.find('.//w:drawing', WN)
    emblem=deepcopy(drawing) if drawing is not None else None
    section=deepcopy(body.find('w:sectPr', WN))
    for child in list(body): body.remove(child)
    # Inline two-column header keeps the stamp at the left and recipients at the right.
    table=E.SubElement(body,Q('tbl')); tp=E.SubElement(table,Q('tblPr'))
    E.SubElement(tp,Q('tblW'),{Q('w'):'0',Q('type'):'auto'})
    grid=E.SubElement(table,Q('tblGrid'))
    for width in (4800,4500): E.SubElement(grid,Q('gridCol'),{Q('w'):str(width)})
    row=E.SubElement(table,Q('tr'))
    left=E.SubElement(row,Q('tc')); right=E.SubElement(row,Q('tc'))
    for cell,width in ((left,4800),(right,4500)):
        pr=E.SubElement(cell,Q('tcPr')); E.SubElement(pr,Q('tcW'),{Q('w'):str(width),Q('type'):'dxa'})
    if emblem is not None:
        p=paragraph(align='center'); E.SubElement(p,Q('r')).append(emblem); left.append(p)
    for line in ('МІНІСТЕРСТВО ОБОРОНИ','УКРАЇНИ','ВІЙСЬКОВА ЧАСТИНА А0873','E-mail: a0873@mod.gov.ua','Код ЄДРПОУ 26614722','«____»________________20____','№________________________'):
        left.append(paragraph(line,size=22,bold=True,align='center'))
    recipients=['ДП МОУ «Агенція оборонних закупівель»','Управління безпечності харчових продуктів та ветеринарної медицини'] if general else [records[0].supplier]
    for text in recipients:
        right.append(paragraph(text)); right.append(paragraph())
    body.append(paragraph())
    text='Надсилаю на Вашу адресу протоколи випробувань на зразки, перевірені в межах випробувальних спроможностей лабораторії безпечності харчових продуктів військової частини А0873'
    if general:
        pairs=list(dict.fromkeys((r.supplier,r.code) for r in records))
        text+=', '+', '.join('постачальника '+name+(' – код постачальника '+code if code else '') for name,code in pairs)
    body.append(paragraph(text+'.'))
    body.append(paragraph())
    attachments=[f'Протокол випробувань №{r.number} від {r.date} року — на {r.pages} арк. в 1 прим.' for r in records]
    if general:
        attachments += [s.strip() for s in batch.extra_attachments.splitlines() if s.strip()]
        if batch.include_excel: attachments.append('Зведена таблиця у форматі Excel «Зведена інформація щодо протоколів випробувань».')
    for i,text in enumerate(attachments,1): body.append(paragraph(('Додаток: ' if i==1 else '')+f'{i}. '+text))
    body.append(paragraph());body.append(paragraph(batch.general_position if general else batch.company_position))
    body.append(paragraph(batch.general_signer if general else batch.company_signer))
    body.append(paragraph());body.append(paragraph(batch.contact,size=20))
    body.append(section)
    parts['word/document.xml']=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        for name,data in parts.items(): z.writestr(name,data)
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
