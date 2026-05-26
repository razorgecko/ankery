"""Format-normalization applied at a provider's output boundary.

A provider is the only place that knows its source's shape quirks, so the
`WordInfo` contract (see WordInfo.inflections) is enforced here rather than in
consumers. The article table is keyed by language so the logic stays
language-agnostic — the German-ness is data, not control flow — and an unknown
language degrades to a no-op (we trust the prompt instruction for languages we
have no table for).
"""

# Leading definite articles to strip from an inflected form so it matches the
# bare-form contract on WordInfo.inflections. Keyed by source language (the
# `source_language` code, e.g. "de"); add an entry per language as needed.
_ARTICLES_BY_LANG = {
    "de": {"der", "die", "das", "des", "dem", "den"},
}


def strip_leading_article(form: str, language: str | None) -> str:
    """Drop a leading definite article from an inflected form.

    Splits off the first whitespace-delimited token and removes it when it is a
    definite article in `language` (e.g. "des Hauses" -> "Hauses"). A form with
    no article, a single token, or an unknown/absent language is returned
    unchanged.
    """
    articles = _ARTICLES_BY_LANG.get((language or "").lower(), ())
    head, _, rest = form.partition(" ")
    return rest if head.lower() in articles and rest else form
