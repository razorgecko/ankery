"""Word provider that scrapes verbformen.com for German grammar data.

Fetches the compact steckbrief (summary panel) from one URL per word:
  - Nouns (capital first letter): /declension/nouns/{Word}.htm
  - Verbs (lowercase):            /conjugation/{word}.htm

Parsing uses BeautifulSoup for element selection — the steckbrief and the
declension tables carry stable id/class anchors — with light regex only for
*text* normalization (whitespace, footnote superscripts). A real parser is
what lets the translation span be read whole: the site wraps rarer senses in
nested <span> elements, and a non-greedy `(.*?)</span>` regex would stop at the
first nested close tag and silently drop every sense after it. get_text() has
no such failure mode.

No Cloudflare bypass needed — the site serves 200 to plain httpx.
"""

import re

import httpx
from bs4 import BeautifulSoup

from anki_deckbuilder.models import WordInfo
from anki_deckbuilder.providers.base import ProviderError

_BASE = "https://www.verbformen.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
# Footnote superscript digits the site appends to forms (e.g. ⁵ ⁶)
_SUPERSCRIPTS = str.maketrans("", "", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_CASES = {"Nominative": "nom", "Genitive": "gen", "Dative": "dat", "Accusative": "acc"}


class VerbformenProvider:
    name = "verbformen"

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._client = httpx.Client(
            headers=_HEADERS, timeout=timeout, follow_redirects=True
        )

    def fetch(
        self,
        word: str,
        *,
        source_language: str,
        target_language: str,
    ) -> WordInfo | None:
        # German nouns are always capitalised; verbs/adjectives start lowercase.
        if word[0].isupper():
            html_text = self._get(f"/declension/nouns/{word}.htm")
            if html_text is None:
                return None
            soup = BeautifulSoup(html_text, "html.parser")
            return _parse_noun(word, soup, target_language, self.name)
        else:
            html_text = self._get(f"/conjugation/{word}.htm")
            if html_text is None:
                return None
            soup = BeautifulSoup(html_text, "html.parser")
            return _parse_verb(word, soup, target_language, self.name)

    def _get(self, path: str) -> str | None:
        url = f"{_BASE}{path}"
        try:
            r = self._client.get(url)
        except httpx.HTTPError as exc:
            raise ProviderError(f"verbformen.com request failed: {exc}") from exc
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ProviderError(
                f"verbformen.com returned HTTP {r.status_code} for {url}"
            )
        return r.text


# ---------------------------------------------------------------------------
# Top-level parsers
# ---------------------------------------------------------------------------


def _parse_noun(
    word: str, soup: BeautifulSoup, target_language: str, source: str
) -> WordInfo | None:
    krz = soup.find(id="vStckKrz")
    steckbrief = _steckbrief_panel(krz)
    info = WordInfo(
        word=word,
        part_of_speech="noun",
        gender=_gender(krz),
        translations=_translations(soup, target_language),
        pronunciation=_pronunciation(steckbrief),
        definitions=_definition(steckbrief),
        examples=_example(steckbrief),
        inflections=_declension_tables(soup),
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
    info = WordInfo(
        word=word,
        part_of_speech="verb",
        separable=_separable(soup),
        translations=_translations(soup, target_language),
        pronunciation=_pronunciation(steckbrief),
        definitions=_definition(steckbrief),
        examples=_example(steckbrief),
        inflections=_stammformen(soup),
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


def _example(steckbrief) -> list[str]:
    if steckbrief is None:
        return []
    # Example sentences start with ». German and translation share the paragraph;
    # a stray " ." appears because the period and the flag <img> are siblings.
    for p in steckbrief.find_all("p"):
        text = _normalize(p.get_text())
        if text.startswith("»"):
            return [re.sub(r"\s+\.", ".", text)]
    return []


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


def _stammformen(soup: BeautifulSoup) -> dict[str, str]:
    """Principal parts from the id='stammformen' paragraph.

    For einkaufen the text is "kauft ein · kaufte ein · hat eingekauft",
    mapping to present_3sg / preterite_3sg / perfect. The trailing audio link
    holds only an <img>, so it contributes no text.
    """
    p = soup.find(id="stammformen")
    if p is None:
        return {}
    parts = [s.strip() for s in _normalize(p.get_text()).split("·") if s.strip()]
    keys = ["present_3sg", "preterite_3sg", "perfect"]
    return dict(zip(keys, parts))


def _declension_tables(soup: BeautifulSoup) -> dict[str, str]:
    """Parse the two noun declension tables (singular, then plural).

    Each row is <th class="vKs" title="Nominative/…"> + <td>article</td> +
    <td>form</td>. The form cell concatenates several <b>/<i>/<u> nodes, which
    get_text() joins; slash-separated variants keep only the primary form.
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
            article = _normalize(tds[0].get_text())
            form = _normalize(tds[1].get_text().split("/")[0])
            result[f"{case}_{number}"] = f"{article} {form}".strip()

    return result
