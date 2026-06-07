from typing import Protocol, runtime_checkable

from ankery.models import WordInfo


class ProviderError(Exception):
    """Hard provider failure (network error, bad response, validation failure).

    Distinct from a clean miss: return None for "no result", raise this for failures.
    """


@runtime_checkable
class WordProvider(Protocol):
    name: str

    def fetch(self, word: str, category_hint: str | None = None) -> WordInfo | None:
        """Return word info, or None if this provider has no result for `word`.

        `category_hint`, when given, is a canonical category value from the active
        pack's vocabulary. A provider may use it to disambiguate (e.g. pick the
        right page to scrape) or to skip a lookup it can't serve for that category;
        passing it is always optional.
        """
        ...
