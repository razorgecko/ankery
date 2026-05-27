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
    interchangeable: the cross-language LLM provider, or a pack-local scraper
    (verbformen), or later a dictionary API — all behind this one method.

    A provider is built for one run against one language pack, so the language
    pair is fixed at construction (the LLM provider is handed the rendered
    prompt; a scraper knows its own language). `fetch` therefore takes only the
    word — the engine never re-passes language at call time.
    """

    name: str

    def fetch(self, word: str) -> WordInfo | None:
        """Return word info, or None if this provider has no result for `word`."""
        ...
