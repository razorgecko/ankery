"""The German pack's filter.py normalize hook, loaded through the pack loader.

Normalization is no longer an engine module — it is per-pack code the loader
imports from packs/<code>/filter.py and the manager applies to every provider's
output. These tests exercise the real hook the loader returns.
"""

import pytest

from ankery.models import Entry
from ankery.pack import load_pack

normalize = load_pack("de").normalize


def _noun(properties: dict[str, str]) -> Entry:
    return Entry(term="Haus", source="test", category="noun", properties=properties)


def test_strips_leading_definite_article_from_forms():
    entry = normalize(_noun({"genitive_sg": "des Hauses", "nominative_pl": "die Häuser"}))
    assert entry.properties["genitive_sg"] == "Hauses"
    assert entry.properties["nominative_pl"] == "Häuser"


def test_leaves_articleless_forms_untouched():
    entry = normalize(_noun({"genitive_sg": "Hauses", "perfect": "hat gesehen"}))
    assert entry.properties["genitive_sg"] == "Hauses"
    # A multi-word form whose first token is not an article is unchanged.
    assert entry.properties["perfect"] == "hat gesehen"


def test_single_token_article_value_is_preserved():
    # The `gender` value ("das") is a bare article with nothing after it, so it
    # is not stripped down to empty — the form-stripping rule needs a remainder.
    entry = normalize(_noun({"gender": "das", "genitive_sg": "des Hauses"}))
    assert entry.properties["gender"] == "das"
    assert entry.properties["genitive_sg"] == "Hauses"


def test_is_idempotent():
    once = normalize(_noun({"genitive_sg": "des Hauses"}))
    twice = normalize(once)
    assert twice.properties["genitive_sg"] == "Hauses"


def test_article_stripping_applies_only_to_nouns():
    verb = Entry(term="sehen", source="test", category="verb", properties={"perfect": "die Häuser"})
    assert normalize(verb).properties["perfect"] == "die Häuser"


def test_entry_without_category_is_unchanged():
    entry = Entry(term="Haus", source="test", properties={"genitive_sg": "des Hauses"})
    assert normalize(entry).properties["genitive_sg"] == "des Hauses"


def _verb(term: str, **properties: str) -> Entry:
    return Entry(term=term, source="test", category="verb", properties=properties)


@pytest.mark.parametrize(
    ("term", "preposition", "base"),
    [
        ("sich freuen auf", "auf", "sich freuen"),
        ("denken an", "an", "denken"),
        ("sich merken", "", "sich merken"),
        ("sehen", "", "sehen"),
        ("Rad fahren", "", "Rad fahren"),
    ],
)
def test_verb_splits_a_final_preposition_off_the_term(term, preposition, base):
    entry = normalize(_verb(term))
    assert entry.term == term
    assert entry.properties["preposition"] == preposition
    assert entry.properties["base"] == base


def test_fixed_case_preposition_overrides_the_model():
    entry = normalize(_verb("sich unterhalten mit", preposition_case="Akk"))
    assert entry.properties["preposition_case"] == "Dat"


@pytest.mark.parametrize(
    ("written", "expected"),
    [("Akk", "Akk"), ("Akkusativ", "Akk"), ("accusative", "Akk"), ("Dativ", "Dat"), ("Genitiv", ""), ("", "")],
)
def test_two_way_preposition_keeps_the_models_case_cleaned_up(written, expected):
    entry = normalize(_verb("sich freuen auf", preposition_case=written))
    assert entry.properties["preposition_case"] == expected


def test_verb_without_preposition_overwrites_model_supplied_keys():
    entry = normalize(_verb("sehen", preposition="auf", base="x", preposition_case="Akk"))
    assert entry.properties["preposition"] == ""
    assert entry.properties["base"] == "sehen"
    assert entry.properties["preposition_case"] == ""


def test_verb_step_is_idempotent():
    once = normalize(_verb("sich freuen auf", preposition_case="Akkusativ", perfect="hat sich gefreut"))
    twice = normalize(once.model_copy(deep=True))
    assert twice.properties == once.properties


def test_verb_step_applies_only_to_verbs():
    phrase = Entry(term="Lust haben auf", source="test", category="phrase")
    assert "preposition" not in normalize(phrase).properties

    noun = normalize(_noun({"genitive_sg": "des Hauses"}))
    assert noun.properties.keys() == {"genitive_sg"}
