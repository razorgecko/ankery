"""German pack filter: applies per-category cleanup rules, looked up by `entry.category`.

Imports must be absolute — this file is loaded by path.
"""

from ankery.models import Entry

_ARTICLES = {"der", "die", "das", "des", "dem", "den"}


def _strip_leading_article(form: str) -> str:
    head, _, rest = form.partition(" ")
    return rest if head.lower() in _ARTICLES and rest else form


def _strip_articles(entry: Entry) -> Entry:
    """Strip leading definite articles from property values ("des Hauses" -> "Hauses")."""
    entry.properties = {
        key: _strip_leading_article(value) for key, value in entry.properties.items()
    }
    return entry


_BY_CATEGORY = {"noun": _strip_articles}


def normalize(entry: Entry) -> Entry:
    step = _BY_CATEGORY.get(entry.category)
    return step(entry) if step else entry
