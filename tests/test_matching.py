from app.matching import infer_subcategory, rank_templates


LIBRARY = [
    {"id": "pork", "name": "Свинина тушкована", "aliases": ["свинина", "свинина тушеная"]},
    {"id": "beef", "name": "Яловичина тушкована"},
    {"id": "breakfast", "name": "Сніданки м’ясні. Свинина пряна"},
    {"id": "soup", "name": "Суп гречаний з м’ясом свинини (Готова до вживання перша страва)"},
    {"id": "porridge", "name": "Каша гречана з яловичиною"},
    {"id": "barley", "name": "Каша ячна з куркою та овочами (Готова до вживання друга страва)"},
    {"id": "sprat", "name": "Кілька чорноморська нерозібрана у томатному соусі"},
    {"id": "herring", "name": "Салака у томатному соусі"},
    {"id": "tuna", "name": "Тунець натуральний"},
    {"id": "liver", "name": "Печінка тріски"},
    {"id": "wheat", "name": "Сухарики армійські пшеничні"},
    {"id": "rye", "name": "Сухарики армійські житні"},
    {"id": "jam1", "name": "Джем абрикосовий", "aliases": ["абрикосовый джем"]},
    {"id": "jam2", "name": "Джем абрикосовий", "aliases": ["абрикосовый джем"]},
]


def ids(query):
    return [entry["id"] for entry in rank_templates(query, LIBRARY)]


def test_pork_does_not_select_soup_breakfast_or_beef():
    assert ids("Свинина тушкована") == ["pork"]
    assert ids("свинина тушеная") == ["pork"]


def test_soup_with_pork_stays_first_course():
    assert ids("Суп гречаний зі свининою") == ["soup"]
    assert ids("Готова до вживання перша страва") == ["soup"]


def test_second_courses_reject_different_grain_and_protein():
    assert ids("Каша ячна з куркою") == ["barley"]
    assert ids("Каша гречана з яловичиною") == ["porridge"]


def test_fish_species_are_not_interchangeable():
    assert ids("Кілька в томаті") == ["sprat"]
    assert ids("салака") == ["herring"]
    assert ids("Тунець") == ["tuna"]
    assert ids("печінка тріски") == ["liver"]


def test_rye_wheat_are_separate_and_russian_alias_supported():
    assert ids("Сухарики житні") == ["rye"]
    assert ids("Сухарики пшеничні") == ["wheat"]
    assert ids("Сухари ржаные") == ["rye"]


def test_duplicate_variants_remain_ambiguous():
    result = rank_templates("абрикосовый джем", LIBRARY)
    assert {entry["id"] for entry in result} == {"jam1", "jam2"}
    assert result[0]["score"] == result[1]["score"]


def test_unknown_product_does_not_receive_high_confidence():
    assert not rank_templates("Молоко незбиране", LIBRARY)


def test_custom_alias_and_exclusion_are_respected():
    templates = [{"id": "custom", "name": "Новий шаблон", "aliases": ["Продукт Альфа"], "exclusions": ["алерген"]}]
    assert rank_templates("Продукт Альфа", templates)[0]["score"] == 0.99
    assert not rank_templates("Продукт Альфа алерген", templates)


def test_explicit_alias_cannot_cross_known_food_categories():
    templates = [{"id": "pork", "name": "Свинина тушкована", "aliases": ["Суп зі свининою"]}]
    assert not rank_templates("Суп зі свининою", templates)


def test_disabled_templates_are_not_returned():
    assert not rank_templates("Сіль", [{"id": "x", "name": "Сіль", "enabled": False}])


def test_blank_query_has_no_candidates():
    assert not rank_templates("", LIBRARY)


def test_liver_pate_with_pork_fat_is_not_meat_pate_or_cod_liver():
    templates = [*LIBRARY, {"id": "pate", "name": "Консерви м’ясні стерилізовані. Паштет «М’ясний»"}]
    assert not rank_templates("Паштет печінковий із свинячим жиром", templates)
    assert not rank_templates("Паштет печеночный со свиным жиром", templates)


def test_liver_pate_can_use_a_matching_liver_pate_template():
    templates = [
        {"id": "meat", "name": "Паштет м’ясний"},
        {"id": "liver", "name": "Паштет печінковий із свинячим жиром"},
    ]
    result = rank_templates("Паштет печінковий із свинячим жиром", templates)
    assert [entry["id"] for entry in result] == ["liver"]
    assert result[0]["score"] == 0.99


def test_unspecified_stew_meat_species_remains_low_confidence():
    result = rank_templates("Консерви м’ясні «М’ясо тушковане»", LIBRARY)
    assert {entry["id"] for entry in result} == {"pork", "beef"}
    assert all(entry["score"] < 0.5 for entry in result)


def test_broad_alias_cannot_invent_missing_meat_species():
    templates = [{"id": "pork", "name": "Свинина тушкована", "aliases": ["М’ясо тушковане"]}]
    assert rank_templates("М’ясо тушковане", templates)[0]["score"] < 0.5


