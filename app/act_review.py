"""Evidence for sampling-act review and bounded previews of retained originals."""
from io import BytesIO
from pathlib import Path

from .extraction import MAX_PAGES, MAX_PIXELS, parse_act


def add_review(extracted, parsed):
    pages = extracted.get('page_evidence', [])
    locations, summaries, page_items = [], [], []
    for page in pages:
        items = parse_act(page['text'])['items']
        page_items.extend(items)
        boxes = page.get('locations', [])
        for index in range(len(items)):
            location = {'page': page['page'], 'angle': page.get('angle', 0)}
            if len(boxes) == len(items):
                location.update(boxes[index])
            locations.append(location)
        summaries.append({'page':page['page'], 'angle':page.get('angle', 0),
                          'detected':page.get('detected_rows'), 'parsed':len(items)})
    # Do not associate a sample with an arbitrary page when text spanning pages
    # changes the global parser's result. Manual page navigation remains available.
    identity = lambda item: tuple(item.get(key, '') for key in ('product_name', 'catalog_number', 'seal_number', 'manufacture_date'))
    if [identity(item) for item in page_items] == [identity(item) for item in parsed['items']]:
        for item, location in zip(parsed['items'], locations):
            item['source_location'] = location
    complete = bool(summaries) and all(page['detected'] is not None for page in summaries)
    parsed['audit'] = {'detected':sum(page['detected'] for page in summaries) if complete else None,
                       'parsed':len(parsed['items']), 'pages':summaries}
    extracted.pop('page_evidence', None)
    return parsed


def original_image(data, filename, page=1, rotation=0, max_side=2200):
    import fitz
    from PIL import Image, ImageOps
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == '.pdf':
            with fitz.open(stream=data, filetype='pdf') as document:
                count = len(document)
                if document.needs_pass or not 0 < count <= MAX_PAGES or not 1 <= page <= count:
                    raise ValueError('Перевірте номер сторінки та захист PDF.')
                source = document[page - 1]
                scale = min(200 / 72, max_side / max(source.rect.width, source.rect.height))
                pixels = source.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                image = Image.frombytes('RGB', (pixels.width, pixels.height), pixels.samples)
        elif suffix in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'}:
            with Image.open(BytesIO(data)) as source:
                count = getattr(source, 'n_frames', 1)
                if not 0 < count <= MAX_PAGES or not 1 <= page <= count:
                    raise ValueError('Цієї сторінки зображення немає.')
                source.seek(page - 1)
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError('Зображення завелике для перегляду.')
                image = ImageOps.exif_transpose(source).convert('RGBA')
                image.thumbnail((max_side, max_side))
                background = Image.new('RGB', image.size, 'white')
                background.paste(image, mask=image.getchannel('A'))
                image = background
        else:
            raise ValueError('Поруч із полями можна переглядати PDF та зображення. Інший оригінал відкрийте окремо.')
        image = image.rotate(rotation, expand=True)
        return image, count
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Не вдалося відкрити сторінку оригіналу. Перевірте файл.') from exc


def preview_original(data, filename, page=1, rotation=0):
    image, count = original_image(data, filename, page, rotation)
    result = BytesIO()
    image.save(result, 'PNG')
    return result.getvalue(), count


def reocr_fragment(data, filename, page, rotation, bbox, psm=6):
    from PIL import ImageOps
    from .extraction import _ocr_image, _ocr_languages
    image, _ = original_image(data, filename, page, rotation, max_side=3500)
    left, top, right, bottom = bbox
    crop = image.crop((round(left*image.width), round(top*image.height),
                       round(right*image.width), round(bottom*image.height)))
    if min(crop.size) < 12:
        raise ValueError('Виділіть більший фрагмент із текстом: щонайменше 12 пікселів у кожному напрямку.')
    scale = min(2., max(1., 800/crop.width))
    if scale > 1:
        crop = crop.resize((round(crop.width*scale), round(crop.height*scale)))
    crop = ImageOps.expand(crop, border=16, fill='white')
    language, _ = _ocr_languages()
    text = _ocr_image(crop, language, psm=psm)
    if len(text) > 12000:
        raise ValueError('Фрагмент містить забагато тексту для одного поля. Виділіть меншу область.')
    return {'text':text, 'page':page}
