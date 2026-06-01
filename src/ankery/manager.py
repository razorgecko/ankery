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
        pos_names: Iterable[str] | None = None,
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
        # The pack's declared part-of-speech vocabulary; the CLI resolves a
        # `word:pos` hint against it before calling add_word.
        self.pos_names = list(pos_names or [])

    def verify_note_types(self) -> None:
        """Provision/validate note types; call once before adding words."""
        self.sink.verify_note_types(
            self.note_definitions,
            default_css=self.style_css,
            catch_all=self.note_type,
        )

    def add_word(self, word: str, *, pos_hint: str | None = None) -> AddResult | None:
        """Look up, build, and write a note; returns the result or None on a clean miss.

        The result's `word` is the form the provider resolved to, which may differ
        from the requested `word` (e.g. an inflection redirected to its lemma).
        `pos_hint`, when given, forces the routing part of speech (see `lookup`).
        """
        info = self.lookup(word, pos_hint=pos_hint)
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
        """First note whose `applies` matches wins; else the pack default note (a
        note with `applies_to = "*"`); else the language-neutral catch-all."""
        default: NoteDefinition | None = None
        for note_def in self.note_definitions:
            if note_def.applies(info):
                return note_def.name, note_def.render
            if note_def.is_default:
                default = note_def
        if default is not None:
            return default.name, default.render
        return self.note_type, self.map_fields

    def lookup(self, word: str, *, pos_hint: str | None = None) -> WordInfo | None:
        """Run the provider chain and normalize the result; re-raises last ProviderError on total miss.

        When `pos_hint` is set the user has explicitly stated the word class, so it
        is authoritative: the resolved info's `part_of_speech` is overwritten with it
        before normalize runs, ensuring routing and the pack filter see the intended
        POS even if a provider classified it differently.
        """
        last_error: ProviderError | None = None
        for provider in self.providers:
            try:
                info = provider.fetch(word, pos_hint=pos_hint)
            except ProviderError as exc:
                last_error = exc
                continue
            except Exception as exc:
                # A provider bug (e.g. a scraper hitting unexpected markup) must
                # not abort the whole run: contain it like a ProviderError so the
                # chain continues and it surfaces only if nothing else matches.
                name = getattr(provider, "name", type(provider).__name__)
                last_error = ProviderError(f"provider {name!r} crashed: {exc}")
                last_error.__cause__ = exc
                continue
            if info is not None:
                if pos_hint is not None:
                    info.part_of_speech = pos_hint
                return self._normalize(info)
        if last_error is not None:
            raise last_error
        return None

    def _normalize(self, info: WordInfo) -> WordInfo:
        try:
            return self.normalize(info)
        except Exception as exc:
            raise ProviderError(f"language pack normalize hook failed: {exc}") from exc
