"""German pack filter: strips leading definite articles from feature values ("des Hauses" -> "Hauses").

Imports must be absolute — this file is loaded by path.
"""

from ankery.models import Entry

_ARTICLES = {"der", "die", "das", "des", "dem", "den"}


def _strip_leading_article(form: str) -> str:
    head, _, rest = form.partition(" ")
    return rest if head.lower() in _ARTICLES and rest else form


def normalize(entry: Entry) -> Entry:
    entry.properties = {
        key: _strip_leading_article(value) for key, value in entry.properties.items()
    }
    return entry
