"""Regressions for sideways acts and a clipped bottom table border; no user scan is stored."""
import pytest
from PIL import Image, ImageDraw

from app import extraction


@pytest.mark.parametrize('turn', [0, 90, 180, 270])
def test_orientation_reads_labels_and_preserves_all_right_angle_directions(monkeypatch, turn):
    source = Image.new('RGB', (400, 600), 'white')
    source.putpixel((0, 0), (255, 0, 0))
    source.putpixel((399, 0), (0, 255, 0))
    source.putpixel((399, 599), (0, 0, 255))
    source.putpixel((0, 599), (255, 255, 0))
    calls = []
    def recognize(image, language, psm=3):
        calls.append(psm)
        return 'АКТ відбору зразків. Назва продукту. Виробник. Дата виготовлення.' if image.getpixel((0, 0)) == (255, 0, 0) else 'xz gf uz'
    monkeypatch.setattr(extraction, '_ocr_image', recognize)
    oriented, angle = extraction._orient_image(source.rotate(turn, expand=True), 'ukr')
    assert angle == (360-turn) % 360
    assert oriented.size == source.size and oriented.getpixel((0, 0)) == (255, 0, 0)
    assert len(calls) == (1 if turn == 0 else 4)


def test_uncertain_page_keeps_its_original_orientation(monkeypatch):
    source = Image.new('RGB', (400, 600), 'white')
    monkeypatch.setattr(extraction, '_ocr_image', lambda *a, **k: '123 456 unclear text')
    result, angle = extraction._orient_image(source, 'ukr')
    assert angle == 0 and result.size == source.size


@pytest.mark.parametrize('ambiguous_seals', [False, True])
def test_table_retains_third_sample_when_warp_clips_outer_border(monkeypatch, ambiguous_seals):
    cv2 = pytest.importorskip('cv2')
    image = Image.new('RGB', (1050, 800), 'white')
    draw = ImageDraw.Draw(image)
    xs, ys = [40, 80, 430, 620, 770, 900, 1010], [100, 230, 370, 510, 650]
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill='black', width=3)
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill='black', width=3)
    original = cv2.morphologyEx
    def clipped(source, operation, kernel, *args, **kwargs):
        result = original(source, operation, kernel, *args, **kwargs)
        if kernel.shape[0] == 1 and kernel.shape[1] >= source.shape[1] // 6:
            result[-6:, :] = 0
        return result
    monkeypatch.setattr(cv2, 'morphologyEx', clipped)
    seal = ['unclear', '00000001', '10000000'] if ambiguous_seals else ['00000001']
    values = iter([
        '№', 'Назва харчового продукту', 'Нідприємство-виродник, країна, код ЕДРНОУ', 'Дата ви- готовлення', 'Маса партії', '№ пломби',
        'Сніданок туриста (яловичина) (12011)', 'ТОВ Приклад', '15.09.2026', '1512.525', *seal,
        'Сніданок туриста (яловичина) (12011)', 'ТОВ Приклад', '18.09.2026', '1942.500', *seal,
        'Яловичина тушкована (12013)', 'ТОВ Приклад', '14.09.2028', '7505.400', *seal,
    ])
    monkeypatch.setattr(extraction, '_ocr_image', lambda *a, **k: next(values))
    result = extraction._ocr_table(image, 'ukr')
    assert result['rows'] == 3
    items = extraction.parse_act(result['text'])['items']
    assert len(items) == 3
    assert items[0]['product_name'] == items[1]['product_name']
    assert [item['catalog_number'] for item in items] == ['12011', '12011', '12013']
    assert [item['manufacture_date'] for item in items] == ['15.09.2026', '18.09.2026', '14.09.2028']
    assert all(item['manufacturer'] == 'ТОВ Приклад' for item in items)
    assert all(item['seal_number'] == ('' if ambiguous_seals else '00000001') for item in items)
    if ambiguous_seals:
        assert any('неоднозначно' in warning for warning in result['warnings'])
