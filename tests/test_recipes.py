from anki_deckbuilder.models import WordInfo
from anki_deckbuilder.recipes import (
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
        inflections={"plural": "Häuser", "genitive_sg": "Hauses"},
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


def test_verb_map_assembles_the_present_paradigm_with_pronouns():
    fields = map_verb_fields(_verb())

    assert fields["Infinitive"] == "sehen"
    assert fields["Aux"] == "haben"
    assert fields["Preterite"] == "sah"
    assert fields["Perfect"] == "hat gesehen"
    assert fields["Present"] == (
        "ich sehe / du siehst / er sieht / wir sehen / ihr seht / sie sehen"
    )


def test_present_paradigm_skips_missing_forms():
    info = WordInfo(
        word="x", source="test", part_of_speech="verb",
        inflections={"present_1sg": "bin", "present_3sg": "ist"},
    )
    assert map_verb_fields(info)["Present"] == "ich bin / er ist"


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
