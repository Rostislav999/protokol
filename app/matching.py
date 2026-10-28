"""Conservative, offline matching of food names to an editable template library.

Scores are suggestions, not an authorization to generate documents.  In particular,
two equally good variants intentionally remain separate candidates for review.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).lower()
    value = re.sub(r"\.docx$", "", value)
    value = value.replace("ё", "е").replace("’", "'").replace("`", "'")
    return " ".join(re.findall(r"[a-zа-яіїєґ0-9]+", value))


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(r"\b(?:" + pattern + r")", text) for pattern in patterns)


def _profile(value: str) -> dict[str, Any]:
    text = normalize(value)
    family = None
    # Dish types precede ingredients: soup containing pork is still a soup.
    if _has(text, "суп", "борщ", "перша страва", "перш[а-яіїєґ]* страв", "перв[а-я]* блюд"):
        family = "first_course"
    elif _has(text, "снідан", "завтрак"):
        family = "breakfast"
    elif _has(text, "паштет"):
        family = "pate"
    elif _has(text, "печінк", "печен[ьи]", "печеноч"):
        family = "liver"
    elif _has(text, "сухар", "крекер"):
        family = "cracker"
    elif _has(text, "джем", "повидл", "варенн", "варень"):
        family = "jam"
    elif _has(text, "цукор", "цукр", "сахар"):
        family = "sugar"
    elif _has(text, "сіль", "сол[ьи]"):
        family = "salt"
    elif _has(text, "каша", "каші", "квасол", "фасол", "горох", "горохов", "сочевиц", "чечевиц", "нут", "бобов", "бобові", "картопл", "картоф", "рис", "греч", "ячн", "перлов", "булгур", "кускус", "круп", "м ясорослин", "мясорастител", "друга страва", "друг[а-яіїєґ]* страв", "втор[а-я]* блюд"):
        family = "second_course"
    elif _has(text, "кільк", "кильк", "шпрот", "салак", "тун[еє]ц", "тунець", "сардин", "скумбр", "рибн", "рыбн"):
        family = "fish"
    elif _has(text, "свин", "ялович", "говяд", "курк", "куриц", "тушкован", "тушен", "м яс", "мяс"):
        family = "meat"

    species = set()
    for name, stems in {
        "pork": ("свин",),
        "beef": ("ялович", "говяд"),
        "chicken": ("курк", "куриц", "куряч"),
        "sprat": ("кільк", "кильк"),
        "smoked_sprats": ("шпрот",),
        "herring": ("салак",),
        "tuna": ("тун[еє]ц", "тунець"),
        "cod": ("тріск", "треск"),
        "sardine": ("сардин",),
        "mackerel": ("скумбр",),
    }.items():
        if _has(text, *stems):
            species.add(name)

    grain = set()
    for name, stems in {
        "wheat": ("пшен",),
        "rye": ("житн", "ржан"),
        "buckwheat": ("греч",),
        "barley": ("ячн", "ячнев", "ячмен"),
        "rice": ("рис",),
        "beans": ("квасол", "фасол"),
    }.items():
        if _has(text, *stems):
            grain.add(name)

    fruit = set()
    for name, stems in {
        "apricot": ("абрикос",),
        "apple": ("яблу", "яблок"),
        "strawberry": ("полуниц", "клубник"),
        "plum": ("слив",),
        "cherry": ("вишн",),
    }.items():
        if _has(text, *stems):
            fruit.add(name)
    # Liver pâté is a different product from meat pâté. Pork fat in a liver pâté
    # must not make a generic pork/meat pâté an interchangeable template.
    organ = "liver" if _has(text, "печінк", "печен[ьи]", "печеноч") else None
    base = set()
    if _has(text, "квасол", "фасол", "горох", "горош", "сочевиц", "чечевиц", "нут", "бобов", "бобові"):
        base.add("legumes")
    if _has(text, "картопл", "картоф"):
        base.add("potatoes")
    if _has(text, "греч", "рис", "ячн", "ячнев", "ячмен", "перлов", "пшен", "житн", "ржан", "вівс", "овсян", "пшон", "кукурудз", "кукуруз", "булгур", "кускус", "манн", "круп"):
        base.add("cereals")
    # Pea porridge is a legume dish, despite the generic word "каша".
    if not base and _has(text, "каша", "каші"):
        base.add("cereals")
    legumes = set()
    for ingredient, stems in {
        "beans": ("квасол", "фасол"),
        "peas": ("горох", "горош"),
        "lentils": ("сочевиц", "чечевиц"),
        "chickpeas": ("нут",),
    }.items():
        if _has(text, *stems):
            legumes.add(ingredient)
    form = None
    if family == "second_course":
        if _has(text, "готов[а-яіїєґ]* (?:до вживання )?друг[а-яіїєґ]* страв", "готов[а-я]* (?:к употреблению )?втор[а-я]* блюд"):
            form = "ready_second"
        elif _has(text, "м ясорослин", "мясорастител", "мясо растител"):
            form = "meatplant_canned"
    return {"family": family, "species": species, "grain": grain, "fruit": fruit, "organ": organ, "base": base, "legumes": legumes, "form": form}


def infer_subcategory(product_name: str) -> str:
    """Return an unambiguous base subgroup, without inventing an unknown one."""
    profile = _profile(product_name)
    if profile["family"] != "second_course" or len(profile["base"]) != 1:
        return ""
    return next(iter(profile["base"]))


_FAMILY_LABELS = {
    "first_course": "перша страва",
    "second_course": "друга страва / каша",
    "breakfast": "м’ясний сніданок",
    "pate": "паштет",
    "liver": "печінка",
    "cracker": "сухарики",
    "jam": "джем",
    "sugar": "цукор",
    "salt": "сіль",
    "fish": "рибні консерви",
    "meat": "м’ясні консерви",
}


def _terms(text: str) -> set[str]:
    # Stable short stems allow Ukrainian inflections without a large NLP model.
    stopwords = {"з", "зі", "із", "та", "і", "в", "у", "с", "со", "и", "консерви", "консервы", "стерилізовані", "стерилизованные"}
    return {word[:5] for word in normalize(text).split() if not word.isdigit() and word not in stopwords and len(word) > 2}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[\n;]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part) for part in value if part]
    return []


def rank_templates(product_name: str, templates: list[dict]) -> list[dict]:
    """Rank matching templates, leaving incompatible products out entirely.

    Template fields: ``id``, ``name``, optional ``product_name``, ``aliases`` and
    ``exclusions``, and ``subcategory`` (legumes/cereals/potatoes/other).
    Explicit aliases can identify custom products. Exclusions
    always win.  Callers must still show the selected template for confirmation.
    """
    query = normalize(product_name)
    if not query:
        return []
    query_profile = _profile(query)
    ranked: list[dict] = []
    for template in templates:
        if template.get("enabled") is False:
            continue
        exclusions = [normalize(term) for term in _as_list(template.get("exclusions"))]
        if any(term and re.search(r"\b" + re.escape(term) + r"\b", query) for term in exclusions):
            continue
        canonical = str(template.get("product_name") or template.get("name") or "")
        aliases = _as_list(template.get("aliases"))
        candidate_profile = _profile(canonical)
        if candidate_profile["family"] is None:
            known = [_profile(alias) for alias in aliases if _profile(alias)["family"]]
            if known:
                candidate_profile = known[0]
        subgroup = template.get("subcategory")
        if subgroup in {"legumes", "cereals", "potatoes", "other"}:
            canonical_base = candidate_profile["base"]
            if canonical_base and subgroup not in canonical_base:
                # A mislabeled setting or broad alias cannot erase an explicit
                # ingredient in the actual Word template's canonical name.
                continue
            if not canonical_base:
                candidate_profile["base"] = {subgroup}
        if query_profile["family"] and candidate_profile["family"] and query_profile["family"] != candidate_profile["family"]:
            continue
        if query_profile["family"] == candidate_profile["family"] == "pate" and query_profile["organ"] != candidate_profile["organ"]:
            continue
        if query_profile["family"] == candidate_profile["family"] == "second_course":
            query_form, candidate_form = query_profile["form"], candidate_profile["form"]
            if query_form and candidate_form and query_form != candidate_form:
                continue
            if query_profile["base"] and candidate_profile["base"] and not query_profile["base"].intersection(candidate_profile["base"]):
                continue
        conflicting = False
        specific_matches = 0
        for attribute in ("species", "grain", "fruit"):
            left, right = query_profile[attribute], candidate_profile[attribute]
            if left and right and not left.intersection(right):
                conflicting = True
                break
            if left and right:
                specific_matches += 1
        if conflicting:
            continue

        options = [canonical, *aliases]
        # Filename is useful only as a secondary spelling hint, not a category.
        if template.get("name"):
            options.append(str(template["name"]))
        score = 0.0
        exact = False
        for option in options:
            candidate = normalize(option)
            if not candidate:
                continue
            if candidate == query:
                score, exact = 0.99, True
                break
            q_terms, c_terms = _terms(query), _terms(candidate)
            overlap = len(q_terms & c_terms) / max(len(q_terms | c_terms), 1)
            similarity = SequenceMatcher(None, query, candidate).ratio()
            score = max(score, 0.55 * overlap + 0.30 * similarity)

        family_match = bool(query_profile["family"] and query_profile["family"] == candidate_profile["family"])
        if not exact:
            if family_match:
                score = max(score, 0.62 + min(0.24, 0.12 * specific_matches))
                # Ingredient-only names can be ambiguous even within a family.
                if specific_matches:
                    score = max(score, min(0.93, score + 0.06))
            else:
                score = min(score, 0.49)
        missing_species = (
            family_match
            and query_profile["family"] in {"meat", "fish", "liver"}
            and bool(query_profile["species"]) != bool(candidate_profile["species"])
        )
        if missing_species:
            # A generic "stewed meat" row does not establish pork vs beef.
            # Even an overly broad configured alias cannot supply that fact.
            score = min(score, 0.49)
        missing_form = (
            family_match
            and query_profile["family"] == "second_course"
            and bool(query_profile["form"]) != bool(candidate_profile["form"])
        )
        if missing_form:
            # "Buckwheat with beef" alone cannot distinguish ready second
            # course from meat-and-plant canned food, even via an exact alias.
            score = min(score, 0.49)
        uncertain_legume = (
            family_match
            and query_profile["family"] == "second_course"
            and "legumes" in query_profile["base"]
            and "legumes" in candidate_profile["base"]
            and bool(query_profile["legumes"] or candidate_profile["legumes"])
            and not query_profile["legumes"].intersection(candidate_profile["legumes"])
        )
        if uncertain_legume:
            # Same standards subgroup may still be useful as a manual hint,
            # but peas/lentils/chickpeas do not establish the name "beans".
            score = min(score, 0.49)
        # Do not flood the review list with unrelated library entries.
        if score < 0.20 and not family_match:
            continue
        if uncertain_legume:
            reason = "Збіг групи «бобові»; конкретний вид бобових відрізняється або не визначений — оберіть вручну"
        elif missing_form:
            reason = "Не визначено форму продукту: готова друга страва чи м’ясорослинні консерви — оберіть вручну"
        elif missing_species:
            reason = "Вид м’яса або риби не визначений в одній із назв — оберіть вручну"
        elif exact:
            reason = "Точний збіг назви або налаштованого синоніма"
        elif family_match:
            reason = "Збіг типу продукту: " + _FAMILY_LABELS[query_profile["family"]]
            if specific_matches:
                reason += "; узгоджені основні інгредієнти"
            if query_profile["family"] == "second_course" and len(candidate_profile["base"]) == 1:
                base_label = {"legumes": "бобові", "cereals": "крупи", "potatoes": "картопля", "other": "інше"}[next(iter(candidate_profile["base"]))]
                reason += "; основа: " + base_label
        else:
            reason = "Лише текстова схожість — перевірте вручну"
        ranked.append({
            "id": template.get("id"),
            "name": template.get("name") or canonical,
            "product_name": canonical,
            "score": round(min(score, 0.99), 3),
            "reason": reason,
        })
    return sorted(ranked, key=lambda item: (-item["score"], str(item["name"]), str(item["id"])))
