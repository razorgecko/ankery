from pathlib import Path

import pytest

import ankery
from ankery.models import Entry
from ankery.notedef import (
    NoteDefinition,
    NoteDefinitionError,
    load_notes_from_dir,
    merge_note_definitions,
)

# The bundled German pack's note definitions live here now (not a top-level
# notes/ dir merged by config).
DE_NOTES = Path(ankery.__file__).parent / "packs" / "de" / "notes"


def _defs() -> dict[str, NoteDefinition]:
    return {d.name: d for d in load_notes_from_dir(DE_NOTES)}


def _noun() -> Entry:
    return Entry(
        term="Haus",
        source="test",
        category="noun",
        collections={"translations": ["house", "home"], "examples": ["Das Haus ist groß."]},
        properties={"gender": "das", "nominative_pl": "Häuser", "genitive_sg": "Hauses"},
    )


def _verb() -> Entry:
    return Entry(
        term="sehen",
        source="test",
        category="verb",
        collections={"translations": ["to see"], "examples": ["Ich sehe dich."]},
        properties={
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


def test_bundled_definitions_load_with_names_and_field_order():
    defs = _defs()

    noun = defs["Ankery DE: Noun"]
    assert noun.applies_to == "noun"
    # Word leads: Anki keys duplicate detection on the first field.
    assert noun.fields[0] == "Word"
    assert defs["Ankery DE: Verb"].applies_to == "verb"


def test_noun_render_fills_the_noun_model_fields():
    fields = _defs()["Ankery DE: Noun"].render(_noun())

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
    entry = Entry(
        term="Haus",
        source="test",
        category="noun",
        properties={"gender": "das", "nominative_pl": "die Häuser", "genitive_sg": "des Hauses"},
    )
    fields = _defs()["Ankery DE: Noun"].render(entry)

    assert fields["Plural"] == "die Häuser"
    assert fields["GenitiveSg"] == "des Hauses"


def test_verb_render_fills_present_forms_as_separate_fields():
    fields = _defs()["Ankery DE: Verb"].render(_verb())

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
    entry = Entry(
        term="x", source="test", category="verb",
        properties={"present_1sg": "bin", "present_3sg": "ist"},
    )
    fields = _defs()["Ankery DE: Verb"].render(entry)

    assert fields["Present1sg"] == "bin"
    assert fields["Present3sg"] == "ist"
    assert fields["Present2sg"] == ""
    assert fields["Present3pl"] == ""


def test_render_tolerates_absent_data_without_literal_none():
    # Optional Entry fields are None, not absent; they must render "" not the
    # string "None", and missing feature/section keys must render "".
    bare = Entry(term="Ding", source="test", category="noun")
    fields = _defs()["Ankery DE: Noun"].render(bare)

    assert fields == {
        "Word": "Ding",
        "Article": "",
        "Plural": "",
        "GenitiveSg": "",
        "Translation": "",
        "Example": "",
    }


def test_applies_routes_by_category():
    defs = _defs()

    assert defs["Ankery DE: Noun"].applies(_noun())
    assert not defs["Ankery DE: Noun"].applies(_verb())
    assert defs["Ankery DE: Verb"].applies(_verb())
    # An adjective matches no bespoke note (none `applies`), so it routes to the
    # pack default note "Ankery DE: Word" — which is itself fallback-only (`applies` is
    # always False; routing selects it via is_default, see test_manager).
    adj = Entry(term="schön", source="test", category="adjective")
    assert not any(d.applies(adj) for d in defs.values())
    assert defs["Ankery DE: Word"].is_default
    assert not defs["Ankery DE: Word"].applies(adj)
    assert not defs["Ankery DE: Noun"].is_default


def test_default_note_renders_bundled_grammar_per_category():
    d = _defs()["Ankery DE: Word"]

    prep = Entry(
        term="mit", source="test", category="preposition",
        collections={"translations": ["with"]}, properties={"governs_case": "dative"},
    )
    assert d.render(prep)["Grammar"] == "+ dative"
    assert d.render(prep)["Translation"] == "with"
    assert d.render(prep)["PartOfSpeech"] == "preposition"

    adj = Entry(
        term="schnell", source="test", category="adjective",
        properties={"comparative": "schneller", "superlative": "am schnellsten"},
    )
    assert d.render(adj)["Grammar"] == "Steigerung: schneller / am schnellsten"

    # A category with none of the bundled properties (e.g. a non-gradable adverb) yields
    # an empty Grammar; the card hides the block via its {{#Grammar}} section.
    adv = Entry(term="heute", source="test", category="adverb",
                collections={"translations": ["today"]})
    assert d.render(adv)["Grammar"] == ""


def test_bundled_definitions_carry_no_css_of_their_own():
    # The per-category notes leave styling to the pack's style.css, so the sink can
    # supply it (catch-all model or the pack fallback) without a definition
    # overriding.
    assert all(d.css == "" for d in _defs().values())


def test_load_notes_orders_by_stem_for_routing_precedence():
    # Stem order is routing order: default_de, noun_de, phrase_de, verb_de.
    assert [d.name for d in load_notes_from_dir(DE_NOTES)] == [
        "Ankery DE: Word", "Ankery DE: Noun", "Ankery DE: Phrase", "Ankery DE: Verb",
    ]


def test_missing_directory_yields_no_definitions(tmp_path):
    assert load_notes_from_dir(tmp_path / "absent") == []


def test_malformed_note_file_raises(tmp_path):
    (tmp_path / "broken.toml").write_text("name = \n", "utf-8")  # missing value
    with pytest.raises(NoteDefinitionError):
        load_notes_from_dir(tmp_path)


def _write_note(directory: Path, stem: str, name: str, applies_to: str | None):
    directory.mkdir(parents=True, exist_ok=True)
    applies = f'applies_to = "{applies_to}"\n' if applies_to is not None else ""
    (directory / f"{stem}.toml").write_text(
        f'name = "{name}"\n{applies}[map]\nFront = "{{{{ term }}}}"\n', "utf-8"
    )


def test_two_notes_serving_one_category_in_a_directory_raise(tmp_path):
    # Silently letting the first-by-stem win and the other go dead is the trap we
    # close: a same-category clash names both files and the category.
    _write_note(tmp_path, "a_noun", "Noun A", "noun")
    _write_note(tmp_path, "b_noun", "Noun B", "noun")
    with pytest.raises(NoteDefinitionError, match="both serve category 'noun'"):
        load_notes_from_dir(tmp_path)


def test_two_default_notes_in_a_directory_raise(tmp_path):
    # At most one pack default note per directory; the clash names it as such.
    _write_note(tmp_path, "a_default", "Default A", "*")
    _write_note(tmp_path, "b_default", "Default B", "*")
    with pytest.raises(NoteDefinitionError, match="both serve the pack default note"):
        load_notes_from_dir(tmp_path)


def test_notes_without_applies_to_do_not_collide(tmp_path):
    # A note with no applies_to matches nothing, so two of them are not a clash.
    _write_note(tmp_path, "a", "Catchall A", None)
    _write_note(tmp_path, "b", "Catchall B", None)
    assert [d.name for d in load_notes_from_dir(tmp_path)] == ["Catchall A", "Catchall B"]


def _note(name: str, applies_to: str | None) -> NoteDefinition:
    return NoteDefinition(name=name, field_map={"Front": "{{ term }}"}, applies_to=applies_to)


def test_merge_override_replaces_same_category_in_place():
    base = [_note("Ankery DE: Noun", "noun"), _note("Ankery DE: Verb", "verb")]
    override = [_note("Simple Noun", "noun")]

    merged = merge_note_definitions(base, override)

    # The override's noun takes the base noun's slot; the verb is untouched.
    assert [d.name for d in merged] == ["Simple Noun", "Ankery DE: Verb"]


def test_merge_appends_a_new_category():
    base = [_note("Ankery DE: Noun", "noun")]
    override = [_note("Adjective", "adjective")]

    merged = merge_note_definitions(base, override)

    assert [d.name for d in merged] == ["Ankery DE: Noun", "Adjective"]


def test_merge_carries_through_none_keyed_notes_without_replacing():
    base = [_note("Ankery DE: Noun", "noun")]
    override = [_note("Loose", None)]

    merged = merge_note_definitions(base, override)

    # A None-keyed override note keys on nothing: it appends, replaces no base.
    assert [d.name for d in merged] == ["Ankery DE: Noun", "Loose"]


def test_merge_empty_override_returns_the_base_unchanged():
    base = [_note("Ankery DE: Noun", "noun"), _note("Ankery DE: Verb", "verb")]

    assert merge_note_definitions(base, []) == base


# ---------------------------------------------------------------------------
# Escaping of untrusted provider data
# ---------------------------------------------------------------------------


def test_field_map_escapes_provider_html():
    # Provider-supplied values can contain markup; a note map must escape it so
    # it can't inject into the card. Card structure lives in templates, not here.
    note = NoteDefinition(
        name="X", field_map={"Front": "{{ term }}", "Back": "{{ collections.translations[0] }}"}
    )
    entry = Entry(
        term="<b>x</b>", source="test",
        collections={"translations": ["<script>alert(1)</script>"]},
    )
    fields = note.render(entry)

    assert fields["Front"] == "&lt;b&gt;x&lt;/b&gt;"
    assert "<script>" not in fields["Back"]
    assert "&lt;script&gt;" in fields["Back"]


def test_field_map_safe_filter_opts_out_of_escaping():
    # A pack that deliberately emits HTML from a value can use `| safe`.
    note = NoteDefinition(name="X", field_map={"Back": "{{ collections.definitions[0] | safe }}"})
    entry = Entry(term="x", source="test", collections={"definitions": ["<i>emphasis</i>"]})

    assert note.render(entry)["Back"] == "<i>emphasis</i>"
