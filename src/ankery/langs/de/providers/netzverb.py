"""Netzverb scraper for the German pack.

One company publishes the same word data across sibling sites with isomorphic
markup (a one-letter CSS prefix tracks the host). For most parts of speech we
scrape the **lightweight "Steckbrief" pages** — a compact ~10 KB summary (lemma,
gender, audio, the key irregular forms, gloss, IPA, one example) instead of the
full ~150 KB declension page, since the rule-governed case/declension grids add
nothing the lemma plus a few stem-irregular forms do not. **Verbs are the
exception:** the per-person present paradigm is not rule-derivable, so the verb
route fetches the full ~180 KB conjugation page and parses its present-indicative
table (the Steckbrief panel it also embeds still supplies gloss, IPA, example,
and audio). This provider scrapes two hosts:

  verbformen.com (prefix "v") — inflected words (gender, comparison, the verb's
  conjugation), the verb on its full conjugation page, the rest on a
  /steckbrief/info/ path:
    noun       /declension/nouns/steckbrief/info/{Word}.htm   (capitalised)
    verb       /conjugation/{word}.htm                        (lowercase, full page)
    adjective  /declension/adjectives/steckbrief/info/{word}.htm
    pronoun    /declension/pronouns/steckbrief/info/{word}.htm
    article    /declension/articles/steckbrief/info/{word}.htm
  verben.de (prefix "w") — uninflected words, under a /steckbrief-info/ path:
    adverb       /adverbs/steckbrief-info/{word}.htm
    preposition  /prepositions/steckbrief-info/{word}.htm
    conjunction  /conjunctions/steckbrief-info/{word}.htm
    particle     /particles/steckbrief-info/{word}.htm

A `pos_hint` picks the route directly; without one only noun/verb are reachable,
chosen by capitalisation. A hint for any POS no site here serves misses cleanly.

Imports must be absolute — this file is loaded by path.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup, Tag

from ankery.models import WordInfo
from ankery.providers.retry import request_with_retry
from ankery.providers.base import ProviderError

_VERBFORMEN_BASE = "https://www.verbformen.com"
_VERBEN_BASE = "https://www.verben.de"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
# Footnote superscript digits the site appends to forms (e.g. ⁵ ⁶)
_SUPERSCRIPTS = str.maketrans("", "", "⁰¹²³⁴⁵⁶⁷⁸⁹")
# Each Steckbrief page carries a compact "Stammformen" line ("Hauses · Häuser")
# listing a POS's lexically irregular forms — the ones not derivable from the
# lemma by rule. This maps the line's middot-separated positions to feature keys
# per POS; None skips a position (the lemma itself). A POS absent here (verb,
# pronoun, article, and every uninflected verben.de word) reads nothing from the
# line. The full noun case grid and the adjective declension grid are
# deliberately not parsed: those endings are rule-governed, so only these
# stem-irregular forms are kept. Verbs are not here at all — they use the full
# conjugation page and read their forms from the present table and the
# principal-parts line instead (see _conjugation).
_KEY_FORMS = {
    "noun":      ("genitive_sg", "nominative_pl"),
    "adjective": (None, "comparative", "superlative"),
}
# Subject pronouns keying the present-indicative table's rows, in citation order;
# the table is the first one whose rows are exactly these six. Their forms fill
# the corresponding feature keys (the per-person present paradigm).
_PRONOUNS = ("ich", "du", "er", "wir", "ihr", "sie")
_PRESENT_KEYS = (
    "present_1sg", "present_2sg", "present_3sg",
    "present_1pl", "present_2pl", "present_3pl",
)
# Used only as a 404 fallback to strip "zu" from separable-verb zu-infinitives.
_SEPARABLE_PREFIXES = (
    "ab", "an", "auf", "aus", "bei", "ein", "empor", "entgegen", "fort", "vor",
    "weg", "zurück", "zusammen", "durch", "über", "unter", "wieder", "gegen",
    "hinter", "mit", "nach", "nieder", "statt", "um", "zu",
)


@dataclass(frozen=True)
class Source:
    """Where the parser finds each piece on one Netzverb site.

    The same data is published across sibling sites with a one-letter prefix
    that tracks the host (verbformen.com -> "v", verben.de -> "w"). `panel` and
    `key_forms` carry that prefix; `lemma` differs per site (verbformen has an
    article-bearing headword span; verben.de has only a bare headword block).
    Translations, IPA, gloss, and the example live inside the panel and are
    located uniformly, so they need no per-site selector.
    """

    panel: str       # id of the Steckbrief summary panel
    key_forms: str   # CSS selector for the compact Stammformen line whose parts
                     # are a POS's irregular forms (see _KEY_FORMS)
    lemma: str       # selector (relative to the panel) for the headword element;
                     # on verbformen it also bears the gender article and audio


# verbformen.com: inflected pages — article-bearing headword span (lemma +
# gender + audio in one node).
VERBFORMEN = Source(panel="vStckKrz", key_forms="p.vStm", lemma="span.vGrnd")

# verben.de: uninflected pages — no headword span; the lemma is the leading
# headword block, and there is no gender, audio, or Stammformen line.
VERBEN = Source(panel="wStckKrz", key_forms="p.wStm", lemma="div.rCntr.rClear")


def _normalize_verb_input(word: str) -> str:
    """Strip inserted "zu" from a separable zu-infinitive: umzusetzen -> umsetzen.
    Used as the verb route's 404 fallback (see _Route.fallback)."""
    for prefix in _SEPARABLE_PREFIXES:
        if word.startswith(prefix + "zu") and len(word) > len(prefix) + 2:
            return prefix + word[len(prefix) + 2 :]
    return word


