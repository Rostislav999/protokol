"""The built-in summary form must retain the supplied layout without source data."""
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.protocol_excel import DEFAULT_TEMPLATE, FIELDS, NS, Record, cell_text, write_excel_bytes, xml


def strings(archive):
    return [''.join(item.itertext()) for item in xml(archive.read('xl/sharedStrings.xml'))]


def test_built_in_form_retains_ubhp_headers_widths_table_style_and_formats():
    with ZipFile(DEFAULT_TEMPLATE) as archive:
        sheet = xml(archive.read('xl/worksheets/sheet1.xml'))
        shared = strings(archive)
        for column, label in FIELDS:
            assert ' '.join(cell_text(sheet.find(f".//s:c[@r='{column}1']", NS), shared).split()) == label
        assert cell_text(sheet.find(".//s:c[@r='O1']", NS), shared) == 'Результат випробувань'
        assert cell_text(sheet.find(".//s:c[@r='P1']", NS), shared) == 'Відповідність описам Каталогу'
        widths = {int(col.get('min')): col for col in sheet.find('s:cols', NS)}
        assert [widths[i].get('hidden') for i in (1, 2, 3)] == ['1', '1', '1']
        assert widths[7].get('width') == '51.5703125'
        assert widths[8].get('width') == '60.42578125'
        table = xml(archive.read('xl/tables/table1.xml'))
        assert table.get('ref') == 'A1:P88'
        assert table.find('s:tableStyleInfo', NS).get('name') == 'TableStyleMedium2'
        assert table.find('s:tableStyleInfo', NS).get('showRowStripes') == '1'
        styles = xml(archive.read('xl/styles.xml'))
        assert styles.find('s:fonts/s:font/s:name', NS).get('val') == 'Aptos Narrow'
        assert styles.find('s:numFmts/s:numFmt', NS).get('formatCode') == '0.000'
        assert sheet.find('s:pageSetup', NS).get('paperSize') == '9'


def test_built_in_form_contains_only_headers_and_validation_choices():
    with ZipFile(DEFAULT_TEMPLATE) as archive:
        shared = strings(archive)
        sheet = xml(archive.read('xl/worksheets/sheet1.xml'))
        for row in sheet.findall('s:sheetData/s:row', NS):
            if row.get('r') != '1':
                assert not any(cell_text(cell, shared) or cell.find('s:f', NS) is not None for cell in row)
        hidden = xml(archive.read('xl/worksheets/sheet2.xml'))
        assert hidden.find(".//s:c[@r='A1']", NS) is None
        assert {cell_text(cell, shared) for cell in hidden.findall('.//s:c', NS)} == {'Відповідає', 'Не відповідає'}
        for name in archive.namelist():
            assert not name.startswith(('xl/printerSettings/', 'xl/externalLinks/', 'customXml/'))
            content = archive.read(name)
            assert b'dot_protocol:' not in content
            assert b'C:\\Users\\' not in content
            if name.endswith('.rels'):
                assert not any(rel.get('TargetMode') == 'External' for rel in xml(content))


@pytest.mark.parametrize('count', [1, 100])
def test_default_export_keeps_layout_and_expands_the_form_without_filling_service_or_compliance(count):
    original = DEFAULT_TEMPLATE.read_bytes()
    records = [Record('Test', dict(D=f'87-012-2972-{2000+i}-2', E='04.11.2026', G='Свинина тушкована',
                                  I='01.10.2026', J='01.10.2028', K='1234.5', L='0.525', M='00001234', N='01234567'))
               for i in range(count)]
    output, report = write_excel_bytes(None, records)
    assert len(report['added']) == count and not report['updated']
    assert DEFAULT_TEMPLATE.read_bytes() == original
    with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(output)) as after:
        for name in before.namelist():
            if name not in ('xl/worksheets/sheet1.xml', 'xl/tables/table1.xml'):
                assert after.read(name) == before.read(name), name
        sheet = xml(after.read('xl/worksheets/sheet1.xml'))
        prototype = xml(before.read('xl/worksheets/sheet1.xml'))
        for tag in ('cols', 'sheetProtection', 'protectedRanges', 'dataValidations', 'pageMargins', 'pageSetup', 'headerFooter', 'extLst'):
            assert xml_bytes(sheet.find('s:' + tag, NS)) == xml_bytes(prototype.find('s:' + tag, NS)), tag
        for i, record in enumerate(records, 2):
            row = sheet.find(f"s:sheetData/s:row[@r='{i}']", NS)
            assert cell_text(row.find(f"s:c[@r='D{i}']", NS), []) == record.values['D']
            for col in ('E', 'I', 'J', 'K', 'L'):
                assert row.find(f"s:c[@r='{col}{i}']", NS).get('t') == 'n'
            assert cell_text(row.find(f"s:c[@r='M{i}']", NS), []) == '00001234'
            for col in ('A', 'B', 'C', 'O', 'P'):
                cell = row.find(f"s:c[@r='{col}{i}']", NS)
                assert cell is None or not (cell_text(cell, []) or cell.find('s:f', NS) is not None)
        assert xml(after.read('xl/tables/table1.xml')).get('ref') == f'A1:P{max(88, count+1)}'


def xml_bytes(node):
    from lxml import etree
    return etree.tostring(deepcopy(node), method='c14n')
