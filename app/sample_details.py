"""Physical sample facts, kept separate from batch size and protocol identity."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata


def _space(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))).strip()


def first_seal_number(value: str) -> str:
    """Return only the first physical seal's digits, retaining leading zeroes.

    The numbered bag labels in an act (``№1``, ``№2``) are not part of a seal
    identifier. Never concatenate two IDs into a new, nonexistent identifier.
    """
    text = _space(value)
    numbered = re.search(r"(?:№|#|\bNo\.?)\s*1(?=\s|[:.)-])\s*[:.)-]?\s*(.+)", text, re.I)
    if numbered:
        text = numbered.group(1)
    else:
        # An ordinal with no following physical ID must not become a seal.
        if re.fullmatch(r"(?:№|#|No\.?)\s*[12]\s*[:.)-]?", text, re.I):
            return ""
        second_only = re.match(r"(?:№|#|No\.?)\s*2(?=\s|[:.)-])\s*[:.)-]?\s*(.+)", text, re.I)
        if second_only:
            text = second_only.group(1)
    # Delimiters and the next bag label terminate the first ID. A letter prefix
    # is intentionally discarded because protocol seals must contain digits.
    token = re.search(r"[0-9]+", text)
    return token.group() if token else ""


def _decimal(value: str) -> Decimal | None:
    try:
        result = Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result > 0 else None


def _number(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".").replace(".", ",") if "." in format(value, "f") else format(value, "f")


def _quantity(value: str) -> tuple[int, str] | None:
    text = _space(value).strip(". ")
    match = re.fullmatch(r"([0-9]+)\s*(?:\(?\s*([^()]+?)\s*\)?)?", text)
    if not match or not 0 < int(match[1]) <= 1_000_000:
        return None
    unit = (match[2] or "шт").strip(". ")
    # Only clearly expressed package-count units may supplement sample mass.
    if not re.fullmatch(r"(?:шт|штук|од|одиниць|ж\s*/\s*б|жб|скл\s*/\s*б|пакет(?:ів|и)?|стік-пакет(?:ів|и)?)", unit, re.I):
        return None
    unit = re.sub(r"\s*/\s*", "/", unit)
    unit = {"штук": "шт", "жб": "ж/б"}.get(unit.lower(), unit)
    return int(match[1]), unit


def _mass(value: str, *, whole: bool) -> tuple[Decimal, str] | None:
    # A frequent OCR substitution in this act is numeric "4.725 кі".
    value = re.sub(r"(?<=\d)\s*кі\s*$", " кг", value, flags=re.I)
    pattern = r"([0-9]+(?:[.,][0-9]+)?)\s*(кг|г|kg|g)(?![а-яіїєґa-z])"
    match = re.fullmatch(pattern, value.strip(". "), re.I) if whole else re.search(pattern, value, re.I)
    if not match and not whole:
        # The slash marks this as a package size rather than a product code.
        # In scanned Ukrainian tables a lower-case г may be read as Latin r.
        ratio = re.search(r"(?<!\w)\d+\s*/\s*([0-9]+(?:[.,][0-9]+)?)\s*r\b", value)
        if ratio:
            amount = _decimal(ratio[1])
            return (amount, "г") if amount else None
    if not match:
        return None
    amount = _decimal(match[1])
    if amount is None:
        return None
    unit = "кг" if match[2].lower() in {"кг", "kg"} else "г"
    return amount, unit


def sample_weight_value(weight: str, quantity: str = "", product_name: str = "") -> str:
    """Format known sample mass/count; calculate only from explicit sample count.

    A package mass in a product name alone never establishes the sample mass.
    A batch mass is not accepted as an argument and is never a fallback.
    """
    explicit = _space(weight).strip(". ")
    count = _quantity(quantity)
    total = _mass(explicit, whole=True) if explicit else None
    package = _mass(_space(product_name), whole=False)
    if explicit and total is None:
        # Existing full statements and deliberate manual descriptions are
        # retained; only the requested terminal punctuation is normalized.
        return explicit + "."
    if not explicit and not (count and package):
        return ""
    if not total:
        amount, unit = package
        grams = amount * (1000 if unit == "кг" else 1) * count[0]
        total = grams / 1000, "кг"
    if not count and quantity and total and package:
        # Recover malformed OCR count text only if both independent mass facts
        # agree on an exact integer and the container type remains recognizable.
        container = re.search(r"ж\s*/\s*б|стік[- ]?пакет|скл\s*/\s*б|\bшт\b", quantity, re.I)
        if container:
            total_grams = total[0] * (1000 if total[1] == "кг" else 1)
            pack_grams = package[0] * (1000 if package[1] == "кг" else 1)
            derived = total_grams / pack_grams
            if derived == derived.to_integral_value() and 0 < derived <= 1_000_000:
                unit = re.sub(r"\s*/\s*", "/", container.group().lower())
                count = int(derived), unit
    amount, unit = total
    total_text = _number(amount) + " " + unit
    if not count:
        return total_text + "."
    count_text = f"{count[0]} {count[1]}"
    if package:
        per_pack, pack_unit = package
        calculated = per_pack * (1000 if pack_unit == "кг" else 1) * count[0]
        actual = amount * (1000 if unit == "кг" else 1)
        if calculated == actual:
            return f"{count_text} по {_number(per_pack)} {pack_unit} ({total_text})."
    # If the package mass is absent, the explicit sample count and total are
    # sufficient to calculate its mean mass; require an exact finite gram
    # quantity to avoid turning a contradictory declared pack mass into a fact.
    if not package:
        per_pack_grams = amount * (1000 if unit == "кг" else 1) / count[0]
        try:
            exact_grams = per_pack_grams == per_pack_grams.quantize(Decimal("0.001"))
        except InvalidOperation:
            exact_grams = False
        if exact_grams:
            return f"{count_text} по {_number(per_pack_grams)} г ({total_text})."
    return f"{total_text} ({count_text})."
