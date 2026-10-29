"""Format protocol text while retaining the facts read from a sampling act."""
from __future__ import annotations

import re
import unicodedata


SAMPLING_ENDING = (
    "та направлені на проведення лабораторних випробувань до Лабораторії "
    "безпечності харчових продуктів військової частини А0873."
)
_UNIT = r"[АA]\s*\d{4}"
_RANKS = {
    "старший майстер-сержант": "старшим майстер-сержантом",
    "головний майстер-сержант": "головним майстер-сержантом",
    "генерал-полковник": "генерал-полковником",
    "генерал-лейтенант": "генерал-лейтенантом",
    "генерал-майор": "генерал-майором",
    "бригадний генерал": "бригадним генералом",
    "молодший лейтенант": "молодшим лейтенантом",
    "старший лейтенант": "старшим лейтенантом",
    "головний сержант": "головним сержантом",
    "старший сержант": "старшим сержантом",
    "старший солдат": "старшим солдатом",
    "старший матрос": "старшим матросом",
    "майстер-сержант": "майстер-сержантом",
    "штаб-сержант": "штаб-сержантом",
    "підполковник": "підполковником",
    "полковник": "полковником",
    "капітан": "капітаном",
    "лейтенант": "лейтенантом",
    "майор": "майором",
    "сержант": "сержантом",
    "солдат": "солдатом",
    "матрос": "матросом",
}
_RANK_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(rank) for rank in sorted(
        [*_RANKS, *_RANKS.values()], key=len, reverse=True)) + r")\b", re.I,
)
_NAME = r"[А-ЯІЇЄҐ][а-яіїєґ’'ʼ-]+"
_SURNAME = r"[А-ЯІЇЄҐ][А-ЯІЇЄҐа-яіїєґ’'ʼ-]+"
_PERSON_AFTER_RANK = re.compile(
    r"^(?P<given>" + _NAME + r"(?:\s+" + _NAME + r")?)\s+(?P<surname>" + _SURNAME + r")(?=$|[\s,;.])"
)
_OLD_ENDING = re.compile(
    r"\bта\s+направлені\s+на\s+проведення\s+лабораторних\s+випробувань\s+до\s+"
    r"Лабораторії\s+безпечності\s+харчових\s+продуктів\s+військової\s+частини\s+"
    + _UNIT + r"\s*[.!;,:]*", re.I,
)


def _space(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))).strip()


def _format_ranks_and_names(value: str) -> str:
    """Inflect known ranks; do not guess the case or gender of surnames."""
    matches = list(_RANK_PATTERN.finditer(value))
    if not matches:
        return value
    chunks = []
    previous = 0
    for index, match in enumerate(matches):
        chunks.append(value[previous:match.start()])
        rank = _space(match.group()).lower()
        chunks.append(_RANKS.get(rank, rank))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        following = value[match.end():end]
        leading = re.match(r"\s*", following).group()
        person = _PERSON_AFTER_RANK.match(following[len(leading):])
        if person:
            initials = " ".join(name[0] + "." for name in person.group("given").split())
            following = leading + initials + " " + person.group("surname") + following[len(leading) + person.end():]
        chunks.append(following)
        previous = end
    return "".join(chunks) + value[previous:]


def _people(body: str) -> str:
    """Prefer the rank/name parts over repeated positions and unit titles."""
    rows = [part.strip(" ,;.") for part in re.split(r"[;\n]+", body) if part.strip(" ,;. ")]
    people = []
    for row in rows:
        ranks = list(_RANK_PATTERN.finditer(row))
        if ranks:
            row = row[ranks[0].start():]
        else:
            row = re.sub(r"^ветеринарний\s+лікар\b", "ветеринарним лікарем", row, flags=re.I)
        if row:
            people.append(_format_ranks_and_names(_space(row)))
    return " та ".join(people)


