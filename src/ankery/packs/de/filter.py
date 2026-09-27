"""German pack filter: applies per-category cleanup rules, looked up by `entry.category`.

Imports must be absolute — this file is loaded by path.
"""

from ankery.models import Entry

_ARTICLES = {"der", "die", "das", "des", "dem", "den"}


def _strip_leading_article(form: str) -> str:
    head, _, rest = form.partition(" ")
    return rest if head.lower() in _ARTICLES and rest else form


def _strip_articles(entry: Entry) -> Entry:
    """Strip leading definite articles from the term and property values
    ("des Hauses" -> "Hauses")."""
    entry.term = _strip_leading_article(entry.term)
    entry.properties = {
        key: _strip_leading_article(value) for key, value in entry.properties.items()
    }
    return entry


def _noun_headword(entry: Entry) -> Entry:
    """Set `headword` to "term (gender), plural", or "term (Pl.)" when there is no
    gender.

    A missing gender marks a noun with no singular, whose term is its plural, so
    `nominative_pl` is set to the term.
    """
    properties = dict(entry.properties)
    gender = properties.get("gender", "").strip()
    if gender:
        plural = properties.get("nominative_pl", "").strip()
        headword = f"{entry.term} ({gender})" + (f", {plural}" if plural else "")
    else:
        properties["nominative_pl"] = entry.term
        headword = f"{entry.term} (Pl.)"
    properties["headword"] = headword
    entry.properties = properties
    return entry


def _normalize_noun(entry: Entry) -> Entry:
    return _noun_headword(_strip_articles(entry))


# Preposition -> the case it fixes, or None for a two-way preposition, whose case
# depends on the verb.
_PREPOSITIONS = {
    "mit": "Dat", "von": "Dat", "bei": "Dat", "zu": "Dat", "aus": "Dat", "nach": "Dat",
    "für": "Akk", "um": "Akk", "durch": "Akk", "gegen": "Akk", "ohne": "Akk",
    "an": None, "auf": None, "in": None, "über": None, "vor": None,
    "hinter": None, "neben": None, "unter": None, "zwischen": None,
}

_CASES = {
    "akk": "Akk", "akkusativ": "Akk", "acc": "Akk", "accusative": "Akk",
    "dat": "Dat", "dativ": "Dat", "dative": "Dat",
}


def _split_preposition(entry: Entry) -> Entry:
    """Set `preposition` and `base` from the term's last word, and `preposition_case`
    from the preposition, or, for a two-way preposition, from the provider's value
    mapped through _CASES.

    Without a preposition the infinitive ends the term, and a separable particle is
    joined to it (aufregen), so a final word in _PREPOSITIONS can only be a governed
    preposition. All three keys are always written, so a provider value for
    `preposition` or `base` never survives.
    """
    head, _, last = entry.term.rpartition(" ")
    properties = dict(entry.properties)
    if head and last in _PREPOSITIONS:
        fixed = _PREPOSITIONS[last]
        written = properties.get("preposition_case", "").strip().rstrip(".").lower()
        properties.update(
            preposition=last,
            base=head.rstrip(),
            preposition_case=fixed or _CASES.get(written, ""),
        )
    else:
        properties.update(preposition="", base=entry.term, preposition_case="")
    entry.properties = properties
    return entry


def _verb_headword(entry: Entry) -> Entry:
    """Set `headword` to "term, perfect", or the term alone when there is no perfect."""
    perfect = entry.properties.get("perfect", "").strip()
    headword = ", ".join(part for part in (entry.term, perfect) if part)
    entry.properties = {**entry.properties, "headword": headword}
    return entry


def _normalize_verb(entry: Entry) -> Entry:
    return _verb_headword(_split_preposition(entry))


_BY_CATEGORY = {"noun": _normalize_noun, "verb": _normalize_verb}


def normalize(entry: Entry) -> Entry:
    step = _BY_CATEGORY.get(entry.category)
    return step(entry) if step else entry
