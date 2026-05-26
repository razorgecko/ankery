from typing import Protocol, runtime_checkable

from ankery.models import WordInfo


class ProviderError(Exception):
    """A provider failed in a way that should not be silently skipped.

    Distinct from a clean miss: a provider that simply has no entry for the word
    returns None so the manager moves to the next one in the chain. Raise this
    for hard failures instead (network errors, malformed responses, output that
    fails WordInfo validation) so the manager can decide whether to abort.
    """


@runtime_checkable
class WordProvider(Protocol):
    """A source of word information.

    The seam the manager's fallback chain types against. Implementations are
    interchangeable: the LLM provider here, or later a dictionary API, a
    web scraper, or a manual stdin entry provider — all behind this one method.
    """

    name: str

    def fetch(
        self,
        word: str,
        *,
        source_language: str,
        target_language: str,
    ) -> WordInfo | None:
        """Return word info, or None if this provider has no result for `word`."""
        ...
