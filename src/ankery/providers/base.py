from typing import Protocol, runtime_checkable

from ankery.models import WordInfo


class ProviderError(Exception):
    """Hard provider failure (network error, bad response, validation failure).

    Distinct from a clean miss: return None for "no result", raise this for failures.
    """


@runtime_checkable
class WordProvider(Protocol):
    name: str

    def fetch(self, word: str) -> WordInfo | None:
        """Return word info, or None if this provider has no result for `word`."""
        ...