@dataclass(frozen=True)
class _Route:
    """How to fetch and parse one part of speech: which host, the path template
    (`{word}` is percent-encoded in), the Source profile for that page, and an
    optional input normalizer retried once on a 404 (separable zu-infinitives)."""

    base: str
    path: str
    source: Source
    fallback: Callable[[str], str] | None = None


# POS -> route. The key set is exactly the parts of speech this provider can
# scrape; a hint outside it is a clean miss. The verb route carries a fallback
# for the zu-infinitive 404 retry; otherwise every POS flows through one parser.
_ROUTES = {
    "noun":        _Route(_VERBFORMEN_BASE, "/declension/nouns/steckbrief/info/{word}.htm", VERBFORMEN),
    "verb":        _Route(_VERBFORMEN_BASE, "/conjugation/{word}.htm", VERBFORMEN, fallback=_normalize_verb_input),
    "adjective":   _Route(_VERBFORMEN_BASE, "/declension/adjectives/steckbrief/info/{word}.htm", VERBFORMEN),
    "pronoun":     _Route(_VERBFORMEN_BASE, "/declension/pronouns/steckbrief/info/{word}.htm", VERBFORMEN),
    "article":     _Route(_VERBFORMEN_BASE, "/declension/articles/steckbrief/info/{word}.htm", VERBFORMEN),
    "adverb":      _Route(_VERBEN_BASE, "/adverbs/steckbrief-info/{word}.htm", VERBEN),
    "preposition": _Route(_VERBEN_BASE, "/prepositions/steckbrief-info/{word}.htm", VERBEN),
    "conjunction": _Route(_VERBEN_BASE, "/conjunctions/steckbrief-info/{word}.htm", VERBEN),
    "particle":    _Route(_VERBEN_BASE, "/particles/steckbrief-info/{word}.htm", VERBEN),
}


