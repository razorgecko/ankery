"""verbformen.com scraper — the German pack's own provider.

A language-specific provider lives in its pack, not the engine: it is named in
lang.toml's chain and resolved here. The engine's registry holds only
cross-language providers (the LLM). The pack exposes its builders through the
module-level `PROVIDERS` dict, which the loader merges over the engine registry.

Fetches the compact steckbrief (summary panel) from one URL per word:
  - Nouns (capital first letter): /declension/nouns/{Word}.htm
  - Verbs (lowercase):            /conjugation/{word}.htm

Parsing uses BeautifulSoup for element selection — the steckbrief and the
declension tables carry stable id/class anchors — with light regex only for
*text* normalization (whitespace, footnote superscripts). get_text() reads a
translation cell whole, nested sense-spans included, where a non-greedy
`(.*?)</span>` regex would truncate at the first inner close tag.

Imports are absolute: the engine loads this file by path, so a relative import
has no package to resolve against.
"""

import re

import httpx
from bs4 import BeautifulSoup, Tag

from ankery.models import WordInfo
from ankery.providers.base import ProviderError

_BASE = "https://www.verbformen.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
# Footnote superscript digits the site appends to forms (e.g. ⁵ ⁶)
_SUPERSCRIPTS = str.maketrans("", "", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_CASES = {
    "Nominative": "nominative",
    "Genitive": "genitive",
    "Dative": "dative",
    "Accusative": "accusative",
}
# Subject pronouns keying a conjugation table's rows, in citation order. The
# present-indicative table is the first table whose rows are exactly these.
_PRONOUNS = ("ich", "du", "er", "wir", "ihr", "sie")
_PRESENT_KEYS = (
    "present_1sg", "present_2sg", "present_3sg",
    "present_1pl", "present_2pl", "present_3pl",
)
# Separable prefixes a "zu"-infinitive inserts "zu" after (umzusetzen ->
# umsetzen). Used only as a 404 fallback, so a real lemma that merely looks like
# prefix+zu is tried literally first and never rewritten out from under itself.
_SEPARABLE_PREFIXES = (
    "ab", "an", "auf", "aus", "bei", "ein", "empor", "entgegen", "fort", "vor",
    "weg", "zurück", "zusammen", "durch", "über", "unter", "wieder", "gegen",
    "hinter", "mit", "nach", "nieder", "statt", "um", "zu",
)


class VerbformenProvider:
    name = "verbformen"

    def __init__(self, *, timeout: float = 15.0, target_language: str = "en") -> None:
        # Source language is always German for this provider; target steers the
        # gloss language requested via Accept-Language (see _get).
        self._target_language = target_language
        self._client = httpx.Client(
            headers=_HEADERS, timeout=timeout, follow_redirects=True
        )

    def fetch(self, word: str) -> WordInfo | None:
        target = self._target_language
        # German nouns are always capitalised; verbs/adjectives start lowercase.
        if word[0].isupper():
            html_text = self._get(f"/declension/nouns/{word}.htm")
            if html_text is None:
                return None
            soup = BeautifulSoup(html_text, "html.parser")
            return _parse_noun(word, soup, target, self.name)
        else:
            lemma = word
            html_text = self._get(f"/conjugation/{word}.htm")
            if html_text is None:
                # The literal form missed; if it looks like a "zu"-infinitive
                # (umzusetzen) retry the base infinitive (umsetzen).
                normalized = _normalize_verb_input(word)
                if normalized != word:
                    html_text = self._get(f"/conjugation/{normalized}.htm")
                    lemma = normalized
            if html_text is None:
                return None
            soup = BeautifulSoup(html_text, "html.parser")
            return _parse_verb(lemma, soup, target, self.name)

    def _get(self, path: str) -> str | None:
        url = f"{_BASE}{path}"
        # The steckbrief example's gloss is rendered in whatever language the
        # Accept-Language header requests (the German sentence is unchanged), so
        # set it per request to get the example in the target language rather
        # than English. The translation list (dl.vNrn) carries every language
        # regardless, so this only steers the example/primary-span language.
        headers = {"Accept-Language": _accept_language(self._target_language)}
        try:
            r = self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"verbformen.com request failed: {exc}") from exc
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ProviderError(
                f"verbformen.com returned HTTP {r.status_code} for {url}"
            )
        return r.text


