"""The German pack's verbformen provider (langs/de/providers/verbformen.py).

The provider is pack-local code loaded by path, so the test loads the module the
same way the pack loader does and exercises the class and helpers directly. Its
output goes into the language-neutral `features` dict (gender, ipa, declension,
conjugation) rather than dedicated German fields.
"""

import importlib.util
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import ankery
from ankery.providers.base import ProviderError

FIXTURES = Path(__file__).parent / "fixtures"
NOUN_URL = "https://www.verbformen.com/declension/nouns/Haus.htm"
VERB_URL = "https://www.verbformen.com/conjugation/einkaufen.htm"

_PROVIDER_PATH = (
    Path(ankery.__file__).parent / "langs" / "de" / "providers" / "verbformen.py"
)
_spec = importlib.util.spec_from_file_location("de_verbformen_under_test", _PROVIDER_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

VerbformenProvider = _mod.VerbformenProvider
_accept_language = _mod._accept_language
_normalize_verb_input = _mod._normalize_verb_input
_separable = _mod._separable


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _provider(target_language: str = "en") -> "VerbformenProvider":
    return VerbformenProvider(target_language=target_language)


# ---------------------------------------------------------------------------
# Noun page (Haus)
# ---------------------------------------------------------------------------


def test_noun_fetch_returns_wordinfo(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider().fetch("Haus")

    assert info is not None
    assert info.word == "Haus"
    assert info.part_of_speech == "noun"
    assert info.source == "verbformen"
    assert info.source_language == "de"
    assert info.target_language == "en"
    assert info.features["gender"] == "das"
    assert info.features["ipa"] == "/haʊs/"
    assert info.definitions and "erbautes Gebäude" in info.definitions[0]
    assert info.examples == ["Ich geh nach Hause."]
    assert info.example_translations == ["I'm going home."]
    assert (
        info.audio_url
        == "https://www.verbformen.de/deklination/substantive/grundform/der_Haus.mp3"
    )


def test_word_taken_from_page_headword_not_request(httpx_mock):
    # verbformen resolves a misspelled/umlaut-stripped request to the right page;
    # `word` must be the lemma the page displays, not the requested spelling.
    url = "https://www.verbformen.com/declension/nouns/Hause.htm"
    httpx_mock.add_response(url=url, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider().fetch("Hause")

    assert info is not None
    assert info.word == "Haus"


def test_noun_declension_goes_into_features(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider().fetch("Haus")

    # Declension forms are stored bare (the article cell is never read) under the
    # case_number keys the pack declares, alongside gender/ipa in the same dict.
    declension = {
        "nominative_sg": "Haus",
        "genitive_sg": "Hauses",
        "dative_sg": "Haus",
        "accusative_sg": "Haus",
        "nominative_pl": "Häuser",
        "genitive_pl": "Häuser",
        "dative_pl": "Häusern",
        "accusative_pl": "Häuser",
    }
    assert declension.items() <= info.features.items()
    assert info.features["gender"] == "das"


def test_nested_span_translations_not_truncated(httpx_mock):
    # Regression: a non-greedy regex stopped at the first inner </span>, dropping
    # nested senses. BeautifulSoup reads the whole cell, so the nested sense
    # survives at the end of the list.
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider().fetch("Haus")

    assert info.translations[0] == "house"
    assert info.translations[-1] == "domicile"


# ---------------------------------------------------------------------------
# Verb page (einkaufen)
# ---------------------------------------------------------------------------


def test_verb_fetch_returns_wordinfo(httpx_mock):
    httpx_mock.add_response(url=VERB_URL, text=_fixture("verbformen_verb_einkaufen.html"))

    info = _provider().fetch("einkaufen")

    assert info is not None
    assert info.word == "einkaufen"
    assert info.part_of_speech == "verb"
    assert info.features["separable"] == "true"
    assert info.features["ipa"] == "/ˈaɪ̯nˌkaʊ̯fən/"
    assert info.translations[0] == "buy"
    assert "do the shopping" in info.translations
    assert info.examples == ["Wir kaufen ein."]
    assert info.example_translations == ["We are shopping."]
    assert (
        info.audio_url
        == "https://www.verbformen.de/konjugation/infinitiv/einkaufen.mp3"
    )


def test_verb_conjugation_goes_into_features(httpx_mock):
    httpx_mock.add_response(url=VERB_URL, text=_fixture("verbformen_verb_einkaufen.html"))

    info = _provider().fetch("einkaufen")

    conjugation = {
        "present_1sg": "kaufe ein",
        "present_2sg": "kaufst ein",
        "present_3sg": "kauft ein",
        "present_1pl": "kaufen ein",
        "present_2pl": "kauft ein",
        "present_3pl": "kaufen ein",
        "preterite": "kaufte ein",
        "perfect": "hat eingekauft",
        "auxiliary": "haben",
    }
    assert conjugation.items() <= info.features.items()


# ---------------------------------------------------------------------------
# Language selection — translations follow the requested target language.
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

    info = _provider(target).fetch("Haus")

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

    info = _provider(target).fetch("einkaufen")

    assert info.translations[: len(expected_head)] == expected_head


def test_target_language_does_not_bleed_other_languages(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider("fr").fetch("Haus")

    assert "maison" in info.translations
    assert "house" not in info.translations
    assert "casa" not in info.translations


def test_unavailable_target_language_yields_no_translations(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider("de").fetch("Haus")

    assert info is not None
    assert info.translations == []
    assert info.definitions  # the German definition is language-independent


# ---------------------------------------------------------------------------
# Example gloss language — Accept-Language steers the example's translation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, expected_header",
    [
        ("en", "en-US,en;q=0.9"),
        ("fr", "fr,fr;q=0.9,en;q=0.1"),
        ("ru", "ru,ru;q=0.9,en;q=0.1"),
        (None, "en-US,en;q=0.9"),
    ],
)
def test_accept_language_prefers_target_with_english_fallback(target, expected_header):
    assert _accept_language(target) == expected_header


def test_request_carries_target_language_header(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    _provider("fr").fetch("Haus")

    request = httpx_mock.get_requests()[0]
    assert request.headers["Accept-Language"] == "fr,fr;q=0.9,en;q=0.1"


# ---------------------------------------------------------------------------
# Routing and the network contract
# ---------------------------------------------------------------------------


def test_capitalisation_routes_to_noun_vs_verb_url(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))
    httpx_mock.add_response(url=VERB_URL, text=_fixture("verbformen_verb_einkaufen.html"))

    provider = _provider()
    provider.fetch("Haus")
    provider.fetch("einkaufen")

    requested = {str(r.url) for r in httpx_mock.get_requests()}
    assert requested == {NOUN_URL, VERB_URL}


def test_word_with_special_chars_is_percent_encoded_in_url(httpx_mock):
    # A slash in the word must not alter the URL path; it is percent-encoded.
    url = "https://www.verbformen.com/declension/nouns/A%2FB.htm"
    httpx_mock.add_response(url=url, status_code=404)

    assert _provider().fetch("A/B") is None
    assert str(httpx_mock.get_requests()[0].url) == url


def test_404_is_a_clean_miss(httpx_mock):
    url = "https://www.verbformen.com/declension/nouns/Quux.htm"
    httpx_mock.add_response(url=url, status_code=404)

    assert _provider().fetch("Quux") is None


def test_server_error_raises_provider_error(httpx_mock):
    url = "https://www.verbformen.com/declension/nouns/Quux.htm"
    httpx_mock.add_response(url=url, status_code=500)

    with pytest.raises(ProviderError):
        _provider().fetch("Quux")


def test_page_without_steckbrief_is_a_miss(httpx_mock):
    url = "https://www.verbformen.com/declension/nouns/Leer.htm"
    httpx_mock.add_response(url=url, text="<html><body>nothing here</body></html>")

    assert _provider().fetch("Leer") is None


# ---------------------------------------------------------------------------
# Separable detection (whole-token match, not substring)
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
    soup = BeautifulSoup(
        f'<p>{attributes}</p><div id="vStckInf"></div>', "html.parser"
    )
    assert _separable(soup) is expected


# ---------------------------------------------------------------------------
# zu-infinitive normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given, expected",
    [
        ("umzusetzen", "umsetzen"),
        ("anzufangen", "anfangen"),
        ("zusammenzusetzen", "zusammensetzen"),
        ("umsetzen", "umsetzen"),
        ("einkaufen", "einkaufen"),
        ("zumuten", "zumuten"),
    ],
)
def test_normalize_verb_input(given, expected):
    assert _normalize_verb_input(given) == expected


def test_zu_infinitive_retries_base_form(httpx_mock):
    httpx_mock.add_response(
        url="https://www.verbformen.com/conjugation/einzukaufen.htm", status_code=404
    )
    httpx_mock.add_response(url=VERB_URL, text=_fixture("verbformen_verb_einkaufen.html"))

    info = _provider().fetch("einzukaufen")

    assert info is not None
    assert info.word == "einkaufen"


def test_non_zu_miss_does_not_retry(httpx_mock):
    url = "https://www.verbformen.com/conjugation/quux.htm"
    httpx_mock.add_response(url=url, status_code=404)

    info = _provider().fetch("quux")

    assert info is None
    assert len(httpx_mock.get_requests()) == 1
