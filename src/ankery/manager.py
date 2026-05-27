from collections.abc import Callable, Iterable
from typing import NamedTuple

from ankery.models import WordInfo
from ankery.notedef import FieldMap, NoteDefinition, default_field_map
from ankery.providers.base import ProviderError, WordProvider
from ankery.sinks.base import AnkiSink


class AddResult(NamedTuple):
    """Outcome of adding a word: the note id and the form the provider resolved to."""

    note_id: int
    word: str


class DeckBuilder:
    """Runs the provider chain, normalizes output, routes by POS, and writes to the sink."""

    def __init__(
        self,
        providers: Iterable[WordProvider],
        sink: AnkiSink,
        *,
        deck: str,
        note_type: str,
        style_css: str,
        normalize: Callable[[WordInfo], WordInfo] | None = None,
        map_fields: FieldMap | None = None,
        note_definitions: Iterable[NoteDefinition] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.providers = list(providers)
        self.sink = sink
        self.deck = deck
        self.note_type = note_type
        self.style_css = style_css
        self.normalize = normalize or (lambda info: info)
        self.map_fields = map_fields or default_field_map
        self.note_definitions = list(note_definitions or [])
        self.tags = tags or []

    def verify_note_types(self) -> None:
        """Provision/validate note types; call once before adding words."""
        self.sink.verify_note_types(
            self.note_definitions,
            default_css=self.style_css,
            catch_all=self.note_type,
        )

    def add_word(self, word: str) -> AddResult | None:
        """Look up, build, and write a note; returns the result or None on a clean miss.

        The result's `word` is the form the provider resolved to, which may differ
        from the requested `word` (e.g. an inflection redirected to its lemma).
        """
        info = self.lookup(word)
        if info is None:
            return None
        note_type, map_fields = self._route(info)
        note_id = self.sink.add_note(
            deck=self.deck,
            note_type=note_type,
            fields=map_fields(info),
            tags=self.tags,
        )
        return AddResult(note_id=note_id, word=info.word)

    def _route(self, info: WordInfo) -> tuple[str, FieldMap]:
        """First note definition whose `applies` matches wins; else the catch-all."""
        for note_def in self.note_definitions:
            if note_def.applies(info):
                return note_def.name, note_def.render
        return self.note_type, self.map_fields

    def lookup(self, word: str) -> WordInfo | None:
        """Run the provider chain and normalize the result; re-raises last ProviderError on total miss."""
        last_error: ProviderError | None = None
        for provider in self.providers:
            try:
                info = provider.fetch(word)
            except ProviderError as exc:
                last_error = exc
                continue
            if info is not None:
                return self._normalize(info)
        if last_error is not None:
            raise last_error
        return None

    def _normalize(self, info: WordInfo) -> WordInfo:
        try:
            return self.normalize(info)
        except Exception as exc:
            raise ProviderError(f"language pack normalize hook failed: {exc}") from exc
