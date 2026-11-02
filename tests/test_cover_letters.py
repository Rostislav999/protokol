"""Run with python -m unittest discover -s tests -p test_cover_letters.py."""
import json
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi.testclient import TestClient
from lxml import etree as E

from app.main import create_app
from app.cover_letters import LetterBatch, generate, prepare, workbook_records
from app.protocol_excel import Record, write_excel_bytes, W


class CoverLettersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.temp.name)))
        self.records = [dict(number='87-015-2920-1232-2',date='19.10.2026',supplier='ТОВ «Фірма А»',code='015',pages=3),
                        dict(number='87-025-2932-1233-2',date='20.10.2026',supplier='ТОВ «Фірма Б»',code='025',pages=5),
                        dict(number='87-015-2920-1234-2',date='21.10.2026',supplier='ТОВ «Фірма А»',code='015',pages=2)]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def text(self, data):
        with ZipFile(BytesIO(data)) as z:
            root = E.fromstring(z.read('word/document.xml'))
            self.assertEqual(len(root.findall('.//{'+W+'}drawing')),1)
            # Word may split a single word across several formatting runs.
            return '\n'.join(''.join(p.itertext()) for p in root.findall('.//{'+W+'}p'))

    def protocol(self):
        response=self.client.post('/api/generate',json={
            'confirmed':True,'common':{'customer':'Замовник','supplier':'ТОВ «Фірма А»','sampling_description':'Одеса'},
            'items':[{'id':'one','protocol_number':self.records[0]['number'],'protocol_date':'19.10.2026',
                      'product_name':'Свинина тушкована','template_id':'sample-1053'}]})
        self.assertEqual(response.status_code,200,response.text if response.status_code!=200 else '')
        with ZipFile(BytesIO(response.content)) as z:
            name=next(n for n in z.namelist() if n.endswith('.docx'))
            return name,z.read(name)

    def test_exports_general_and_only_matching_company_protocols(self):
        response=self.client.post('/api/cover-letters/generate',data={'payload':json.dumps({'records':self.records,'confirmed':True,'extra_attachments':'Додатковий документ на 1 арк.'})})
        self.assertEqual(response.status_code,200,response.text if response.status_code!=200 else '')
        with ZipFile(BytesIO(response.content)) as z:
            self.assertEqual(len(z.namelist()),3)
            general=self.text(z.read(z.namelist()[0]))
            self.assertIn('Агенція оборонних закупівель',general)
            self.assertIn('Управління безпечності харчових продуктів',general)
            self.assertIn('Код ЄДРПОУ 26614722',general)
            self.assertIn('E-mail: a0873@mod.gov.ua',general)
            self.assertIn('Додатковий документ',general)
            for r in self.records: self.assertIn(r['number'],general)
            a=self.text(z.read(z.namelist()[1]));b=self.text(z.read(z.namelist()[2]))
            self.assertIn(self.records[2]['number'],a)
            self.assertNotIn(self.records[1]['number'],a)
            self.assertNotIn(self.records[0]['number'],b)
            self.assertNotIn('Зведена таблиця',a)
            self.assertNotIn('Додатковий документ',a)
            self.assertIn('на 5 арк.',b)

    def test_rejects_unconfirmed_duplicate_date_and_missing_pages(self):
        for change in ({'confirmed':False},{'records':[self.records[0],self.records[0]]},
                       {'records':[{**self.records[0],'date':'31.02.2026'}]},
                       {'records':[{**self.records[0],'pages':None}]},
                       {'records':[{**self.records[0],'supplier':'00123456'}]}):
            payload={'records':self.records,'confirmed':True,**change}
            r=self.client.post('/api/cover-letters/generate',data={'payload':json.dumps(payload)})
            self.assertEqual(r.status_code,422,r.text)

    def test_import_protocol_and_excel_same_request(self):
        name,data=self.protocol()
        book,_=write_excel_bytes(None,[Record('test',{'D':self.records[0]['number'],'E':'20.10.2026','M':'00123456'})])
        self.assertEqual(workbook_records(book)[self.records[0]['number']]['E'],'20.10.2026')
        r=self.client.post('/api/cover-letters/import',files=[('files',(name,data)),('files',('table.xlsx',book))])
        self.assertEqual(r.status_code,200,r.text)
        body=r.json();self.assertTrue(body['has_excel'])
        self.assertEqual(body['records'][0]['number'],self.records[0]['number'])
        self.assertEqual(body['records'][0]['date'],'20.10.2026')
        self.assertEqual(body['records'][0]['supplier'],'')
        self.assertTrue(body['warnings'])

    def test_import_zip_and_directory_resolves_company(self):
        name,data=self.protocol()
        out=BytesIO()
        with ZipFile(out,'w',ZIP_DEFLATED) as z: z.writestr('../'+name,data)
        result=prepare([('batch.zip',out.getvalue())],[{'code':'015','name':'ТОВ «Фірма А»','edrpou':'00123456'}])
        self.assertEqual(result['records'][0]['supplier'],'ТОВ «Фірма А»')
        self.assertEqual(result['records'][0]['code'],'015')
        self.assertNotIn('/',result['records'][0]['source'])

    def test_rejects_empty_bad_and_duplicate_files(self):
        for files in ([('files',('bad.docx',b'not a docx'))],[],[('files',('x.exe',b'x'))]):
            r=self.client.post('/api/cover-letters/import',files=files)
            self.assertEqual(r.status_code,422,r.text)
        name,data=self.protocol()
        r=self.client.post('/api/cover-letters/import',files=[('files',(name,data)),('files',('copy.docx',data))])
        self.assertEqual(r.status_code,422,r.text)

    def test_static_script_is_served_and_loaded(self):
        self.assertIn('/static/cover-letters.js',self.client.get('/').text)
        self.assertEqual(self.client.get('/static/cover-letters.js').status_code,200)

    def test_preview_and_independent_letter_edits_preserve_word_layout(self):
        payload={'records':self.records,'confirmed':False}
        response=self.client.post('/api/cover-letters/preview',data={'payload':json.dumps(payload)})
        self.assertEqual(response.status_code,200,response.text)
        letters=response.json()['letters']
        self.assertEqual(len(letters),3)
        self.assertEqual(letters[0]['id'],'general')
        general=letters[0]['content']
        general.update(recipients=['Перший адресат','Другий адресат','Третій адресат'],
                       body=['Виправлений абзац & <текст>.','','Ще один абзац.'],
                       attachments=['Лише потрібний додаток на 2 арк.'],
                       position='Начальник установи',signer='підполковник Ірина ПРИКЛАД',contact='Контакт 00 000')
        company=letters[1]['content']
        company.update(recipients=['Інший адресат фірми'],body=['Окремий текст для фірми.'],attachments=[])
        payload['edits']={letters[0]['id']:general,letters[1]['id']:company}
        # Previewing and editing never substitutes for final confirmation.
        self.assertEqual(self.client.post('/api/cover-letters/generate',data={'payload':json.dumps(payload)}).status_code,422)
        payload['confirmed']=True
        result=self.client.post('/api/cover-letters/generate',data={'payload':json.dumps(payload)})
        self.assertEqual(result.status_code,200,result.text if result.status_code!=200 else '')
        with ZipFile(BytesIO(result.content)) as archive:
            general_data=archive.read(letters[0]['filename'])
            generated=self.text(general_data)
            for expected in ('Перший адресат','Третій адресат','Виправлений абзац & <текст>.',
                             'Ще один абзац.','Додаток: 1. Лише потрібний додаток','Контакт 00 000'):
                self.assertIn(expected,generated)
            self.assertNotIn('Надсилаю на Вашу адресу',generated)
            company_text=self.text(archive.read(letters[1]['filename']))
            self.assertIn('Окремий текст для фірми.',company_text)
            self.assertNotIn('Додаток:',company_text)
            self.assertNotIn('Виправлений абзац',company_text)
            untouched=self.text(archive.read(letters[2]['filename']))
            self.assertIn(self.records[1]['number'],untouched)
            self.assertNotIn('Окремий текст для фірми.',untouched)
        with ZipFile(BytesIO(general_data)) as archive:
            root=E.fromstring(archive.read('word/document.xml'))
            paragraphs=root.findall('.//{'+W+'}body/{'+W+'}p')
            opening=next(p for p in paragraphs if ''.join(p.itertext()).startswith('Виправлений абзац'))
            self.assertEqual(opening.find('{'+W+'}pPr/{'+W+'}ind').get('{'+W+'}firstLine'),'284')
            self.assertEqual(''.join(opening.getnext().itertext()),'')
            margins=root.find('{'+W+'}body/{'+W+'}sectPr/{'+W+'}pgMar')
            self.assertEqual(margins.get('{'+W+'}left'),'1701')
            self.assertEqual(margins.get('{'+W+'}right'),'567')

    def test_rejects_stale_edits_and_empty_letter_text(self):
        payload={'records':self.records,'confirmed':True}
        letter=self.client.post('/api/cover-letters/preview',data={'payload':json.dumps(payload)}).json()['letters'][0]
        for edits in ({'deleted-company':letter['content']},
                      {'general':{**letter['content'],'body':[' ']}},
                      {'general':{**letter['content'],'recipients':['']}}):
            response=self.client.post('/api/cover-letters/generate',data={'payload':json.dumps({**payload,'edits':edits})})
            self.assertEqual(response.status_code,422,response.text)


if __name__=='__main__': unittest.main()
