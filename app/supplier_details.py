"""Keep supplier names and their own company codes together."""
import re
import unicodedata

_CODE = re.compile(r"(?:код\s*)?[ЄЕ]ДРПОУ\s*:?\s*([0-9]{8}(?:[0-9]{2})?)(?![0-9])", re.I)


def _clean(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))).strip()


def _name(value):
    return _CODE.sub("", _clean(value)).strip(" ,;.")


def _key(value):
    return re.sub(r"[«»\"“”]", "", _name(value)).casefold()


def supplier_with_code(value: str, suppliers: list[dict]) -> str:
    """Prefer an explicit source code, or a unique exact directory match.

    A manufacturer's code must never be borrowed for a supplier. Ambiguous or
    absent codes remain for manual review rather than being invented.
    """
    value = _clean(value)
    if not value or value == "[НЕ ЗАПОВНЕНО]":
        return value
    codes = set(_CODE.findall(value))
    if len(codes) > 1:
        return value
    code = next(iter(codes)) if codes else ""
    company = _name(value)
    numeric_only = re.fullmatch(r"[0-9]{8}(?:[0-9]{2})?", value)
    code_only = bool(numeric_only or code and not company)
    lookup_code = value if numeric_only else code
    matches = []
    for supplier in suppliers:
        candidate_code = _clean(supplier.get("edrpou"))
        if not candidate_code:
            embedded = set(_CODE.findall(_clean(supplier.get("name"))))
            candidate_code = next(iter(embedded)) if len(embedded) == 1 else ""
        if (_key(supplier.get("name")) == _key(company)
                or code_only and candidate_code == lookup_code):
            matches.append((_name(supplier.get("name")), candidate_code))
    if code_only:
        if len({name for name, _ in matches}) == 1:
            company, code = matches[0][0], lookup_code
        else:
            return value
    elif not code:
        known = {candidate for _, candidate in matches if candidate}
        if len(known) == 1:
            code = next(iter(known))
    return company + ", код ЄДРПОУ: " + code if company and code else value