class NetzverbProvider:
    name = "netzverb"

    def __init__(self, *, timeout: float = 15.0, target_language: str = "en") -> None:
        self._target_language = target_language
        self._timeout = timeout

    def fetch(self, word: str, pos_hint: str | None = None) -> WordInfo | None:
        pos = self._resolve_pos(word, pos_hint)
        # A hint for a POS no site here serves (or any POS at all when none of
        # the no-hint priors apply): miss cleanly so the chain (the LLM) handles
        # it instead of scraping the wrong page shape.
        if pos is None:
            return None
        route = _ROUTES[pos]
        # Scope the client to one lookup so connections are pooled across the
        # requests it makes, then always closed — no leaked sockets.
        with httpx.Client(
            headers=_HEADERS, timeout=self._timeout, follow_redirects=True
        ) as client:
            lemma, html_text = self._load(client, route, word)
            if html_text is None:
                return None
            soup = BeautifulSoup(html_text, "html.parser")
            return _parse(lemma, soup, route, self._target_language, self.name, pos)

    def _load(
        self, client: httpx.Client, route: "_Route", word: str
    ) -> tuple[str, str | None]:
        # Fetch the word as given; on a 404 a route may normalize the input and
        # retry once (separable zu-infinitives: umzusetzen 404s, umsetzen resolves).
        # Returns the lemma actually fetched alongside the body (None body = miss).
        html = self._get(client, route, word)
        if html is None and route.fallback is not None:
            retry = route.fallback(word)
            if retry != word:
                return retry, self._get(client, route, retry)
        return word, html

    def _resolve_pos(self, word: str, pos_hint: str | None) -> str | None:
        # An explicit hint picks the route directly (None if no site serves it).
        if pos_hint is not None:
            return pos_hint if pos_hint in _ROUTES else None
        # Without a hint only noun/verb are reachable; German orthography is the
        # prior — capitalised words are nouns, the rest verbs.
        return "noun" if word[:1].isupper() else "verb"

    def _get(self, client: httpx.Client, route: "_Route", word: str) -> str | None:
        url = f"{route.base}{route.path.format(word=quote(word, safe=''))}"
        headers = {"Accept-Language": _accept_language(self._target_language)}
        try:
            # 429 (rate limited) is transient — the site is asking us to slow
            # down, not refusing the word; retry it before treating the response.
            r = request_with_retry(lambda: client.get(url, headers=headers))
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to {url} failed: {exc}") from exc
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ProviderError(f"{url} returned HTTP {r.status_code}")
        return r.text


def _build(config, pack) -> NetzverbProvider:
    options = pack.provider_options.get("netzverb", {})
    return NetzverbProvider(
        timeout=float(options.get("timeout", 15.0)),
        target_language=config.target_language,
    )


PROVIDERS = {"netzverb": _build}



@dataclass(frozen=True)
class _ParseCtx:
    """The page's shared anchors, located once and handed to the POS feature
    contributor so it can reach whatever it needs without re-finding them: the
    raw soup (the verb contributor scans it for the present-indicative table and
    principal-parts line), the Source profile, the Steckbrief panel, and the
    lemma element (its prefix middot gives separability)."""

    soup: BeautifulSoup
    src: Source
    steckbrief: Tag | None
    lemma_el: Tag | None


def _verb_extras(features: dict[str, str], ctx: _ParseCtx) -> None:
    """Verb-only features beyond the universal gender/IPA. The verb route fetches
    the full conjugation page, so _conjugation reads the whole present paradigm
    (present_1sg..present_3pl) from the present-indicative table plus preterite
    and perfect from the principal-parts line. The auxiliary is then derived from
    the perfect form and separability from the lemma's prefix middot."""
    features.update(_conjugation(ctx.soup))
    _put(features, "auxiliary", _auxiliary(features.get("perfect", "")))
    _put(features, "separable", _bool_str(_separable(ctx.lemma_el)))


# POS -> extra feature contributor beyond key_forms/gender/IPA. A POS absent here
# carries only those universal features. Contributors take the parse context, so
# new per-POS extraction (a conjugation table, a declension grid) is registered
# here rather than branched into the parser.
_FEATURE_EXTRAS: dict[str, Callable[[dict[str, str], _ParseCtx], None]] = {
    "verb": _verb_extras,
}


def _parse(
    word: str, soup: BeautifulSoup, route: "_Route", target_language: str,
    source: str, pos: str,
) -> WordInfo | None:
    """Parse one page into a WordInfo, for every POS — the Steckbrief summary
    panel is read identically whether it stands alone (most POS) or is embedded
    in the verb's full conjugation page. The POS-specific parts degrade away on
    their own: a POS with no _KEY_FORMS entry yields no irregular forms, a lemma
    with no leading article yields no gender, and a POS with no _FEATURE_EXTRAS
    contributor adds nothing further — so the same body serves an inflected noun,
    a verb, and a bare adverb without a branch."""
    src = route.source
    krz = soup.find(id=src.panel)
    steckbrief = _steckbrief_panel(krz)
    lemma_el = krz.select_one(src.lemma) if krz else None
    examples, example_translations = _example(steckbrief)

    features = _key_forms(soup, src, pos)
    _put(features, "gender", _gender(lemma_el))
    _put(features, "ipa", _pronunciation(steckbrief))
    extra = _FEATURE_EXTRAS.get(pos)
    if extra is not None:
        extra(features, _ParseCtx(soup, src, steckbrief, lemma_el))

    info = WordInfo(
        word=_lemma(lemma_el, fallback=word),
        part_of_speech=pos,
        translations=_translations(steckbrief, target_language),
        audio_url=_audio_url(lemma_el),
        definitions=_definition(steckbrief),
        examples=examples,
        example_translations=example_translations,
        features=features,
        source=source,
        source_language="de",
        target_language=target_language,
    )
    if not info.translations and not info.definitions:
        return None
    return info



