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
NOUN_URL = "https://www.verbformen.com/declension/nouns/steckbrief/info/Haus.htm"
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
_auxiliary = _mod._auxiliary


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
    url = "https://www.verbformen.com/declension/nouns/steckbrief/info/Hause.htm"
    httpx_mock.add_response(url=url, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider().fetch("Hause")

    assert info is not None
    assert info.word == "Haus"


def test_noun_key_forms_go_into_features(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider().fetch("Haus")

    # Only the lexically irregular forms are kept (genitive_sg, nominative_pl),
    # read bare from the compact "Hauses · Häuser" line alongside gender/ipa. The
    # rule-derivable cases (dative, accusative, the plural genitive/accusative)
    # are deliberately not parsed.
    assert info.features["genitive_sg"] == "Hauses"
    assert info.features["nominative_pl"] == "Häuser"
    assert info.features["gender"] == "das"
    assert "dative_pl" not in info.features
    assert "accusative_sg" not in info.features


def test_nested_span_translations_not_truncated(httpx_mock):
    # The gloss span wraps later senses in nested <span> tags (… domicile,
    # legislative body). Reading the outer span's full text keeps them, rather
    # than stopping at the first inner </span>.
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider().fetch("Haus")

    assert info.translations[0] == "house"
    assert "domicile" in info.translations


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
    # The full conjugation page carries the whole present-indicative paradigm in
    # a pronoun table; preterite and perfect come from the principal-parts line,
    # the auxiliary is read off the perfect form, and the separable prefix split
    # ("kauft" + "ein") is preserved per form.
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


# The Steckbrief page carries only the Accept-Language-negotiated gloss, so each
# target language is served from its own fixture (fetched with that header).
@pytest.mark.parametrize(
    "target, fixture, expected_head",
    [
        ("en", "verbformen_noun_Haus.html", ["house", "home"]),
        ("fr", "verbformen_noun_Haus_fr.html", ["maison", "coquille"]),
    ],
)
def test_noun_translations_follow_target_language(httpx_mock, target, fixture, expected_head):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture(fixture))

    info = _provider(target).fetch("Haus")

    assert info.translations[: len(expected_head)] == expected_head


@pytest.mark.parametrize(
    "target, fixture, expected_head",
    [
        ("en", "verbformen_verb_einkaufen.html", ["buy", "shop"]),
        ("fr", "verbformen_verb_einkaufen_fr.html", ["acheter", "faire des courses"]),
    ],
)
def test_verb_translations_follow_target_language(httpx_mock, target, fixture, expected_head):
    httpx_mock.add_response(url=VERB_URL, text=_fixture(fixture))

    info = _provider(target).fetch("einkaufen")

    assert info.translations[: len(expected_head)] == expected_head


def test_target_language_does_not_bleed_other_languages(httpx_mock):
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus_fr.html"))

    info = _provider("fr").fetch("Haus")

    assert "maison" in info.translations
    assert "house" not in info.translations
    assert "casa" not in info.translations


def test_unavailable_target_language_yields_no_translations(httpx_mock):
    # With German as the target the provider's gloss filter finds no foreign
    # translations to pick (German is the source), so translations come back
    # empty — but the German definition still stands.
    httpx_mock.add_response(url=NOUN_URL, text=_fixture("verbformen_noun_Haus.html"))

    info = _provider("de").fetch("Haus")

    assert info is not None
    assert info.translations == []
    assert info.definitions and "erbautes Gebäude" in info.definitions[0]


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
    url = "https://www.verbformen.com/declension/nouns/steckbrief/info/A%2FB.htm"
    httpx_mock.add_response(url=url, status_code=404)

    assert _provider().fetch("A/B") is None
    assert str(httpx_mock.get_requests()[0].url) == url


def test_404_is_a_clean_miss(httpx_mock):
    url = "https://www.verbformen.com/declension/nouns/steckbrief/info/Quux.htm"
    httpx_mock.add_response(url=url, status_code=404)

    assert _provider().fetch("Quux") is None


def test_server_error_raises_provider_error(httpx_mock):
    url = "https://www.verbformen.com/declension/nouns/steckbrief/info/Quux.htm"
    httpx_mock.add_response(url=url, status_code=500)

    with pytest.raises(ProviderError):
        _provider().fetch("Quux")


def test_page_without_steckbrief_is_a_miss(httpx_mock):
    url = "https://www.verbformen.com/declension/nouns/steckbrief/info/Leer.htm"
    httpx_mock.add_response(url=url, text="<html><body>nothing here</body></html>")

    assert _provider().fetch("Leer") is None


# ---------------------------------------------------------------------------
# Separable detection (whole-token match, not substring)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lemma_html, expected",
    [
        ('<span class="vGrnd">ein·kaufen</span>', True),   # middot marks the prefix
        ('<span class="vGrnd">verkaufen</span>', False),   # inseparable, no middot
    ],
)
def test_separable_detected_from_grundform_middot(lemma_html, expected):
    soup = BeautifulSoup(lemma_html, "html.parser")
    assert _separable(soup.select_one("span.vGrnd")) is expected


