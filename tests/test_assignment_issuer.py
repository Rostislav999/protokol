"""An explicitly selected author replaces only the assignment's issuer line."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from app.assignments_documents import _load, inspect_assignment_template, render_assignment
from app.documents import TemplateError

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = sorted((ROOT / "assets/assignments").glob("*.docx"))
NS = {"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
SAMPLE_FIELDS = {"sample_number":"12345", "product_name":"Новий зразок", "sample_weight":"1,2 кг",
                 "standard":"ДСТУ 9999:2026", "issue_date":"2026-10-29"}
ISSUER = {"issuer_name":"Олександр ТЕСТ & Перевірка", "issuer_position":"старший лейтенант"}


def structure_except_fields(data, *, include_issuer):
    doc = _load(data)
    editable = {id(p) for target in doc.targets + (doc.issuer_targets if include_issuer else [])
                for p in target.paragraphs}
    indexes = {index for index, para in enumerate(doc.paragraphs) if id(para) in editable}
    root = ET.fromstring(doc.xml)
    for index, paragraph in enumerate(root.findall(".//w:p",NS)):
        if index in indexes:
            for node in paragraph.findall(".//w:t",NS):
                node.text = ""
                node.attrib.pop("{http://www.w3.org/XML/1998/namespace}space",None)
    return ET.tostring(root)


@pytest.mark.parametrize("sample",SAMPLES,ids=lambda path:path.name)
def test_selected_issuer_replaces_both_supplied_layouts_without_touching_receivers_or_methods(sample):
    source = sample.read_bytes()
    loaded = _load(source)
    assert len(loaded.issuer_targets) == 1
    old_issuer = loaded.issuer_targets[0]
    old_text = old_issuer.paragraphs[0].text[old_issuer.start:old_issuer.end]
    output = render_assignment(source,{**SAMPLE_FIELDS,**ISSUER})
    result = _load(output)
    selected = result.issuer_targets[0]
    new_text = selected.paragraphs[0].text[selected.start:selected.end]
    assert ISSUER["issuer_name"] in new_text
    assert ISSUER["issuer_position"] in new_text
    assert old_text not in new_text
    assert inspect_assignment_template(output)["fields"] == {**SAMPLE_FIELDS,"issue_date":"29.10.2026"}
    assert structure_except_fields(source,include_issuer=True) == structure_except_fields(output,include_issuer=True)
    with ZipFile(BytesIO(source)) as original, ZipFile(BytesIO(output)) as generated:
        assert original.namelist() == generated.namelist()
        for name in original.namelist():
            if name != "word/document.xml":
                assert original.read(name) == generated.read(name)


def test_not_selecting_issuer_preserves_the_original_issuer_paragraph_verbatim():
    source = SAMPLES[0].read_bytes()
    output = render_assignment(source,SAMPLE_FIELDS)
    original = _load(source)
    generated = _load(output)
    before = original.issuer_targets[0].paragraphs[0]
    after = generated.issuer_targets[0].paragraphs[0]
    assert original.xml[before.tag_start:before.close_start] == generated.xml[after.tag_start:after.close_start]
    assert structure_except_fields(source,include_issuer=False) == structure_except_fields(output,include_issuer=False)


def test_custom_issuer_placeholders_are_optional_and_filled_only_when_person_selected():
    text = "".join('<w:p><w:r><w:t>{{'+key+'}}</w:t></w:r></w:p>' for key in SAMPLE_FIELDS)
    text += '<w:p><w:r><w:t>{{issuer_position}} — {{issuer_name}}</w:t></w:r></w:p>'
    out = BytesIO()
    with ZipFile(out,"w") as archive:
        archive.writestr("word/document.xml",f'<w:document xmlns:w="{NS["w"]}"><w:body>{text}</w:body></w:document>')
    output = render_assignment(out.getvalue(),{**SAMPLE_FIELDS,**ISSUER})
    with ZipFile(BytesIO(output)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    assert any(ISSUER["issuer_name"] in "".join(para.itertext()) and ISSUER["issuer_position"] in "".join(para.itertext())
               for para in root.findall(".//w:p",NS))


def test_selected_issuer_is_rejected_if_a_custom_form_has_no_place_for_the_person():
    text = "".join('<w:p><w:r><w:t>{{'+key+'}}</w:t></w:r></w:p>' for key in SAMPLE_FIELDS)
    out = BytesIO()
    with ZipFile(out,"w") as archive:
        archive.writestr("word/document.xml",f'<w:document xmlns:w="{NS["w"]}"><w:body>{text}</w:body></w:document>')
    with pytest.raises(TemplateError,match="рядка видавця"):
        render_assignment(out.getvalue(),{**SAMPLE_FIELDS,**ISSUER})


def test_invalid_issuer_text_is_rejected():
    with pytest.raises(TemplateError,match="Некоректне"):
        render_assignment(SAMPLES[0].read_bytes(),{**SAMPLE_FIELDS,**ISSUER,"issuer_name":"Тест\x01"})