def _accept_language(target_language: str | None) -> str:
    """Build Accept-Language header; target first, English as low-priority fallback."""
    lang = (target_language or "en").strip()
    if lang.lower().startswith("en"):
        return "en-US,en;q=0.9"
    return f"{lang},{lang};q=0.9,en;q=0.1"


def _normalize(text: str) -> str:
    """Collapse whitespace and strip footnote superscripts."""
    return re.sub(r"\s+", " ", text).strip().translate(_SUPERSCRIPTS)


def _put(features: dict[str, str], key: str, value: str | None) -> None:
    """Add a feature only when present; None and the empty string are dropped."""
    if value:
        features[key] = value


def _bool_str(value: bool | None) -> str | None:
    """Render a tri-state boolean feature: None stays absent, else "true"/"false".
    The result is a truthy string, so _put keeps "false" rather than dropping it."""
    return None if value is None else ("true" if value else "false")


def _one(text: str) -> list[str]:
    """[text] for a non-empty string, [] otherwise — the single-item list shape
    examples and translations use."""
    return [text] if text else []


def _steckbrief_panel(krz):
    """The inner content block holding translations, IPA, definition, and the
    example. verbformen wraps these in a collapsible onclick <div>; verben.de
    places them directly in the panel. Isolating the verbformen block keeps
    _definition from picking up the <i> endings inside the Stammformen line.
    None when the panel is absent."""
    if krz is None:
        return None
    inner = krz.find("div", onclick=True)
    return inner if inner is not None else krz



def _translations(steckbrief, lang: str) -> list[str]:
    # The negotiated language's gloss is the panel's single `span[lang="xx"]`
    # (the Steckbrief page carries only the Accept-Language-negotiated language,
    # unlike the full page's multi-language list). Nested sense <span>s have no
    # lang attribute, so reading the outer span keeps them.
    if steckbrief is None:
        return []
    span = steckbrief.select_one(f'span[lang="{lang}"]')
    if span is None:
        return []
    text = _normalize(span.get_text())
    return [t.strip() for t in text.split(",") if t.strip() and "..." not in t]


def _pronunciation(steckbrief) -> str | None:
    if steckbrief is None:
        return None
    # IPA paragraph starts with "/"; keep only the first middot-separated form.
    for p in steckbrief.find_all("p"):
        text = _normalize(p.get_text())
        if text.startswith("/"):
            return text.split("·")[0].strip()
    return None


def _definition(steckbrief) -> list[str]:
    if steckbrief is None:
        return []
    i = steckbrief.find("i")  # the lone <i> in the content block holds the definition
    if i is None:
        return []
    text = _normalize(i.get_text())
    return [text] if text else []


def _example(steckbrief) -> tuple[list[str], list[str]]:
    """Return ([german_sentence], [translation]); a flag <img> separates the two
    halves. No flag => the German half only; no example paragraph => ([], [])."""
    if steckbrief is None:
        return [], []
    p = next(
        (p for p in steckbrief.find_all("p")
         if _normalize(p.get_text()).startswith("»")),
        None,
    )
    if p is None:
        return [], []
    children = list(p.children)
    flag = p.find("img")
    if flag is None:
        return _one(_clean_example(_node_text(children))), []
    cut = children.index(flag)
    german = _clean_example(_node_text(children[:cut]))
    translation = _normalize(_node_text(children[cut + 1:]))
    return _one(german), _one(translation)


def _node_text(nodes) -> str:
    return "".join(n.get_text() if isinstance(n, Tag) else str(n) for n in nodes)


def _clean_example(text: str) -> str:
    # Rejoin detached period (" ." -> ".") the site renders as a separate sibling.
    text = re.sub(r"\s+\.", ".", _normalize(text))
    return text.lstrip("»").strip()


def _audio_url(lemma_el) -> str | None:
    # The pronunciation mp3 is a link inside the lemma element; sites without
    # one (lemma_el is None, or no child link) yield no audio.
    if lemma_el is None:
        return None
    a = lemma_el.select_one("a[href]")
    if a is None:
        return None
    href = a["href"]
    return href if href.endswith(".mp3") else None