def test_separable_is_none_without_a_lemma_element():
    assert _separable(None) is None


@pytest.mark.parametrize(
    "perfect, expected",
    [
        ("hat eingekauft", "haben"),
        ("ist gefahren", "sein"),
        ("", None),
    ],
)
def test_auxiliary_read_from_perfect_form(perfect, expected):
    assert _auxiliary(perfect) == expected


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
        url="https://www.verbformen.com/conjugation/einzukaufen.htm",
        status_code=404,
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


# ---------------------------------------------------------------------------
# Explicit part-of-speech hint
# ---------------------------------------------------------------------------


def test_pos_hint_for_unscrapable_pos_misses_without_a_request(httpx_mock):
    # The two Netzverb sites serve a fixed set of parts of speech. Given a hint
    # for one outside that set (interjection has no page on either site) the
    # provider must miss cleanly so the chain falls through to the LLM — and it
    # must not waste an HTTP request guessing. (No mocked response is added; a
    # request here would make pytest-httpx fail.)
    assert _provider().fetch("hallo", pos_hint="interjection") is None
    assert httpx_mock.get_requests() == []


def test_noun_hint_forces_the_noun_page_for_a_lowercase_word(httpx_mock):
    # Without a hint a lowercase word would be treated as a verb; the noun hint
    # overrides the capitalisation prior and hits the declension page.
    httpx_mock.add_response(
        url="https://www.verbformen.com/declension/nouns/steckbrief/info/haus.htm",
        text=_fixture("verbformen_noun_Haus.html"),
    )

    info = _provider().fetch("haus", pos_hint="noun")

    assert info is not None
    assert info.part_of_speech == "noun"


def test_verb_hint_forces_the_conjugation_page_for_a_capitalised_word(httpx_mock):
    httpx_mock.add_response(
        url="https://www.verbformen.com/conjugation/Einkaufen.htm",
        text=_fixture("verbformen_verb_einkaufen.html"),
    )

    info = _provider().fetch("Einkaufen", pos_hint="verb")

    assert info is not None
    assert info.part_of_speech == "verb"


# ---------------------------------------------------------------------------
# Other parts of speech: verbformen.com declinables and verben.de uninflected
# ---------------------------------------------------------------------------


def test_adjective_keeps_only_comparison_forms(httpx_mock):
    # The lexically irregular forms (comparative, superlative) come from the
    # "hoch · höher · am höchsten" line; the rule-governed declension grid is
    # not parsed, and the positive (the lemma) is skipped.
    httpx_mock.add_response(
        url="https://www.verbformen.com/declension/adjectives/steckbrief/info/hoch.htm",
        text=_fixture("verbformen_adjective_hoch.html"),
    )

    info = _provider().fetch("hoch", pos_hint="adjective")

    assert info is not None
    assert info.word == "hoch"
    assert info.part_of_speech == "adjective"
    assert info.features["comparative"] == "höher"
    assert info.features["superlative"] == "am höchsten"
    assert info.audio_url and info.audio_url.endswith(".mp3")


def test_pronoun_and_article_carry_no_inflection_features(httpx_mock):
    # Their declension is a closed paradigm we deliberately do not scrape; the
    # card gets gloss-level data (translations/definition) and nothing keyed off
    # the Stammformen line, which for these POS has no _KEY_FORMS entry.
    httpx_mock.add_response(
        url="https://www.verbformen.com/declension/pronouns/steckbrief/info/er.htm",
        text=_fixture("verbformen_pronoun_er.html"),
    )
    httpx_mock.add_response(
        url="https://www.verbformen.com/declension/articles/steckbrief/info/ein.htm",
        text=_fixture("verbformen_article_ein.html"),
    )

    pronoun = _provider().fetch("er", pos_hint="pronoun")
    article = _provider().fetch("ein", pos_hint="article")

    assert pronoun is not None and pronoun.part_of_speech == "pronoun"
    assert article is not None and article.part_of_speech == "article"
    for info in (pronoun, article):
        assert set(info.features) <= {"ipa"}  # gloss-level only, no inflection


def test_verben_de_uninflected_pos_route_to_verben_host(httpx_mock):
    # adverb/preposition/conjunction/particle live on the sibling site verben.de
    # and have no inflection tables; the lemma is taken from the page.
    httpx_mock.add_response(
        url="https://www.verben.de/prepositions/steckbrief-info/mit.htm",
        text=_fixture("verben_preposition_mit.html"),
    )

    info = _provider().fetch("mit", pos_hint="preposition")

    assert info is not None
    assert info.word == "mit"
    assert info.part_of_speech == "preposition"
    assert "with" in info.translations
    assert info.audio_url is None  # verben.de pages carry no headword audio
    assert str(httpx_mock.get_requests()[0].url) == "https://www.verben.de/prepositions/steckbrief-info/mit.htm"
