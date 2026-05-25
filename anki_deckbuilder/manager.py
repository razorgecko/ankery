from collections.abc import Callable, Iterable

from anki_deckbuilder.models import WordInfo
from anki_deckbuilder.providers.base import ProviderError, WordProvider
from anki_deckbuilder.sinks.base import AnkiSink

FieldMap = Callable[[WordInfo], dict[str, str]]


class DeckBuilder:
    """Orchestrates the provider chain, field mapping, and sink.

    Owns the one piece of glue the layers don't: turning a `WordInfo` into the
    flat field dict a note needs. Everything else is delegated — providers
    produce the info, the sink writes it.
    """

    def __init__(
        self,
        providers: Iterable[WordProvider],
        sink: AnkiSink,
        *,
        deck: str,
        note_type: str,
        map_fields: FieldMap | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.providers = list(providers)
        self.sink = sink
        self.deck = deck
        self.note_type = note_type
        self.map_fields = map_fields or default_field_map
        self.tags = tags or []

    def add_word(
        self,
        word: str,
        *,
        source_language: str,
        target_language: str,
    ) -> int | None:
        """Look the word up, build a note, write it; return the note id.

        Returns None if no provider had the word (a clean miss across the whole
        chain) — there is nothing to write, which the caller can report.
        """
        info = self.lookup(
            word,
            source_language=source_language,
            target_language=target_language,
        )
        if info is None:
            return None
        fields = self.map_fields(info)
        return self.sink.add_note(
            deck=self.deck,
            note_type=self.note_type,
            fields=fields,
            tags=self.tags,
        )

    def lookup(
        self,
        word: str,
        *,
        source_language: str,
        target_language: str,
    ) -> WordInfo | None:
        """Run the fallback chain: first provider with a result wins.

        A provider returning None is a clean miss — try the next one. A
        ProviderError is a hard failure; we still try the next provider, but if
        the chain is exhausted without a result we re-raise it rather than
        masking the failure as a clean miss.
        """
        last_error: ProviderError | None = None
        for provider in self.providers:
            try:
                info = provider.fetch(
                    word,
                    source_language=source_language,
                    target_language=target_language,
                )
            except ProviderError as exc:
                last_error = exc
                continue
            if info is not None:
                return info
        if last_error is not None:
            raise last_error
        return None


def default_field_map(info: WordInfo) -> dict[str, str]:
    """Render a `WordInfo` into Front/Back fields for a Basic-style note.

    Deliberately simple — slice 5 (config) will let callers supply their own
    mapping for richer note types. Fields are HTML, so lines are joined with
    <br>.
    """
    return {"Front": _front(info), "Back": _back(info)}


def _front(info: WordInfo) -> str:
    # Show nouns with their article so the gender is learned with the word.
    if info.gender:
        return f"{info.gender} {info.word}"
    return info.word


def _back(info: WordInfo) -> str:
    sections: list[str] = []
    if info.translations:
        sections.append(", ".join(info.translations))
    if info.definitions:
        sections.append("<br>".join(info.definitions))
    if info.inflections:
        sections.append(
            "<br>".join(f"{key}: {value}" for key, value in info.inflections.items())
        )
    if info.examples:
        sections.append("<br>".join(f"<i>{ex}</i>" for ex in info.examples))
    if info.pronunciation:
        sections.append(f"[{info.pronunciation}]")
    return "<hr>".join(sections)
