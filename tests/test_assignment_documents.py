from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from app.assignments_documents import (
    FIELD_LABELS,
    _load,
    inspect_assignment_template,
    render_assignment,
)
from app.documents import MISSING_VALUE, TemplateError

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = sorted((ROOT / "assets/assignments").rglob("*.docx"))
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
FILL = {"sample_number": "999999", "product_name": "НОВИЙ ЗРАЗОК & <Назва>",
        "sample_weight": "9,9 кг (99 шт.)", "standard": "ДСТУ 9999:2026",
        "issue_date": "2026-10-28"}


def xml(data):
    with ZipFile(BytesIO(data)) as archive:
        return archive.read("word/document.xml")


def docx(body):
    out = BytesIO()
    with ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{NS["w"]}"><w:body>{body}</w:body></w:document>')
        archive.writestr("word/styles.xml", b"unchanged styles")
    return out.getvalue()


def p(value):
    return "<w:p><w:r><w:t>" + value + "</w:t></w:r></w:p>"


def placeholders(extra=""):
    return docx("".join(p("{{" + key + "}}") for key in FIELD_LABELS) + extra)


def static_structure(data):
    """Compare every original style, run, method and signature outside fields."""
    loaded = _load(data)
    editable = {id(p) for target in loaded.targets for p in target.paragraphs}
    indexes = {i for i, para in enumerate(loaded.paragraphs) if id(para) in editable}
    root = ET.fromstring(xml(data))
    for i, paragraph in enumerate(root.findall(".//w:p", NS)):
        if i in indexes:
            for node in paragraph.findall(".//w:t", NS):
                node.text = ""
                node.attrib.pop("{http://www.w3.org/XML/1998/namespace}space", None)
    return ET.tostring(root)


def test_all_sixty_supplied_assignment_forms_are_bundled():
    assert len(SAMPLES) == 60


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda path: str(path.relative_to(ROOT / "assets/assignments")))
def test_every_original_fills_both_form_layouts_without_changing_methods_or_styles(sample):
    source = sample.read_bytes()
    info = inspect_assignment_template(source)
    assert info["department"] in {"vet", "bacteriology", "chemistry"}
    assert set(info["available_fields"]) == set(FIELD_LABELS)
    assert info["product_name"] and info["standard"]
    output = render_assignment(source, FILL)
    fields = inspect_assignment_template(output)["fields"]
    assert fields == {**FILL, "issue_date": "28.10.2026"}
    assert static_structure(source) == static_structure(output)
    with ZipFile(BytesIO(source)) as old, ZipFile(BytesIO(output)) as new:
        assert old.namelist() == new.namelist()
        for name in old.namelist():
            if name == 'word/footer1.xml':
                before, after = ET.fromstring(old.read(name)), ET.fromstring(new.read(name))
                assert ''.join(before.itertext()) == ''.join(after.itertext())
                assert [f.get('{'+NS['w']+'}instr') for f in after.findall('.//w:fldSimple', NS)] == ['PAGE', 'NUMPAGES']
            elif name == 'word/settings.xml':
                before, after = ET.fromstring(old.read(name)), ET.fromstring(new.read(name))
                assert after.find('w:updateFields', NS).get('{'+NS['w']+'}val') == 'true'
                for root in (before, after):
                    update = root.find('w:updateFields', NS)
                    if update is not None:
                        root.remove(update)
                assert ET.tostring(before) == ET.tostring(after)
            elif name != "word/document.xml":
                assert old.read(name) == new.read(name)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda path: str(path.relative_to(ROOT / "assets/assignments")))
def test_missing_act_weight_does_not_retain_historical_sample_amount(sample):
    source = sample.read_bytes()
    info = inspect_assignment_template(source)
    output = render_assignment(source, {"sample_number": "12345", "product_name": "Новий продукт", "issue_date": "28.10.2026"})
    fields = inspect_assignment_template(output)["fields"]
    assert fields["sample_weight"] == MISSING_VALUE
    assert fields["standard"] == info["standard"]
    assert fields["sample_number"] == "12345"
    assert fields["issue_date"] == "28.10.2026"


def test_multiple_placeholders_in_one_paragraph_and_split_runs_are_filled():
    body = p("Бактеріологічне відділення")
    body += p("{{sample_number}} — {{product_name}}")
    body += '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>{{issue_</w:t></w:r><w:r><w:t>date}}</w:t></w:r></w:p>'
    body += p("{{standard}}") + p("{{sample_weight}}") + p("Незмінний метод {{not_a_field}}")
    with pytest.raises(TemplateError, match="Невідоме поле"):
        inspect_assignment_template(docx(body))
    source = docx(body.replace("{{not_a_field}}", "ГОСТ 30425-97"))
    output = render_assignment(source, FILL)
    assert "999999 — НОВИЙ ЗРАЗОК &amp; &lt;Назва&gt;".encode() in xml(output)
    assert b"<w:rPr><w:b/></w:rPr>" in xml(output)
    assert "Незмінний метод ГОСТ 30425-97".encode() in xml(output)


def test_empty_table_values_accept_text_without_rebuilding_rows():
    rows = []
    for key, label in FIELD_LABELS.items():
        blank = "<w:p/>" if key == "product_name" else "<w:p><w:r><w:t/></w:r></w:p>"
        rows.append("<w:tr><w:tc>" + p(label) + "</w:tc><w:tc>" + blank + "</w:tc></w:tr>")
    source = docx("<w:tbl>" + "".join(rows) + "</w:tbl>")
    output = render_assignment(source, FILL)
    assert inspect_assignment_template(output)["fields"] == {**FILL, "issue_date": "28.10.2026"}
    assert xml(source).count(b"<w:tr>") == xml(output).count(b"<w:tr>")


@pytest.mark.parametrize("bad", ["2026-02-30", "31.02.2026", "yesterday"])
def test_invalid_issue_dates_are_rejected(bad):
    with pytest.raises(TemplateError, match="дата"):
        render_assignment(placeholders(), {**FILL, "issue_date": bad})


def test_invalid_text_and_empty_sample_number_are_rejected():
    with pytest.raises(TemplateError, match="Некоректне"):
        render_assignment(placeholders(), {**FILL, "product_name": "bad\x01text"})
    with pytest.raises(TemplateError, match="номер"):
        render_assignment(placeholders(), {**FILL, "sample_number": "№ "})


def test_absent_required_fields_and_malformed_docx_fail_actionably():
    with pytest.raises(TemplateError, match="відсутні"):
        inspect_assignment_template(docx(p("{{sample_number}}")))
    with pytest.raises(TemplateError, match="справжній"):
        inspect_assignment_template(b"not a docx")
    source = docx(p("{{sample_number}}") + p("{{product_name}}") + p("{{standard}}") + p("{{issue_date}}"))
    with ZipFile(BytesIO(source)) as archive:
        malicious = archive.read("word/document.xml").replace(b"<w:document", b'<!DOCTYPE x [<!ENTITY ext SYSTEM "file:///etc/passwd">]><w:document')
    bad = BytesIO()
    with ZipFile(bad, "w") as archive:
        archive.writestr("word/document.xml", malicious)
    with pytest.raises(TemplateError, match="DTD"):
        inspect_assignment_template(bad.getvalue())
