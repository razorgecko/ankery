"""verbformen.com scraper for the German pack.

Nouns (capital first letter): /declension/nouns/{Word}.htm
Verbs (lowercase):            /conjugation/{word}.htm

Imports must be absolute — this file is loaded by path.
"""

import re
from urllib.parse import quote

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
# Present-indicative table is the first table whose rows are exactly these six pronouns.
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


class VerbformenProvider:
    name = "verbformen"

    def __init__(self, *, timeout: float = 15.0, target_language: str = "en") -> None:
        self._target_language = target_language
        self._timeout = timeout

    def fetch(self, word: str) -> WordInfo | None:
        target = self._target_language
        # Scope the client to one lookup so connections are pooled across the
        # requests it makes, then always closed — no leaked sockets.
        with httpx.Client(
            headers=_HEADERS, timeout=self._timeout, follow_redirects=True
        ) as client:
            if word[0].isupper():
                html_text = self._get(client, f"/declension/nouns/{quote(word, safe='')}.htm")
                if html_text is None:
                    return None
                soup = BeautifulSoup(html_text, "html.parser")
                return _parse_noun(word, soup, target, self.name)
            else:
                lemma = word
                html_text = self._get(client, f"/conjugation/{quote(word, safe='')}.htm")
                if html_text is None:
                    normalized = _normalize_verb_input(word)
                    if normalized != word:
                        html_text = self._get(client, f"/conjugation/{quote(normalized, safe='')}.htm")
                        lemma = normalized
                if html_text is None:
                    return None
                soup = BeautifulSoup(html_text, "html.parser")
                return _parse_verb(lemma, soup, target, self.name)

    def _get(self, client: httpx.Client, path: str) -> str | None:
        url = f"{_BASE}{path}"
        headers = {"Accept-Language": _accept_language(self._target_language)}
        try:
            r = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"verbformen.com request failed: {exc}") from exc
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ProviderError(
                f"verbformen.com returned HTTP {r.status_code} for {url}"
            )
        return r.text


def _build(config, pack) -> VerbformenProvider:
    options = pack.provider_options.get("verbformen", {})
    return VerbformenProvider(
        timeout=float(options.get("timeout", 15.0)),
        target_language=config.target_language,
    )


PROVIDERS = {"verbformen": _build}



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



def _accept_language(target_language: str | None) -> str:
    """Build Accept-Language header; target first, English as low-priority fallback."""
    lang = (target_language or "en").strip()
    if lang.lower().startswith("en"):
        return "en-US,en;q=0.9"
    return f"{lang},{lang};q=0.9,en;q=0.1"


def _normalize(text: str) -> str:
    """Collapse whitespace and strip footnote superscripts."""
    return re.sub(r"\s+", " ", text).strip().translate(_SUPERSCRIPTS)


def _steckbrief_panel(krz):
    """The inner summary panel (IPA, definition, example); None when absent."""
    if krz is None:
        return None
    span = krz.select_one("span[lang]")
    if span is not None:
        p = span.find_parent("p")
        if p is not None:
            return p.parent
    return krz



def _translations(soup: BeautifulSoup, lang: str) -> list[str]:
    # dl.vNrn holds all languages as <dd lang="xx">; read from here to support any target.
    dd = soup.select_one(f'dl.vNrn dd[lang="{lang}"]')
    if dd is None:
        return []
    text = _normalize(dd.get_text())
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
    i = steckbrief.find("i")  # the lone <i> in the panel holds the definition
    if i is None:
        return []
    text = _normalize(i.get_text())
    return [text] if text else []


def _example(steckbrief) -> tuple[list[str], list[str]]:
    """Return ([german_sentence], [translation]); a flag <img> separates the two halves."""
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
    return "".join(n.get_text() if isinstance(n, Tag) else str(n) for n in nodes)


def _clean_example(text: str) -> str:
    # Rejoin detached period (" ." -> ".") the site renders as a separate sibling.
    text = re.sub(r"\s+\.", ".", _normalize(text))
    return text.lstrip("»").strip()


def _audio_url(krz) -> str | None:
    if krz is None:
        return None
    a = krz.select_one("span.vGrnd a[href]")
    if a is None:
        return None
    href = a["href"]
    return href if href.endswith(".mp3") else None


def _normalize_verb_input(word: str) -> str:
    """Strip inserted "zu" from a separable zu-infinitive: umzusetzen -> umsetzen."""
    for prefix in _SEPARABLE_PREFIXES:
        if word.startswith(prefix + "zu") and len(word) > len(prefix) + 2:
            return prefix + word[len(prefix) + 2 :]
    return word


def _headword(krz, *, fallback: str) -> str:
    """Canonical lemma from span.vGrnd; strips article and separable-verb middot."""
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
    # Attribute line ("A1 · regular · haben · separable") is the <p> before #vStckInf.
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
    """Extract present paradigm, preterite, perfect, and auxiliary from the page."""
    result: dict[str, str] = {}

    for key, form in zip(_PRESENT_KEYS, _present_paradigm(soup)):
        if form:
            result[key] = form

    # Principal parts: "kauft ein · kaufte ein · hat eingekauft"
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
    """Six present-indicative forms from the first table whose rows are exactly _PRONOUNS."""
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
    # Same attribute line as _separable ("A1 · regular · haben · separable").
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
    """Parse singular and plural declension tables; keys are {case}_{number} (e.g. genitive_sg)."""
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
            form = _normalize(tds[1].get_text().split("/")[0])  # skip article cell (tds[0])
            if form:
                result[f"{case}_{number}"] = form

    return result