# Pack provider builders: name -> (config, pack) -> provider. The loader merges
# this over the engine's cross-language registry, so "verbformen" in a chain
# resolves here. Reads its own options from lang.toml [provider_options.*].
def _build(config, pack) -> VerbformenProvider:
    options = pack.provider_options.get("verbformen", {})
    return VerbformenProvider(
        timeout=float(options.get("timeout", 15.0)),
        target_language=config.target_language,
    )


PROVIDERS = {"verbformen": _build}


# ---------------------------------------------------------------------------
# Top-level parsers
# ---------------------------------------------------------------------------


def _parse_noun(
    word: str, soup: BeautifulSoup, target_language: str, source: str
) -> WordInfo | None:
    krz = soup.find(id="vStckKrz")
    steckbrief = _steckbrief_panel(krz)
    examples, example_translations = _example(steckbrief)
    features = _declension_tables(soup)
    gender = _gender(krz)
    if gender:
        features["gender"] = gender
    ipa = _pronunciation(steckbrief)
    if ipa:
        features["ipa"] = ipa
    info = WordInfo(
        word=_headword(krz, fallback=word),
        part_of_speech="noun",
        translations=_translations(soup, target_language),
        audio_url=_audio_url(krz),
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


def _parse_verb(
    word: str, soup: BeautifulSoup, target_language: str, source: str
) -> WordInfo | None:
    krz = soup.find(id="vStckKrz")
    steckbrief = _steckbrief_panel(krz)
    examples, example_translations = _example(steckbrief)
    features = _conjugation(soup)
    separable = _separable(soup)
    if separable is not None:
        features["separable"] = "true" if separable else "false"
    ipa = _pronunciation(steckbrief)
    if ipa:
        features["ipa"] = ipa
    info = WordInfo(
        word=_headword(krz, fallback=word),
        part_of_speech="verb",
        translations=_translations(soup, target_language),
        audio_url=_audio_url(krz),
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


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _accept_language(target_language: str | None) -> str:
    """Accept-Language value that selects the gloss language for the example.

    The target tag goes first; English trails as a low-priority fallback so a
    word with no gloss in the target still yields an English example rather than
    none. An absent target degrades to plain English.
    """
    lang = (target_language or "en").strip()
    if lang.lower().startswith("en"):
        return "en-US,en;q=0.9"
    return f"{lang},{lang};q=0.9,en;q=0.1"


def _normalize(text: str) -> str:
    """Collapse whitespace and strip the footnote superscripts the site appends.

    BeautifulSoup already unescapes entities and discards markup; this only
    tidies the *text* the elements carried.
    """
    return re.sub(r"\s+", " ", text).strip().translate(_SUPERSCRIPTS)


def _steckbrief_panel(krz):
    """The inner summary panel holding IPA, definition and example.

    It is the div wrapping the translation span (translations themselves are
    read separately from the page's translation list); fall back to the whole
    steckbrief if the structure differs. Returns None when there is no
    steckbrief at all so the field extractors degrade to empty.
    """
    if krz is None:
        return None
    span = krz.select_one("span[lang]")
    if span is not None:
        p = span.find_parent("p")
        if p is not None:
            return p.parent
    return krz


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------


def _translations(soup: BeautifulSoup, lang: str) -> list[str]:
    # Every language the site offers lives in the "Translations" list as a
    # <dd lang="xx"> (one per language, page-unique), inside a dl.vNrn. The
    # compact steckbrief only renders the primary language, so read from the
    # list to honour an arbitrary target. An absent language yields nothing
    # rather than silently returning the wrong language's words.
    dd = soup.select_one(f'dl.vNrn dd[lang="{lang}"]')
    if dd is None:
        return []
    # get_text() reads the whole cell, nested sense-spans included — the case a
    # non-greedy regex would truncate at the first inner </span>.
    text = _normalize(dd.get_text())
    return [t.strip() for t in text.split(",") if t.strip() and "..." not in t]


def _pronunciation(steckbrief) -> str | None:
    if steckbrief is None:
        return None
    # The IPA paragraph is the one whose text starts with "/"; several
    # middot-separated forms may follow, keep only the first (base form).
    for p in steckbrief.find_all("p"):
        text = _normalize(p.get_text())
        if text.startswith("/"):
            return text.split("·")[0].strip()
    return None


def _definition(steckbrief) -> list[str]:
    if steckbrief is None:
        return []
    # The German definition is the lone <i> in the panel (translations and the
    # example carry no <i>).
    i = steckbrief.find("i")
    if i is None:
        return []
    text = _normalize(i.get_text())
    return [text] if text else []


def _example(steckbrief) -> tuple[list[str], list[str]]:
    """Return ([german_sentence], [translation]) for the example, if present.

    The example paragraph starts with » and carries the German sentence and its
    gloss in one <p>, divided by a flag <img> (its alt names the target
    language). Splitting on that image keeps the sentence and its translation in
    separate fields instead of mashing them into one string. Each list holds at
    most one item and is empty when its half is missing (no example, or a
    sentence with no gloss).
    """
    if steckbrief is None:
        return [], []
    for p in steckbrief.find_all("p"):
        if not _normalize(p.get_text()).startswith("»"):
            continue
        flag = p.find("img")
        if flag is None:
            german = _clean_example(_node_text(p.children))
            return ([german] if german else []), []
        before, after, seen = [], [], False
        for child in p.children:
            if child is flag:
                seen = True
                continue
            (after if seen else before).append(child)
        german = _clean_example(_node_text(before))
        translation = _normalize(_node_text(after))
        return ([german] if german else []), ([translation] if translation else [])
    return [], []


def _node_text(nodes) -> str:
    """Concatenate the text of a run of mixed Tag/NavigableString siblings."""
    return "".join(n.get_text() if isinstance(n, Tag) else str(n) for n in nodes)


def _clean_example(text: str) -> str:
    # Drop the leading » marker and rejoin the period the site renders as a
    # separate sibling of the highlighted word (" ." -> ".").
    text = re.sub(r"\s+\.", ".", _normalize(text))
    return text.lstrip("»").strip()


def _audio_url(krz) -> str | None:
    # The lemma's pronunciation audio is the <a> inside the headword span
    # (span.vGrnd) for both nouns and verbs; its href is an absolute .mp3 URL.
    if krz is None:
        return None
    a = krz.select_one("span.vGrnd a[href]")
    if a is None:
        return None
    href = a["href"]
    return href if href.endswith(".mp3") else None


def _normalize_verb_input(word: str) -> str:
    """Strip the inserted "zu" from a separable-verb zu-infinitive.

    umzusetzen -> umsetzen, anzufangen -> anfangen. Returns the word unchanged
    when it is not a recognisable prefix+zu form, so callers can compare and
    only retry when it actually rewrote something.
    """
    for prefix in _SEPARABLE_PREFIXES:
        if word.startswith(prefix + "zu") and len(word) > len(prefix) + 2:
            return prefix + word[len(prefix) + 2 :]
    return word


def _headword(krz, *, fallback: str) -> str:
    """The canonical lemma the page is actually about, read from the headword
    span (span.vGrnd).

    verbformen resolves a misspelled or umlaut-stripped request to the right
    page ("Ol" -> the "Öl" page), so reading the lemma the page displays gives
    the corrected form, where echoing the requested `word` would keep the typo.
    The span carries the noun's article ("das Öl") and a middot at a separable
    verb's seam ("ein·kaufen"); strip both for the bare lemma. Falls back to the
    requested word when the span is absent or empty.
    """
    if krz is None:
        return fallback
    span = krz.select_one("span.vGrnd")
    if span is None:
        return fallback
    text = _normalize(span.get_text()).replace("·", "")
    head, _, rest = text.partition(" ")
    if head in ("der", "die", "das"):
        text = rest
    return text.strip() or fallback


def _gender(krz) -> str | None:
    # vGrnd holds the article + lemma: "das Haus" / "der Mann".
    if krz is None:
        return None
    span = krz.select_one("span.vGrnd")
    if span is None:
        return None
    parts = _normalize(span.get_text()).split()
    if parts and parts[0] in ("der", "die", "das"):
        return parts[0]
    return None


def _separable(soup: BeautifulSoup) -> bool | None:
    # The attribute line ("A1 · regular · haben · separable") is the paragraph
    # just before the conjugation block. Split on the middot and match a whole
    # token so "inseparable" is not read as containing "separable".
    div = soup.find(id="vStckInf")
    if div is None:
        return None
    p = div.find_previous_sibling("p")
    if p is None:
        return None
    tokens = {t.strip().lower() for t in p.get_text().split("·")}
    if "separable" in tokens:
        return True
    if "inseparable" in tokens:
        return False
    return None


def _conjugation(soup: BeautifulSoup) -> dict[str, str]:
    """Verb forms keyed by the conjugation vocabulary the de pack declares.

    Fills the same keys the note definitions and the LLM provider speak — the
    full present paradigm (`present_1sg` .. `present_3pl`) plus `preterite`,
    `perfect` and `auxiliary` — so a verbformen note carries the same fields an
    LLM note would. The present paradigm comes from the indicative present table
    (six rows); `preterite`/`perfect` from the principal-parts line; `auxiliary`
    from the attribute line shared with `_separable`.
    """
    result: dict[str, str] = {}

    for key, form in zip(_PRESENT_KEYS, _present_paradigm(soup)):
        if form:
            result[key] = form

    # Principal parts: "kauft ein · kaufte ein · hat eingekauft" -> the
    # preterite (1st/3rd sg, identical in German) and the perfect. The present
    # 3sg it also carries is already covered by the table above. The trailing
    # audio link holds only an <img>, so it contributes no text.
    p = soup.find(id="stammformen")
    if p is not None:
        parts = [s.strip() for s in _normalize(p.get_text()).split("·") if s.strip()]
        if len(parts) >= 2:
            result["preterite"] = parts[1]
        if len(parts) >= 3:
            result["perfect"] = parts[2]

    aux = _auxiliary(soup)
    if aux:
        result["auxiliary"] = aux

    return result


def _present_paradigm(soup: BeautifulSoup) -> list[str]:
    """The six present-indicative forms, in `_PRONOUNS` order.

    The indicative present is the first conjugation table whose rows are exactly
    the six subject pronouns (the translation table above it is language-keyed,
    so it is skipped; subjunctive/preterite tables come after). Each row's
    remaining cells are the conjugated stem and, for separable verbs, the split
    prefix — joined into one form ("kauft" + "ein" -> "kauft ein"). The optional
    "-e" the site parenthesises ("kauf(e)") is kept as the full written form
    ("kaufe"). Returns [] when no such table is found.
    """
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


def _auxiliary(soup: BeautifulSoup) -> str | None:
    # The perfect-tense auxiliary is a token on the attribute line
    # ("A1 · regular · haben · separable"), the paragraph before the conjugation
    # block — the same line `_separable` reads.
    div = soup.find(id="vStckInf")
    if div is None:
        return None
    p = div.find_previous_sibling("p")
    if p is None:
        return None
    tokens = {t.strip().lower() for t in p.get_text().split("·")}
    for aux in ("haben", "sein"):
        if aux in tokens:
            return aux
    return None


def _declension_tables(soup: BeautifulSoup) -> dict[str, str]:
    """Parse the two noun declension tables (singular, then plural).

    Each row is <th class="vKs" title="Nominative/…"> + <td>article</td> +
    <td>form</td>. The form cell concatenates several <b>/<i>/<u> nodes, which
    get_text() joins; slash-separated variants keep only the primary form.

    Keys are `{case}_{number}` with the case spelled out (`genitive_sg`,
    `nominative_pl`) — the vocabulary the de pack declares. The form is stored
    bare: only the form cell (<td>Hauses</td>) is read, never the article cell
    (<td>des</td>), so the no-leading-article contract holds by construction. The
    noun's nominative article is captured separately as the `gender` feature.
    """
    decl = [t for t in soup.find_all("table") if t.find("th", class_="vKs")]

    result: dict[str, str] = {}
    for table, number in zip(decl[:2], ("sg", "pl")):
        for tr in table.find_all("tr"):
            th = tr.find("th", class_="vKs")
            if th is None:
                continue
            case = _CASES.get(th.get("title"))
            if case is None:
                continue
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            # tds[0] is the article cell ("des"); read only the form cell.
            form = _normalize(tds[1].get_text().split("/")[0])
            if form:
                result[f"{case}_{number}"] = form

    return result
