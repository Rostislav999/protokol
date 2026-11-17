from copy import deepcopy
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
import fitz
import pytest

from app.act_review import add_review
from app.extraction import parse_act
from app.main import create_app
from app.workbench_store import fingerprint


def text(count):
    return '№ | Найменування продукції | Маса партії\n'+'\n'.join(f'{i+1} | Свинина тушкована | 100 кг' for i in range(count))


def test_review_combines_physical_rows_and_keeps_page_and_row_association():
    pages = [{'page':i+1, 'angle':90, 'text':text(count), 'detected_rows':count,
              'locations':[{'bbox':[.1,.2,.9,.4]} for _ in range(count)]} for i,count in enumerate([1,4])]
    source = {'page_evidence':pages}
    parsed = parse_act('\n'.join(page['text'] for page in pages))
    add_review(source, parsed)
    assert parsed['audit']['detected'] == parsed['audit']['parsed'] == 5
    assert [item['source_location']['page'] for item in parsed['items']] == [1,2,2,2,2]
    assert parsed['items'][0]['source_location']['bbox'] == [.1,.2,.9,.4]
    assert 'page_evidence' not in source


def test_missing_rows_remain_visible_and_ambiguous_associations_are_not_invented():
    source = {'page_evidence':[{'page':1,'text':text(5),'detected_rows':5,'locations':[]}]}
    parsed = parse_act(text(2))
    add_review(source, parsed)
    assert parsed['audit']['detected'] == 5 and parsed['audit']['parsed'] == 2
    assert all('source_location' not in item for item in parsed['items'])


def test_equal_counts_do_not_associate_different_sample_identities():
    source={'page_evidence':[{'page':1,'text':text(1),'detected_rows':1}]}
    parsed=parse_act(text(1).replace('Свинина тушкована','Яловичина тушкована'))
    add_review(source,parsed)
    assert 'source_location' not in parsed['items'][0]


def test_unruled_page_does_not_claim_the_total_has_been_checked():
    source = {'page_evidence':[{'page':1,'text':text(2),'detected_rows':2},
                               {'page':2,'text':text(1),'detected_rows':None}]}
    parsed = parse_act(text(3))
    add_review(source, parsed)
    assert parsed['audit']['detected'] is None
    assert len(parsed['audit']['pages']) == 2


def test_grid_counts_a_physical_row_even_if_its_product_cell_was_not_read(monkeypatch):
    pytest.importorskip('cv2')
    from PIL import ImageDraw
    from app import extraction
    image=Image.new('RGB',(1050,700),'white');draw=ImageDraw.Draw(image)
    xs,ys=[40,80,400,620,770,1010],[60,180,340,500]
    for x in xs:draw.line((x,ys[0],x,ys[-1]),fill='black',width=3)
    for y in ys:draw.line((xs[0],y,xs[-1],y),fill='black',width=3)
    values=iter(['№','Найменування продукції','Виробник','Дата виготовлення','Маса партії',
                 'Свинина','ТОВ А','01.11.2026','100 кг',
                 '', 'ТОВ Б','02.11.2026','200 кг'])
    monkeypatch.setattr(extraction,'_ocr_image',lambda *a,**k:next(values))
    table=extraction._ocr_table(image,'ukr')
    assert table is not None and table['detected_rows']==2 and table['rows']==1
    parsed=parse_act(table['text'])
    add_review({'page_evidence':[{'page':1,**table}]},parsed)
    assert parsed['audit']['detected']==2 and parsed['audit']['parsed']==1


def upload(client, scope, data, name):
    response = client.post('/api/workbench/files',files={'file':(name,data)},
                           data={'scope':scope,'section':'acts','kind':'source'})
    assert response.status_code == 200,response.text
    return response.json()['id']


