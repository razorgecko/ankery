from ankery.models import WordInfo
from ankery.notedef import (
    NoteDefinition,
    default_css,
    default_field_map,
    load_note_definitions,
)


def _defs() -> dict[str, NoteDefinition]:
    return {d.name: d for d in load_note_definitions()}


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


def test_bundled_definitions_load_with_names_id_and_field_order():
    defs = _defs()

    noun = defs["Noun (DE)"]
    assert noun.model_id == 1986815750
    assert noun.applies_to == "noun"
    # Word leads: Anki keys duplicate detection on the first field.
    assert noun.fields[0] == "Word"
    assert defs["Verb (DE)"].applies_to == "verb"


def test_noun_render_fills_the_noun_model_fields():
    fields = _defs()["Noun (DE)"].render(_noun())

    assert fields == {
        "Article": "das",
        "Word": "Haus",
        "Plural": "Häuser",
        "GenitiveSg": "Hauses",
        "Translation": "house, home",
        "Example": "Das Haus ist groß.",
    }


def test_render_copies_forms_verbatim_no_stripping():
    # Normalization (dropping the article) is the provider's job, not the
    # template's. An article-bearing value must survive the map untouched.
    info = WordInfo(
        word="Haus",
        source="test",
        part_of_speech="noun",
        gender="das",
        inflections={"nominative_pl": "die Häuser", "genitive_sg": "des Hauses"},
    )
    fields = _defs()["Noun (DE)"].render(info)

    assert fields["Plural"] == "die Häuser"
    assert fields["GenitiveSg"] == "des Hauses"


def test_verb_render_fills_present_forms_as_separate_fields():
    fields = _defs()["Verb (DE)"].render(_verb())

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


def test_verb_render_uses_empty_string_for_missing_present_forms():
    info = WordInfo(
        word="x", source="test", part_of_speech="verb",
        inflections={"present_1sg": "bin", "present_3sg": "ist"},
    )
    fields = _defs()["Verb (DE)"].render(info)

    assert fields["Present1sg"] == "bin"
    assert fields["Present3sg"] == "ist"
    assert fields["Present2sg"] == ""
    assert fields["Present3pl"] == ""


def test_render_tolerates_absent_data_without_literal_none():
    # Optional WordInfo fields are None, not absent; they must render "" not
    # the string "None", and missing inflection keys must render "".
    bare = WordInfo(word="Ding", source="test", part_of_speech="noun")
    fields = _defs()["Noun (DE)"].render(bare)

    assert fields == {
        "Word": "Ding",
        "Article": "",
        "Plural": "",
        "GenitiveSg": "",
        "Translation": "",
        "Example": "",
    }


def test_applies_routes_by_part_of_speech():
    defs = _defs()

    assert defs["Noun (DE)"].applies(_noun())
    assert not defs["Noun (DE)"].applies(_verb())
    assert defs["Verb (DE)"].applies(_verb())
    # An adjective matches neither, so it falls through to the catch-all.
    adj = WordInfo(word="schön", source="test", part_of_speech="adjective")
    assert not any(d.applies(adj) for d in defs.values())


def test_default_css_is_the_bundled_stylesheet():
    css = default_css()

    # The shared fallback look, kept in notes/style.css apart from any field map.
    assert ".card {" in css
    assert "font-family: arial;" in css


def test_bundled_definitions_carry_no_css_of_their_own():
    # The per-POS notes leave styling to the shared default, so the sink can
    # supply it (catch-all model or the bundle) without a definition overriding.
    assert all(d.css == "" for d in _defs().values())


def test_default_field_map_is_the_procedural_catch_all():
    info = WordInfo(
        word="Buch",
        source="test",
        gender="das",
        translations=["book"],
        definitions=["gebundene Seiten"],
        inflections={"nominative_pl": "Bücher"},
    )
    fields = default_field_map(info)

    # Nouns show their article so gender is learned with the word.
    assert fields["Front"] == "das Buch"
    assert "book" in fields["Back"]
    assert "nominative_pl: Bücher" in fields["Back"]
