from pathlib import Path

import pytest

import ankery
from ankery.models import WordInfo
from ankery.notedef import (
    NoteDefinition,
    NoteDefinitionError,
    default_field_map,
    load_notes_from_dir,
)

# The bundled German pack's note definitions live here now (not a top-level
# notes/ dir merged by config).
DE_NOTES = Path(ankery.__file__).parent / "langs" / "de" / "notes"


def _defs() -> dict[str, NoteDefinition]:
    return {d.name: d for d in load_notes_from_dir(DE_NOTES)}


def _noun() -> WordInfo:
    return WordInfo(
        word="Haus",
        source="test",
        part_of_speech="noun",
        translations=["house", "home"],
        examples=["Das Haus ist groß."],
        features={"gender": "das", "nominative_pl": "Häuser", "genitive_sg": "Hauses"},
    )


def _verb() -> WordInfo:
    return WordInfo(
        word="sehen",
        source="test",
        part_of_speech="verb",
        translations=["to see"],
        examples=["Ich sehe dich."],
        features={
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
    # Normalization (dropping the article) is the pack filter's job, not the
    # template's. An article-bearing feature value must survive the map untouched.
    info = WordInfo(
        word="Haus",
        source="test",
        part_of_speech="noun",
        features={"gender": "das", "nominative_pl": "die Häuser", "genitive_sg": "des Hauses"},
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
        features={"present_1sg": "bin", "present_3sg": "ist"},
    )
    fields = _defs()["Verb (DE)"].render(info)

    assert fields["Present1sg"] == "bin"
    assert fields["Present3sg"] == "ist"
    assert fields["Present2sg"] == ""
    assert fields["Present3pl"] == ""


def test_render_tolerates_absent_data_without_literal_none():
    # Optional WordInfo fields are None, not absent; they must render "" not the
    # string "None", and missing feature keys must render "".
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


def test_bundled_definitions_carry_no_css_of_their_own():
    # The per-POS notes leave styling to the pack's style.css, so the sink can
    # supply it (catch-all model or the pack fallback) without a definition
    # overriding.
    assert all(d.css == "" for d in _defs().values())


def test_load_notes_orders_by_stem_for_routing_precedence():
    # Stem order is routing order: noun_de before verb_de.
    assert [d.name for d in load_notes_from_dir(DE_NOTES)] == ["Noun (DE)", "Verb (DE)"]


def test_missing_directory_yields_no_definitions(tmp_path):
    assert load_notes_from_dir(tmp_path / "absent") == []


def test_malformed_note_file_raises(tmp_path):
    (tmp_path / "broken.toml").write_text("name = \n", "utf-8")  # missing value
    with pytest.raises(NoteDefinitionError):
        load_notes_from_dir(tmp_path)


def test_default_field_map_is_the_neutral_catch_all():
    info = WordInfo(
        word="Buch",
        source="test",
        translations=["book"],
        definitions=["gebundene Seiten"],
        features={"gender": "das", "nominative_pl": "Bücher"},
    )
    fields = default_field_map(info)

    # No article prefix, no German-specific layout: front is the bare word.
    assert fields["Front"] == "Buch"
    assert "book" in fields["Back"]
    assert "gender: das" in fields["Back"]
    assert "nominative_pl: Bücher" in fields["Back"]