def test_original_preview_pages_rotation_limits_and_scope(tmp_path):
    with TestClient(create_app(tmp_path)) as client, fitz.open() as document:
        for color in [(1,0,0),(0,0,1)]:
            page = document.new_page(width=2480,height=3507)
            page.draw_rect(page.rect,fill=color,color=color)
        original = document.tobytes()
        first = client.post('/api/packages',json={'title':'Перший'}).json()['id']
        other = client.post('/api/packages',json={'title':'Другий'}).json()['id']
        file_id = upload(client,first,original,'скан.pdf')
        route = f'/api/workbench/files/{file_id}/preview'
        result = client.get(route,params={'scope':first,'page':2,'rotation':90})
        assert result.status_code == 200,result.text
        assert result.headers['x-page-count'] == '2'
        assert result.headers['cache-control'] == 'no-store'
        with Image.open(BytesIO(result.content)) as image:
            assert image.width > image.height and max(image.size) <= 2200
            assert image.getpixel((image.width//2,image.height//2))[2] > 240
        assert client.get(route,params={'scope':other}).status_code == 404
        for values in [{'page':0},{'page':3},{'rotation':45}]:
            assert client.get(route,params={'scope':first,**values}).status_code == 422
        assert client.get(f'/api/workbench/files/{file_id}',params={'scope':first}).content == original


@pytest.mark.parametrize('name,data', [('broken.pdf',b'not a pdf'),('text.txt',b'text')])
def test_bad_or_unsupported_preview_has_an_actionable_response(tmp_path,name,data):
    with TestClient(create_app(tmp_path)) as client:
        file_id=upload(client,'workspace',data,name)
        response=client.get(f'/api/workbench/files/{file_id}/preview')
        assert response.status_code == 422
        assert isinstance(response.json()['detail'],str)


def test_review_and_location_changes_do_not_make_generated_documents_outdated():
    draft={'items':[{'product_name':'Свинина','source_location':{'page':1}}], 'audit':{'detected':5}}
    changed=deepcopy(draft);changed['audit_expected']=5;changed['audit']={'detected':None};changed['items'][0]['source_location']={'page':2}
    assert fingerprint(draft)==fingerprint(changed)
    changed['items'][0]['product_name']='Яловичина'
    assert fingerprint(draft)!=fingerprint(changed)


def test_repeat_ocr_crops_rotated_original_without_changing_saved_work(tmp_path, monkeypatch):
    from app import extraction
    from PIL import ImageDraw
    image = Image.new('RGB', (600, 400), 'red')
    ImageDraw.Draw(image).rectangle((300, 0, 599, 399), fill='blue')
    stream = BytesIO(); image.save(stream, 'PNG'); original = stream.getvalue()
    seen = []
    monkeypatch.setattr(extraction, '_ocr_languages', lambda: ('ukr+rus+eng', []))
    def recognize(crop, language, psm):
        seen.append((crop.size, crop.getpixel((crop.width//2, crop.height//2)), language, psm))
        return 'ДСТУ 4450:2005'
    monkeypatch.setattr(extraction, '_ocr_image', recognize)
    with TestClient(create_app(tmp_path)) as client:
        scope = client.post('/api/packages', json={'title':'Ручні правки'}).json()['id']
        other = client.post('/api/packages', json={'title':'Інший акт'}).json()['id']
        file_id = upload(client, scope, original, 'акт.png')
        draft = {'items':[{'product_name':'Ручна назва','standard':'Старий НД','template_id':'sample-1053'}]}
        assert client.put(f'/api/packages/{scope}/drafts/acts', json={'data':draft,'expected_revision':0}).status_code == 200
        saved = client.get(f'/api/packages/{scope}').json()
        route = f'/api/workbench/files/{file_id}/reocr'
        values = {'scope':scope,'page':1,'rotation':90,'bbox':[0,.5,1,1],'psm':7}
        response = client.post(route, json=values)
        assert response.status_code == 200, response.text
        assert response.json() == {'text':'ДСТУ 4450:2005','page':1}
        assert seen == [((832,632),(255,0,0),'ukr+rus+eng',7)]
        assert client.get(f'/api/packages/{scope}').json() == saved
        assert client.get(f'/api/workbench/files/{file_id}', params={'scope':scope}).content == original
        assert client.post(route, json={**values,'scope':other}).status_code == 404
        assert len(seen) == 1


@pytest.mark.parametrize('changes', [
    {'bbox':[-.01,0,1,1]}, {'bbox':[0,0,1.01,1]}, {'bbox':[.7,0,.2,1]},
    {'bbox':[0,.5,1,.5]}, {'bbox':[0,0,1]}, {'rotation':45},
    {'page':0}, {'page':21}, {'psm':3}, {'unexpected':True},
])
def test_repeat_ocr_rejects_invalid_regions_before_reading(tmp_path, monkeypatch, changes):
    from app import packages
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid OCR input reached the OCR engine')
    monkeypatch.setattr(packages, 'reocr_fragment', unexpected)
    with TestClient(create_app(tmp_path)) as client:
        file_id = upload(client, 'workspace', b'not needed', 'акт.png')
        values = {'scope':'workspace','page':1,'bbox':[0,0,1,1],**changes}
        assert client.post(f'/api/workbench/files/{file_id}/reocr', json=values).status_code == 422


def test_repeat_ocr_handles_empty_result_and_rejects_tiny_or_excessive_crops(tmp_path, monkeypatch):
    from app import extraction
    image = Image.new('RGB', (400, 200), 'white')
    stream = BytesIO(); image.save(stream, 'PNG')
    monkeypatch.setattr(extraction, '_ocr_languages', lambda: ('eng', []))
    monkeypatch.setattr(extraction, '_ocr_image', lambda *a, **k: '')
    with TestClient(create_app(tmp_path)) as client:
        file_id = upload(client, 'workspace', stream.getvalue(), 'blank.png')
        route = f'/api/workbench/files/{file_id}/reocr'
        values = {'scope':'workspace','page':1,'bbox':[0,0,1,1]}
        assert client.post(route, json=values).json()['text'] == ''
        for changes in [{'page':2}, {'bbox':[0,0,.01,.01]}]:
            assert client.post(route, json={**values,**changes}).status_code == 422
        monkeypatch.setattr(extraction, '_ocr_image', lambda *a, **k: 'x'*12001)
        assert client.post(route, json=values).status_code == 422


def test_repeat_ocr_refuses_generated_documents_and_unsupported_sources(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        values = {'scope':'workspace','page':1,'bbox':[0,0,1,1]}
        for name, data, kind in [('акт.txt',b'text','source'), ('broken.pdf',b'broken','source'), ('result.docx',b'output','output_word')]:
            response = client.post('/api/workbench/files', files={'file':(name,data)},
                                   data={'scope':'workspace','section':'acts','kind':kind})
            assert response.status_code == 200, response.text
            route = f'/api/workbench/files/{response.json()["id"]}/reocr'
            assert client.post(route, json=values).status_code == 422
