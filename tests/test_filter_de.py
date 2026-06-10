"""The German pack's filter.py normalize hook, loaded through the pack loader.

Normalization is no longer an engine module — it is per-pack code the loader
imports from packs/<code>/filter.py and the manager applies to every provider's
output. These tests exercise the real hook the loader returns.
"""

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
