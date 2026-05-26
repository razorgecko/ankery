from ankery.models import WordInfo
from ankery.recipes import (
    build_recipes,
    map_noun_fields,
    map_verb_fields,
)


def _noun() -> WordInfo:
    return WordInfo(
        word="Haus",
        source="test",
        part_of_speech="noun",
        gender="das",
        translations=["house", "home"],
        examples=["Das Haus ist groß."],
        inflections={"nominative_pl": "Häuser", "genitive_sg": "Hauses"},
    )


def _verb() -> WordInfo:
    return WordInfo(
        word="sehen",
        source="test",
        part_of_speech="verb",
        translations=["to see"],
        examples=["Ich sehe dich."],
        inflections={
            "present_1sg": "sehe",
            "present_2sg": "siehst",
            "present_3sg": "sieht",
            "present_1pl": "sehen",
            "present_2pl": "seht",
            "present_3pl": "sehen",
            "preterite": "sah",
            "perfect": "hat gesehen",
            "auxiliary": "haben",
        },
    )


def test_noun_map_fills_the_noun_model_fields():
    fields = map_noun_fields(_noun())

    assert fields == {
        "Article": "das",
        "Word": "Haus",
        "Plural": "Häuser",
        "GenitiveSg": "Hauses",
        "Translation": "house, home",
        "Example": "Das Haus ist groß.",
    }


def test_noun_map_copies_forms_verbatim_no_stripping():
    # Normalization (dropping the article) is the provider's job, not the map's.
    # The map must copy the form through untouched — so an article-bearing value
    # would survive, proving the map is not where stripping happens.
    info = WordInfo(
        word="Haus",
        source="test",
        part_of_speech="noun",
        gender="das",
        inflections={"nominative_pl": "die Häuser", "genitive_sg": "des Hauses"},
    )
    fields = map_noun_fields(info)

    assert fields["Plural"] == "die Häuser"
    assert fields["GenitiveSg"] == "des Hauses"


def test_verb_map_fills_present_forms_as_separate_fields():
    fields = map_verb_fields(_verb())

    assert fields["Infinitive"] == "sehen"
    assert fields["Aux"] == "haben"
    assert fields["Preterite"] == "sah"
    assert fields["Perfect"] == "hat gesehen"
    # Each present form is its own field; the pronoun/slash layout lives in the
    # Anki card template, not the map.
    assert fields["Present1sg"] == "sehe"
    assert fields["Present2sg"] == "siehst"
    assert fields["Present3sg"] == "sieht"
    assert fields["Present1pl"] == "sehen"
    assert fields["Present2pl"] == "seht"
    assert fields["Present3pl"] == "sehen"


def test_verb_map_uses_empty_string_for_missing_present_forms():
    info = WordInfo(
        word="x", source="test", part_of_speech="verb",
        inflections={"present_1sg": "bin", "present_3sg": "ist"},
    )
    fields = map_verb_fields(info)

    assert fields["Present1sg"] == "bin"
    assert fields["Present3sg"] == "ist"
    assert fields["Present2sg"] == ""
    assert fields["Present3pl"] == ""


def test_maps_tolerate_absent_data():
    bare = WordInfo(word="Ding", source="test", part_of_speech="noun")
    fields = map_noun_fields(bare)

    assert fields["Word"] == "Ding"
    assert fields["Article"] == ""
    assert fields["Plural"] == ""


def test_build_recipes_routes_noun_and_verb_by_pos():
    recipes = build_recipes(noun_note_type="Noun (DE)", verb_note_type="Verb (DE)")

    noun_recipe = next(r for r in recipes if r.applies_to(_noun()))
    verb_recipe = next(r for r in recipes if r.applies_to(_verb()))

    assert noun_recipe.note_type == "Noun (DE)"
    assert verb_recipe.note_type == "Verb (DE)"
    # An adjective matches neither, so it falls through to the catch-all.
    adj = WordInfo(word="schön", source="test", part_of_speech="adjective")
    assert not any(r.applies_to(adj) for r in recipes)


def test_build_recipes_skips_disabled_part_of_speech():
    recipes = build_recipes(noun_note_type="", verb_note_type="Verb (DE)")

    assert [r.note_type for r in recipes] == ["Verb (DE)"]
    assert not any(r.applies_to(_noun()) for r in recipes)