def actual_library():
    """Use canonical names from all 22 actual supplied Word templates."""
    import json
    from pathlib import Path
    from app.documents import inspect_template
    root = Path(__file__).resolve().parents[1]
    records = json.loads((root / "assets/library.json").read_text())
    return [{**record, "product_name": inspect_template((root / "assets/templates" / record["filename"]).read_bytes())["product_name"]} for record in records]


def test_new_liver_pate_matches_actual_814_and_rejects_meat_pate():
    result = rank_templates("Паштет печінковий із свинячим жиром", actual_library())
    assert [entry["id"] for entry in result] == ["sample-814"]
    assert result[0]["score"] > .9


def test_actual_sprats_separate_from_kilka_and_salaka():
    library = actual_library()
    for query, expected in [("Шпроти в олії", "sample-861"), ("Шпроты в масле", "sample-861"), ("Кілька в томатному соусі", "sample-1232"), ("Салака у томатному соусі", "sample-1137")]:
        result = rank_templates(query, library)
        assert [entry["id"] for entry in result] == [expected]
        assert result[0]["score"] > .7


def test_actual_ready_dishes_separate_legumes_cereals_and_potatoes():
    library = actual_library()
    for query, expected in [
        ("Картопля з яловичиною (Готова друга страва)", "sample-907"),
        ("Каша гречана з яловичиною (Готова друга страва)", "sample-909"),
        ("Квасоля з куркою (Готова друга страва)", "sample-1124"),
        ("Каша ячна з куркою (Готова до вживання друга страва)", "sample-1095"),
    ]:
        assert [entry["id"] for entry in rank_templates(query, library)] == [expected]
    assert not rank_templates("Бобові з яловичиною (Готова друга страва)", library)


def test_actual_explicit_canned_buckwheat_rejects_ready_second_course():
    result = rank_templates("Консерви м’ясорослинні. Каша гречана з яловичиною", actual_library())
    assert [entry["id"] for entry in result] == ["sample-1082"]
    assert result[0]["score"] > .7


def test_unspecified_buckwheat_form_offers_both_actual_variants_for_review():
    result = rank_templates("Каша гречана з яловичиною", actual_library())
    assert {entry["id"] for entry in result} == {"sample-1082", "sample-909"}
    assert all(entry["score"] < .5 for entry in result)


def test_base_subgroup_metadata_applies_to_generic_ready_template():
    records = [
        {"id": "beans", "name": "Готова друга страва", "subcategory": "legumes"},
        {"id": "grains", "name": "Готова друга страва", "subcategory": "cereals"},
        {"id": "potatoes", "name": "Готова друга страва", "subcategory": "potatoes"},
    ]
    for name, expected in [("Каша горохова", "beans"), ("Сочевиця", "beans"), ("Нут", "beans"), ("Каша рисова", "grains"), ("Картопля", "potatoes")]:
        assert [entry["id"] for entry in rank_templates(name + " (Готова друга страва)", records)] == [expected]


def test_alias_and_inconsistent_metadata_cannot_override_explicit_base():
    actual = next(entry for entry in actual_library() if entry["id"] == "sample-909")
    potato_name = "Картопля з яловичиною (Готова друга страва)"
    bean_name = "Квасоля з яловичиною (Готова друга страва)"
    actual["aliases"] = [potato_name, bean_name]
    assert not rank_templates(potato_name, [actual])
    assert not rank_templates(bean_name, [actual])
    actual["subcategory"] = "potatoes"
    assert not rank_templates(actual["product_name"], [actual])


def test_broad_alias_cannot_override_explicit_meal_form():
    actual = next(entry for entry in actual_library() if entry["id"] == "sample-1082")
    alias = "Каша гречана з яловичиною (Готова друга страва)"
    actual["aliases"] = [alias]
    assert not rank_templates(alias, [actual])


def test_subcategory_inference_uses_food_base_instead_of_generic_word_porridge():
    for text in ["Квасоля з куркою", "Горохова каша", "Сочевиця", "Чечевица", "Нут", "Бобові з овочами"]:
        assert infer_subcategory(text) == "legumes"
    for text in ["Каша гречана", "Рис із куркою", "Каша ячна", "Булгур з яловичиною"]:
        assert infer_subcategory(text) == "cereals"
    assert infer_subcategory("Картопля з яловичиною") == "potatoes"
    assert infer_subcategory("Суп гречаний із свининою") == ""
    assert infer_subcategory("Паштет м’ясний") == ""
    assert infer_subcategory("Невідомий продукт") == ""


def test_distinct_or_unspecified_legumes_are_only_low_subgroup_suggestions():
    templates = [{"id": "beans", "name": "Квасоля з овочами (Готова друга страва)", "subcategory": "legumes", "aliases": ["Горох з овочами (Готова друга страва)"]}]
    for name in ["Горох", "Сочевиця", "Нут", "Бобові"]:
        result = rank_templates(name + " з овочами (Готова друга страва)", templates)
        assert len(result) == 1
        assert result[0]["score"] < .5
        assert "бобові" in result[0]["reason"]
