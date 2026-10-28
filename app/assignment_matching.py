"""Match task forms by food identity and the complete declared research standard.

An equally named food under another TU/DSTU is a review candidate, never a
confirmed match. Standards in test-method tables are deliberately not consulted.
"""
from __future__ import annotations

import re
import unicodedata

from .matching import _profile, rank_templates

DEPARTMENTS = ("vet", "bacteriology", "chemistry")
_STANDARD = re.compile(
    r"(?<![A-ZА-ЯІЇЄҐ0-9])(?P<kind>ДСТУ|ГОСТ|ГН|ТУ\s*У)\s*(?P<code>\d+(?:[.\-:]\d+)*)", re.I,
)


def _standard_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    # Normalize only known document designators. Identifiers and edition years
    # are retained; we never infer them from a filename or a short numeric code.
    text = re.sub(r"\bDSTU(?=\s*\d)|Д[СC][ТT][УY]", "ДСТУ", text)
    text = re.sub(r"\bGOST(?=\s*\d)", "ГОСТ", text)
    text = re.sub(r"\bGN(?=\s*\d)", "ГН", text)
    text = re.sub(r"(?:ТУ|TY|TУ|ТY)\s*[УUY]", "ТУ У", text)
    text = re.sub(r"(?<=\d)\s*([.\-:])\s*(?=\d)", r"\1", text)
    return text


def standard_identifiers(value: str) -> frozenset[str]:
    text = _standard_text(value)
    result = set()
    for match in _STANDARD.finditer(text):
        kind = re.sub(r"\s+", "", match.group("kind"))
        code = match.group("code").rstrip(".")
        result.add(kind + code)
    if "КАТАЛОГ" in text and re.search(r"(?:№|НОМЕР|N)\s*591\b", text):
        # The provided cod-liver forms cite this order with its full date.
        if re.search(r"15[./]11[./]2019", text):
            result.add("КАТАЛОГМОУ№591ВІД15.11.2019")
    return frozenset(result)


def normalize_standard(value: str) -> str:
    """Return a stable full citation identity, preserving codes and editions."""
    if not str(value or "").strip():
        return ""
    identities = standard_identifiers(value)
    if identities:
        identities = set(identities)
        text = _standard_text(value)
        for amendment in re.finditer(r"(?:ЗМІН[АИ]|AMENDMENT)\s*№?\s*(\d+)", text):
            identities.add("ЗМІНА" + amendment.group(1))
        if not any(key.startswith("КАТАЛОГ") for key in identities):
            remainder = _STANDARD.sub("", text)
            remainder = re.sub(r"\b(?:ТА|І|AND|ВІДПОВІДНО\s+ДО)\b", "", remainder)
            remainder = re.sub(r"[\s.,;:/()\"«»]+", "", remainder)
            if remainder:
                identities.add("ДОДАТКОВО:" + remainder)
        return ";".join(sorted(identities))
    text = _standard_text(value)
    return re.sub(r"\s+", "", text).strip(" .;,\n\r")


def standards_match(left: str, right: str) -> bool:
    a, b = normalize_standard(left), normalize_standard(right)
    return bool(a and b and a == b)


def _scoped_template(product_name: str, template: dict) -> dict | None:
    """Broaden only explicit, user-configured task-form coverage."""
    scope = template.get("product_scope", "")
    if not scope:
        return template
    query = _profile(product_name)
    candidate = _profile(template.get("product_name") or template.get("name") or "")
    adjusted = dict(template)
    if scope == "ready_second_all":
        if query["family"] != "second_course" or query["form"] == "meatplant_canned":
            return None
        adjusted["product_name"] = "Готова до вживання друга страва"
        adjusted["subcategory"] = ""
    elif scope in ("ready_second_cereals_potatoes", "cereals_potatoes"):
        if query["family"] != "second_course" or not query["base"]:
            return None
        if not query["base"] <= {"cereals", "potatoes"} or query["form"] == "meatplant_canned":
            return None
        # A mislabeled bean form must not become a cereal/potato form merely
        # because somebody gave its file a broad name.
        if "legumes" in candidate["base"]:
            return None
        adjusted["product_name"] = "Готова до вживання друга страва"
        adjusted["subcategory"] = ""
    elif scope == "first_course":
        if query["family"] != "first_course" or candidate["family"] != "first_course":
            return None
        adjusted["product_name"] = "Готова до вживання перша страва"
    elif scope == "jam":
        if query["family"] != "jam" or candidate["family"] != "jam":
            return None
        adjusted["product_name"] = "Джем"
    elif scope == "family":
        if not query["family"] or query["family"] != candidate["family"]:
            return None
        if query["family"] == "pate" and query["organ"] != candidate["organ"]:
            return None
        if query["family"] in {"fish", "meat", "liver"}:
            # Species and organ identity remain specific even with broad scope.
            return template
        # Only the dedicated scopes above relax fruit/protein or base details.
        return template
    else:
        return template
    adjusted["aliases"] = []
    return adjusted


def product_candidates(product_name: str, templates: list[dict]) -> list[dict]:
    """Return compatible task forms without asserting normative compliance."""
    if not str(product_name or "").strip():
        return []
    candidates = []
    for template in templates:
        if template.get("department") not in DEPARTMENTS or template.get("enabled") is False:
            continue
        adjusted = _scoped_template(product_name, template)
        if adjusted is None:
            continue
        ranked = rank_templates(product_name, [adjusted])
        if not ranked:
            continue
        entry = ranked[0]
        # A weak text overlap across unrelated unknown categories is never
        # enough to propose a task form merely because the standard is shared.
        if entry["score"] < .20:
            continue
        candidates.append({
            **entry, "product_name": template.get("product_name", ""),
            "department": template["department"], "standard": template.get("standard", ""),
            "product_scope": template.get("product_scope", ""),
        })
    return sorted(candidates, key=lambda e: (-e["score"], str(e["name"]), str(e["id"])))


def rank_assignments(product_name: str, standard: str, templates: list[dict]) -> dict:
    """Separate matching forms from candidates requiring explicit ND review."""
    departments = {department: [] for department in DEPARTMENTS}
    review_departments = {department: [] for department in DEPARTMENTS}
    warnings = []
    if not str(standard or "").strip():
        warnings.append("В акті не визначено нормативний документ. Вкажіть його або перевірте НД обраного завдання окремо.")
    for candidate in product_candidates(product_name, templates):
        exact_standard = standards_match(standard, candidate["standard"])
        entry = {**candidate, "standard_match": exact_standard, "requires_review": not exact_standard}
        if exact_standard:
            entry["reason"] += "; збігається повне позначення НД"
            departments[entry["department"]].append(entry)
        else:
            entry["score"] = min(entry["score"], .49)
            entry["reason"] = "Назва продукту сумісна, але НД відрізняється або відсутній — потрібна окрема перевірка"
            review_departments[entry["department"]].append(entry)
    for department in DEPARTMENTS:
        departments[department].sort(key=lambda e: (-e["score"], str(e["name"]), str(e["id"])))
        review_departments[department].sort(key=lambda e: (-e["score"], str(e["name"]), str(e["id"])))
        if len(departments[department]) > 1 and departments[department][0]["score"] == departments[department][1]["score"]:
            label = {"vet": "ветеринарно-санітарного", "bacteriology": "бактеріологічного", "chemistry": "хіміко-токсикологічного"}[department]
            warnings.append(f"Для {label} відділення є кілька рівноцінних шаблонів. Оберіть варіант вручну.")
    return {"departments": departments, "review_departments": review_departments, "warnings": warnings}
