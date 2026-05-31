from typing import Protocol, runtime_checkable

from ankery.models import WordInfo


class ProviderError(Exception):
    """Hard provider failure (network error, bad response, validation failure).

    Distinct from a clean miss: return None for "no result", raise this for failures.
    """


@runtime_checkable
class WordProvider(Protocol):
    name: str

    def fetch(self, word: str, pos_hint: str | None = None) -> WordInfo | None:
        """Return word info, or None if this provider has no result for `word`.

        `pos_hint`, when given, is a canonical part-of-speech name from the active
        pack's vocabulary (resolved at the CLI boundary). A provider may use it to
        disambiguate (e.g. pick the right page to scrape) or to skip a lookup it
        can't serve for that POS; passing it is always optional.
        """
        ...