def normalize_sampling_description(text: str) -> str:
    """Use instrumental representatives and exactly one fixed final sentence.

    Empty input stays empty so the required-field check can still identify it.
    Other manual facts are retained; only the established rank/label grammar is
    changed and the mandated laboratory ending is standardized.
    """
    value = _space(text)
    if not value:
        return ""
    value = _OLD_ENDING.sub("", value).strip(" ,;.")

    def legacy_representatives(match):
        body = match.group("body")
        unit = re.search(r"військової\s+частини\s+(" + _UNIT + r")", body, re.I)
        prefix = "представниками"
        if unit:
            prefix += " військової частини " + re.sub(r"\s+", "", unit.group(1)).replace("A", "А")
        return prefix + " " + _people(body)

    value = re.sub(
        r"\bПредставники\s*:\s*(?P<body>.+?)(?=\s*,?\s*[ув]\s+присутності|$)",
        legacy_representatives, value, flags=re.I,
    )
    value = re.sub(r"\bпредставники(?=\s+(?:військової\s+частини|[А-ЯІЇЄҐ]))", "представниками", value, flags=re.I)
    value = re.sub(r"\b([ув])\s+присутності\s+представники\b", "в присутності представників", value, flags=re.I)
    value = _format_ranks_and_names(value)
    value = re.sub(r"\s+([,;])", r"\1", value)
    value = re.sub(r",\s*,+", ",", value).strip(" ,;.")
    return (value + ", " if value else "") + SAMPLING_ENDING


def sampling_description_from_act(address: str, preamble: str) -> str:
    """Combine the act's address, military representatives and company header."""
    value = _space(address).strip(" ,;.")
    representatives = re.search(
        r"Представники\s+військової\s+частини\s+(?P<unit>" + _UNIT + r")\s*:\s*"
        r"(?P<body>.+?)(?=\s+[УВ]\s+присутності|\s+Зразки\s+харчових|\s+Час\s+відбору|$)",
        preamble, re.I | re.S,
    )
    if representatives and not re.search(r"\bпредставник(?:ами|и)\b", value, re.I):
        unit = re.sub(r"\s+", "", representatives.group("unit")).replace("A", "А")
        people = _people(representatives.group("body"))
        if people:
            value += (", " if value else "") + "представниками військової частини " + unit + " " + people
    if not re.search(r"\bприсутності\b", value, re.I):
        presence = re.search(r"\b[УВ]\s+присутності\s+([^:\n]+)", preamble, re.I)
        if presence:
            header = _space(presence.group(1)).strip(" ,;.")
            company = re.search(
                r"\b(?:ТОВ|ТзОВ|ТЗОВ|ПП|ДП|ФОП)\s*[«\"]?[^»\"\n:]+[»\"]?", header,
            )
            if company:
                presence_text = "представників " + company.group().strip(" ,;.")
            else:
                presence_text = header
            if presence_text:
                value += (", " if value else "") + "в присутності " + presence_text
    return normalize_sampling_description(value)


def clean_product_name(name: str, catalog_number: str) -> str:
    """Keep the product identity without catalog duplication or pack weight.

    Physical package mass belongs in the sample-weight field. Product names
    inside quotation marks, grades, years and normative references are retained.
    """
    value = _space(name)
    number = re.fullmatch(r"\s*(?:№\s*)?(\d+)\s*[.;]?\s*", str(catalog_number or ""))
    if number:
        digits = re.escape(number.group(1))
        label = r"(?:№\s*(?:за\s+каталогом\s*)?|(?:номер|код|№)\s+за\s+каталогом\s*[:№]?\s*|каталожний\s+номер\s*[:№]?\s*)"
        suffix = re.compile(
            r"(?:\s*[\[(]\s*(?:" + label + r")?" + digits + r"\s*[\])]|\s*[,;]?\s+" + label + digits + r")\s*[.;]?\s*$",
            re.I,
        )
        value = suffix.sub("", value).rstrip(" ,;")
    mass = (r"(?<![\w/])(?:маса\s+(?:нетто\s+)?|нетто\s+)?"
            r"(?:\d+\s*[/xх×]\s*)?\d+(?:[.,]\d+)?\s*(?:кг|kg|г|g)(?![\w])"
            r"(?:\s*нетто)?\s*\.?")
    outside_quotes = re.split(r'(«[^»]*»|“[^”]*”|"[^\"]*")', value)
    for index in range(0, len(outside_quotes), 2):
        part = outside_quotes[index]
        part = re.sub(r"(?<!\w)\d+\s*/\s*\d+(?:[.,]\d+)?\s*r\b\s*\.?", "", part, flags=re.I)
        part = re.sub(r"[\[(]\s*" + mass + r"\s*[\])]", "", part, flags=re.I)
        outside_quotes[index] = re.sub(mass, "", part, flags=re.I)
    return _space("".join(outside_quotes)).strip(" ,;")
