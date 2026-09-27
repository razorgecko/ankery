"""The German pack's filter.py normalize hook, loaded through the pack loader.

Normalization is no longer an engine module — it is per-pack code the loader
imports from packs/<code>/filter.py and the manager applies to every provider's
output. These tests exercise the real hook the loader returns.
"""

import re

import pytest

from ankery.models import Entry
from ankery.pack import load_pack

normalize = load_pack("de").normalize


def _noun(properties: dict[str, str]) -> Entry:
    return Entry(term="Haus", source="test", category="noun", properties=properties)


def test_strips_leading_definite_article_from_forms():
    entry = normalize(_noun({"gender": "das", "genitive_sg": "des Hauses", "nominative_pl": "die Häuser"}))
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


@pytest.mark.parametrize(
    ("term", "properties", "headword"),
    [
        ("Mutter", {"gender": "die", "nominative_pl": "Muttern"}, "Mutter (die), Muttern"),
        ("Milch", {"gender": "die"}, "Milch (die)"),
        ("Mutter", {"gender": "die", "nominative_pl": "die Muttern"}, "Mutter (die), Muttern"),
        ("die See", {"gender": "die", "nominative_pl": "Seen"}, "See (die), Seen"),
    ],
)
def test_noun_headword_is_term_gender_and_bare_plural(term, properties, headword):
    entry = normalize(Entry(term=term, source="test", category="noun", properties=properties))
    assert entry.properties["headword"] == headword


def test_noun_article_is_stripped_from_the_term():
    entry = normalize(Entry(term="die See", source="test", category="noun", properties={"gender": "die"}))
    assert entry.term == "See"


@pytest.mark.parametrize("plural", [{}, {"nominative_pl": "Ferien"}, {"nominative_pl": "x"}])
def test_noun_without_gender_is_plural_only(plural):
    entry = normalize(Entry(term="Ferien", source="test", category="noun", properties=plural))
    assert entry.properties["headword"] == "Ferien (Pl.)"
    assert entry.properties["nominative_pl"] == "Ferien"


def test_noun_headword_overwrites_the_models():
    entry = normalize(_noun({"gender": "das", "nominative_pl": "Häuser", "headword": "das Haus"}))
    assert entry.properties["headword"] == "Haus (das), Häuser"


@pytest.mark.parametrize(
    ("term", "properties"),
    [("die See", {"gender": "die", "nominative_pl": "die Seen"}), ("Ferien", {})],
)
def test_noun_step_is_idempotent(term, properties):
    once = normalize(Entry(term=term, source="test", category="noun", properties=properties))
    twice = normalize(once.model_copy(deep=True))
    assert twice.term == once.term
    assert twice.properties == once.properties


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


@pytest.mark.parametrize(
    ("term", "properties", "headword"),
    [
        ("sich freuen auf", {"perfect": "hat sich gefreut"}, "sich freuen auf, hat sich gefreut"),
        ("sehen", {"perfect": "hat gesehen"}, "sehen, hat gesehen"),
        ("sehen", {}, "sehen"),
        ("sehen", {"perfect": "hat gesehen", "headword": "sehen"}, "sehen, hat gesehen"),
    ],
)
def test_verb_headword_is_term_and_perfect(term, properties, headword):
    assert normalize(_verb(term, **properties)).properties["headword"] == headword


def test_verb_step_is_idempotent():
    once = normalize(_verb("sich freuen auf", preposition_case="Akkusativ", perfect="hat sich gefreut"))
    twice = normalize(once.model_copy(deep=True))
    assert twice.properties == once.properties


def test_phrase_passes_through_unchanged():
    # The noun step would strip the leading article from the term, and the verb
    # step would add its keys.
    phrase = Entry(term="die Katze im Sack kaufen", source="test", category="phrase")
    out = normalize(phrase.model_copy(deep=True))
    assert out.term == "die Katze im Sack kaufen"
    assert out.properties == {}


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            _noun({"gender": "der", "nominative_pl": "Jungen/Jungs"}),
            "Haus: nominative_pl 'Jungen/Jungs' lists alternative forms",
        ),
        (
            Entry(term="Arbeit(s)zeit", source="test", category="noun", properties={"gender": "die"}),
            "Arbeit(s)zeit: term 'Arbeit(s)zeit' lists alternative forms",
        ),
        (
            _verb("backen", perfect="hat gebacken/gebackt"),
            "backen: perfect 'hat gebacken/gebackt' lists alternative forms",
        ),
    ],
)
def test_alternative_forms_in_a_headword_input_warn(entry, message):
    with pytest.warns(UserWarning, match=re.escape(message)):
        normalized = normalize(entry)
    assert normalized.properties["headword"]  # the entry still goes through


def test_plural_only_noun_with_variants_warns_once(recwarn):
    normalize(Entry(term="Eltern/Altern", source="test", category="noun"))
    assert len(recwarn) == 1


def test_variants_outside_the_headword_do_not_warn(recwarn):
    # A genitive like "Land(e)s" is common and never part of the headword.
    normalize(_noun({"gender": "das", "nominative_pl": "Häuser", "genitive_sg": "Haus(e)s"}))
    normalize(_verb("backen", perfect="hat gebacken", preterite="backte/buk"))
    assert len(recwarn) == 0
