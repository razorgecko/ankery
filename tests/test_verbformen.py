from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from anki_deckbuilder.providers.base import ProviderError
from anki_deckbuilder.providers.verbformen import VerbformenProvider, _separable

FIXTURES = Path(__file__).parent / "fixtures"
NOUN_URL = "https://www.verbformen.com/declension/nouns/Haus.htm"
VERB_URL = "https://www.verbformen.com/conjugation/einkaufen.htm"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Noun page (Haus)
# ---------------------------------------------------------------------------


def test_noun_fetch_returns_wordinfo(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = VerbformenProvider().fetch("Haus", source_language="de", target_language="en")

    assert info is not None
    assert info.word == "Haus"
    assert info.part_of_speech == "noun"
    assert info.source == "verbformen"
    assert info.source_language == "de"
    assert info.target_language == "en"
    assert info.gender == "das"
    assert info.pronunciation == "/haʊs/"
    assert info.definitions and "erbautes Gebäude" in info.definitions[0]
    assert info.examples == ["» Ich geh nach Hause. I'm going home."]


def test_noun_declension_table_parsed(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = VerbformenProvider().fetch("Haus", source_language="de", target_language="en")

    assert info.inflections == {
        "nom_sg": "das Haus",
        "gen_sg": "des Hauses",
        "dat_sg": "dem Haus",
        "acc_sg": "das Haus",
        "nom_pl": "die Häuser",
        "gen_pl": "der Häuser",
        "dat_pl": "den Häusern",
        "acc_pl": "die Häuser",
    }


def test_nested_span_translations_not_truncated(httpx_mock):
    # Regression: the site wraps rarer senses in nested <span> elements (e.g.
    # <span class="rInf">domicile</span>). A non-greedy regex stopped at the
    # first inner </span>, dropping every sense after it. BeautifulSoup reads
    # the whole cell, so the nested sense survives at the end of the list.
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = VerbformenProvider().fetch("Haus", source_language="de", target_language="en")

    assert info.translations[0] == "house"
    assert info.translations[-1] == "domicile"


# ---------------------------------------------------------------------------
# Verb page (einkaufen)
# ---------------------------------------------------------------------------


def test_verb_fetch_returns_wordinfo(httpx_mock):
    httpx_mock.add_response(url=VERB_URL, text=_fixture("verbformen_verb_einkaufen.html"))

    info = VerbformenProvider().fetch(
        "einkaufen", source_language="de", target_language="en"
    )

    assert info is not None
    assert info.word == "einkaufen"
    assert info.part_of_speech == "verb"
    assert info.separable is True
    assert info.pronunciation == "/ˈaɪ̯nˌkaʊ̯fən/"
    assert info.translations[0] == "buy"
    assert "do the shopping" in info.translations
    assert info.examples == ["» Wir kaufen ein. We are shopping."]


def test_verb_stammformen_parsed(httpx_mock):
    httpx_mock.add_response(url=VERB_URL, text=_fixture("verbformen_verb_einkaufen.html"))

    info = VerbformenProvider().fetch(
        "einkaufen", source_language="de", target_language="en"
    )

    assert info.inflections == {
        "present_3sg": "kauft ein",
        "preterite_3sg": "kaufte ein",
        "perfect": "hat eingekauft",
    }


# ---------------------------------------------------------------------------
# Language selection — translations are returned in the requested target
# language. The page carries ~50 languages; the provider must pick the one
# asked for and only that one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, expected_head",
    [
        ("en", ["house", "home"]),
        ("fr", ["maison", "coquille"]),
        ("es", ["casa", "hogar"]),
        ("ru", ["дом", "здание"]),
    ],
)
def test_noun_translations_follow_target_language(httpx_mock, target, expected_head):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = VerbformenProvider().fetch("Haus", source_language="de", target_language=target)

    assert info.translations[: len(expected_head)] == expected_head


@pytest.mark.parametrize(
    "target, expected_head",
    [
        ("en", ["buy", "shop"]),
        ("fr", ["acheter", "faire des courses"]),
        ("es", ["comprar", "adquirir"]),
        ("ru", ["покупать", "делать покупки"]),
    ],
)
def test_verb_translations_follow_target_language(httpx_mock, target, expected_head):
    httpx_mock.add_response(url=VERB_URL, text=_fixture("verbformen_verb_einkaufen.html"))

    info = VerbformenProvider().fetch(
        "einkaufen", source_language="de", target_language=target
    )

    assert info.translations[: len(expected_head)] == expected_head


def test_target_language_does_not_bleed_other_languages(httpx_mock):
    # Asking for French must not return any English (or other-language) word —
    # the selection is exact, not a fall-through to whatever is present.
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = VerbformenProvider().fetch("Haus", source_language="de", target_language="fr")

    assert "maison" in info.translations
    assert "house" not in info.translations
    assert "casa" not in info.translations


def test_unavailable_target_language_yields_no_translations(httpx_mock):
    # The German source page offers no German translation cell. The provider
    # returns an empty translation list (not the wrong language), while the
    # German definition still makes the note worth keeping.
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = VerbformenProvider().fetch("Haus", source_language="de", target_language="de")

    assert info is not None
    assert info.translations == []
    assert info.definitions  # the German definition is language-independent


# ---------------------------------------------------------------------------
# Routing and the network contract
# ---------------------------------------------------------------------------


def test_capitalisation_routes_to_noun_vs_verb_url(httpx_mock):
    # A capitalised lemma is a noun (declension), lowercase a verb (conjugation).
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))
    httpx_mock.add_response(url=VERB_URL, text=_fixture("verbformen_verb_einkaufen.html"))

    provider = VerbformenProvider()
    provider.fetch("Haus", source_language="de", target_language="en")
    provider.fetch("einkaufen", source_language="de", target_language="en")

    requested = {str(r.url) for r in httpx_mock.get_requests()}
    assert requested == {NOUN_URL, VERB_URL}


def test_404_is_a_clean_miss(httpx_mock):
    # No entry for the word: return None so the manager tries the next provider.
    url = "https://www.verbformen.com/declension/nouns/Quux.htm"
    httpx_mock.add_response(url=url, status_code=404)

    info = VerbformenProvider().fetch(
        "Quux", source_language="de", target_language="en"
    )

    assert info is None


def test_server_error_raises_provider_error(httpx_mock):
    url = "https://www.verbformen.com/declension/nouns/Quux.htm"
    httpx_mock.add_response(url=url, status_code=500)

    with pytest.raises(ProviderError):
        VerbformenProvider().fetch("Quux", source_language="de", target_language="en")


def test_page_without_steckbrief_is_a_miss(httpx_mock):
    # A 200 with no recognisable content yields neither translations nor
    # definitions, so the provider reports a miss rather than an empty note.
    url = "https://www.verbformen.com/declension/nouns/Leer.htm"
    httpx_mock.add_response(url=url, text="<html><body>nothing here</body></html>")

    info = VerbformenProvider().fetch(
        "Leer", source_language="de", target_language="en"
    )

    assert info is None


# ---------------------------------------------------------------------------
# Separable detection (bug #2: whole-token match, not substring)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attributes, expected",
    [
        ("A1 · regular · haben · separable", True),
        ("A2 · regular · haben · inseparable", False),
        ("A1 · regular · haben", None),
    ],
)
def test_separable_matches_whole_token(attributes, expected):
    # "inseparable" must not be read as containing "separable".
    soup = BeautifulSoup(
        f'<p>{attributes}</p><div id="vStckInf"></div>', "html.parser"
    )
    assert _separable(soup) is expected