def _lemma(lemma_el, *, fallback: str) -> str:
    """Canonical lemma from the lemma element; strips article and separable-verb
    middot. Sites without one (lemma_el is None) use the fallback. The stripping
    is a no-op on bare single-token lemmas, so it is safe to always apply."""
    if lemma_el is None:
        return fallback
    text = _normalize(lemma_el.get_text()).replace("·", "")
    first, _, rest = text.partition(" ")
    if first in ("der", "die", "das"):
        text = rest
    return text.strip() or fallback


def _gender(lemma_el) -> str | None:
    # Genus is the leading definite article of the lemma element; lemmas without
    # one (verbs, and every uninflected POS) yield no gender.
    if lemma_el is None:
        return None
    parts = _normalize(lemma_el.get_text()).split()
    if parts and parts[0] in ("der", "die", "das"):
        return parts[0]
    return None


def _separable(lemma_el) -> bool | None:
    """A separable verb's verbformen Grundform carries a middot at the prefix
    boundary (ein·kaufen); an inseparable one does not (verkaufen). None when the
    lemma element is absent."""
    if lemma_el is None:
        return None
    return "·" in lemma_el.get_text()


def _auxiliary(perfect: str) -> str | None:
    """The perfect-tense auxiliary, read off the perfect form's leading word
    ("hat eingekauft" -> haben, "ist gefahren" -> sein). None when the perfect
    form is missing or starts with neither auxiliary."""
    first = perfect.split()[0].lower() if perfect else ""
    if first in ("ist", "sind", "sein", "war"):
        return "sein"
    if first in ("hat", "haben", "habe", "hast", "habt", "hatte"):
        return "haben"
    return None


def _conjugation(soup: BeautifulSoup) -> dict[str, str]:
    """The verb's forms from the full conjugation page: the present-indicative
    paradigm (present_1sg..present_3pl) from the pronoun table, plus preterite and
    perfect from the principal-parts line ("kauft ein · kaufte ein · hat
    eingekauft"). The present 3sg the line also carries is already in the table,
    so only positions 2 and 3 are read; the leading audio link contributes no
    text."""
    result: dict[str, str] = {}
    for key, form in zip(_PRESENT_KEYS, _present_paradigm(soup)):
        _put(result, key, form)
    p = soup.find(id="stammformen")
    if p is not None:
        parts = [s.strip() for s in _normalize(p.get_text()).split("·") if s.strip()]
        if len(parts) >= 2:
            result["preterite"] = parts[1]
        if len(parts) >= 3:
            result["perfect"] = parts[2]
    return result


def _present_paradigm(soup: BeautifulSoup) -> list[str]:
    """The six present-indicative forms in _PRONOUNS order, from the first table
    whose rows are exactly the six subject pronouns (the language-keyed
    translation table above it is skipped, and subjunctive/preterite tables come
    after). Each row's remaining cells are joined into one form (the conjugated
    stem plus, for separable verbs, the split prefix: "kauft" + "ein"), and the
    site's parenthesised optional "-e" ("kauf(e)") is kept as the written form.
    [] when no such table is found."""
    for table in soup.find_all("table"):
        forms: dict[str, str] = {}
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            pronoun = _normalize(tds[0].get_text())
            if pronoun not in _PRONOUNS:
                forms = {}
                break
            text = _normalize(" ".join(td.get_text() for td in tds[1:]))
            forms[pronoun] = re.sub(r"[()]", "", text).strip()
        if set(forms) == set(_PRONOUNS):
            return [forms[p] for p in _PRONOUNS]
    return []


def _key_forms(soup: BeautifulSoup, src: Source, pos: str) -> dict[str, str]:
    """Map the Stammformen line's parts to feature keys for this POS (see
    _KEY_FORMS). "-" is the site's marker for a form the word lacks (no plural,
    no comparison) and is skipped, as is any position whose key is None."""
    keys = _KEY_FORMS.get(pos)
    if keys is None:
        return {}
    p = soup.select_one(src.key_forms)
    if p is None:
        return {}
    parts = [s.strip() for s in _normalize(p.get_text()).split("·")]
    return {
        key: part
        for key, part in zip(keys, parts)
        if key and part and part != "-"
    }
