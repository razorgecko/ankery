from collections.abc import Callable, Iterable

from ankery.models import WordInfo
from ankery.notedef import FieldMap, NoteDefinition, default_field_map
from ankery.providers.base import ProviderError, WordProvider
from ankery.sinks.base import AnkiSink


class DeckBuilder:
    """Orchestrates the provider chain, normalization, field mapping, and sink.

    Owns the glue the layers don't: running the fallback chain, applying the
    pack's `normalize` hook to whatever a provider returns, then choosing a note
    type and turning the `WordInfo` into the flat field dict a note needs. A word
    is routed by part of speech through `note_definitions` (first whose `applies`
    matches wins, filled by its `render`); a word that matches none falls back to
    `note_type` + `map_fields` (the language-neutral "Basic" catch-all).

    `normalize` and the note definitions both come from the active language pack;
    the engine itself names no language.
    """

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
        """Provision/validate the per-POS note types this builder routes to.

        Delegates the loaded note definitions to the sink (see
        AnkiSink.verify_note_types). The catch-all `note_type` carries no
        definition — it is a built-in like "Basic" we assume Anki already has —
        so it is left out of the definitions, but passed as the style to copy:
        created note types match its look (or the pack's style.css if Anki can't
        report it) unless their definition sets its own css. Call once before
        adding words.
        """
        self.sink.verify_note_types(
            self.note_definitions,
            default_css=self.style_css,
            catch_all=self.note_type,
        )

    def add_word(self, word: str) -> int | None:
        """Look the word up, build a note, write it; return the note id.

        Returns None if no provider had the word (a clean miss across the whole
        chain) — there is nothing to write, which the caller can report.
        """
        info = self.lookup(word)
        if info is None:
            return None
        note_type, map_fields = self._route(info)
        return self.sink.add_note(
            deck=self.deck,
            note_type=note_type,
            fields=map_fields(info),
            tags=self.tags,
        )

    def _route(self, info: WordInfo) -> tuple[str, FieldMap]:
        """Pick the note type and field map for a word: first note definition
        whose `applies` matches wins (filled by its `render`), else the catch-all
        `note_type` + `map_fields`."""
        for note_def in self.note_definitions:
            if note_def.applies(info):
                return note_def.name, note_def.render
        return self.note_type, self.map_fields

    def lookup(self, word: str) -> WordInfo | None:
        """Run the fallback chain, then the pack's normalize hook.

        First provider with a result wins. A provider returning None is a clean
        miss — try the next one. A ProviderError is a hard failure; we still try
        the next provider, but if the chain is exhausted without a result we
        re-raise it rather than masking the failure as a clean miss. The winning
        `WordInfo` is passed through the pack's `normalize` before return.
        """
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
        """Apply the pack's normalize hook, wrapping a pack-code failure cleanly."""
        try:
            return self.normalize(info)
        except Exception as exc:  # pack-author code: surface, don't crash opaque
            raise ProviderError(f"language pack normalize hook failed: {exc}") from exc
