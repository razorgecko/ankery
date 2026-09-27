import pytest

from ankery.hints import parse_term, resolve_category_hint, split_category_hint


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Buch", ("Buch", None)),
        ("schnell:adj", ("schnell", "adj")),
        ("  Haus : noun ", ("Haus", "noun")),
        (":noun", ("", "noun")),
        ("auf:", ("auf", "")),
    ],
)
def test_split_category_hint(raw, expected):
    assert split_category_hint(raw) == expected


def test_resolve_category_hint_exact_and_prefix():
    names = ["adjective", "adverb", "noun", "preposition", "verb"]
    assert resolve_category_hint("noun", names) == "noun"  # exact
    assert resolve_category_hint("v", names) == "verb"  # unique prefix
    assert resolve_category_hint("adj", names) == "adjective"
    assert resolve_category_hint("PREP", names) == "preposition"  # case-insensitive


def test_resolve_category_hint_rejects_unknown():
    with pytest.raises(ValueError, match="unknown category"):
        resolve_category_hint("xyz", ["noun", "verb"])


def test_resolve_category_hint_rejects_ambiguous_prefix():
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_category_hint("ad", ["adjective", "adverb"])


def test_resolve_category_hint_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        resolve_category_hint("", ["noun"])


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Buch", ("Buch", None)),
        (" Bank:n ", ("Bank", "noun")),
        ("schnell:ADJ", ("schnell", "adjective")),
    ],
)
def test_parse_term_splits_and_resolves(raw, expected):
    assert parse_term(raw, ["adjective", "noun", "verb"]) == expected


@pytest.mark.parametrize("raw", ["", "   ", ":noun", " : xyz"])
def test_parse_term_rejects_an_empty_term_before_resolving_the_hint(raw):
    with pytest.raises(ValueError, match="empty term"):
        parse_term(raw, ["noun"])


@pytest.mark.parametrize(
    "raw, message", [("Bank:xyz", "unknown category"), ("auf:", "empty category hint")]
)
def test_parse_term_propagates_hint_errors(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_term(raw, ["noun"])
