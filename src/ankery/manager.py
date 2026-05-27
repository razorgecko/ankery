from collections.abc import Iterable

from ankery.models import WordInfo
from ankery.notedef import FieldMap, NoteDefinition, default_css, default_field_map
from ankery.providers.base import ProviderError, WordProvider
from ankery.sinks.base import AnkiSink


class DeckBuilder:
    """Orchestrates the provider chain, field mapping, and sink.

    Owns the one piece of glue the layers don't: choosing a note type for a
    `WordInfo` and turning it into the flat field dict a note needs. A word is
    routed by part of speech through `note_definitions` (first whose `applies`
    matches wins, filled by its `render`); a word that matches none falls back to
    `note_type` + `map_fields` (the procedural "Basic" catch-all). Everything
    else is delegated — providers produce the info, the sink writes it.
    """

    def __init__(
        self,
        providers: Iterable[WordProvider],
        sink: AnkiSink,
        *,
        deck: str,
        note_type: str,
        map_fields: FieldMap | None = None,
        note_definitions: Iterable[NoteDefinition] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.providers = list(providers)
        self.sink = sink
        self.deck = deck
        self.note_type = note_type
        self.map_fields = map_fields or default_field_map
        self.note_definitions = list(note_definitions or [])
        self.tags = tags or []

    def verify_note_types(self) -> None:
        """Provision/validate the per-POS note types this builder routes to.

        Delegates the loaded note definitions to the sink (see
        AnkiSink.verify_note_types). The catch-all `note_type` carries no
        definition — it is a built-in like "Basic" we assume Anki already has —
        so it is left out of the definitions, but passed as the style to copy:
        created note types match its look (or the bundled default if Anki can't
        report it) unless their definition sets its own css. Call once before
        adding words.
        """
        self.sink.verify_note_types(
            self.note_definitions,
            default_css=default_css(),
            catch_all=self.note_type,
        )

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
