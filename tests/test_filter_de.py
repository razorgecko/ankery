"""The German pack's filter.py normalize hook, loaded through the pack loader.

Normalization is no longer an engine module — it is per-pack code the loader
imports from langs/<code>/filter.py and the manager applies to every provider's
output. These tests exercise the real hook the loader returns.
"""

from ankery.models import WordInfo
from ankery.pack import load_pack

normalize = load_pack("de").normalize


def _noun(features: dict[str, str]) -> WordInfo:
    return WordInfo(word="Haus", source="test", part_of_speech="noun", features=features)


def test_strips_leading_definite_article_from_forms():
    info = normalize(_noun({"genitive_sg": "des Hauses", "nominative_pl": "die Häuser"}))
    assert info.features["genitive_sg"] == "Hauses"
    assert info.features["nominative_pl"] == "Häuser"


def test_leaves_articleless_forms_untouched():
    info = normalize(_noun({"genitive_sg": "Hauses", "perfect": "hat gesehen"}))
    assert info.features["genitive_sg"] == "Hauses"
    # A multi-word form whose first token is not an article is unchanged.
    assert info.features["perfect"] == "hat gesehen"


def test_single_token_article_value_is_preserved():
    # The `gender` value ("das") is a bare article with nothing after it, so it
    # is not stripped down to empty — the form-stripping rule needs a remainder.
    info = normalize(_noun({"gender": "das", "genitive_sg": "des Hauses"}))
    assert info.features["gender"] == "das"
    assert info.features["genitive_sg"] == "Hauses"


def test_is_idempotent():
    once = normalize(_noun({"genitive_sg": "des Hauses"}))
    twice = normalize(once)
    assert twice.features["genitive_sg"] == "Hauses"
