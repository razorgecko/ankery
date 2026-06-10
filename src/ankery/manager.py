import logging
from collections.abc import Callable, Iterable
from typing import NamedTuple

from ankery.defaults import default_catch_all
from ankery.models import WordInfo
from ankery.notedef import FieldMap, NoteDefinition
from ankery.providers.base import ProviderError, WordProvider
from ankery.sinks.base import AnkiSink

logger = logging.getLogger(__name__)


class AddResult(NamedTuple):
    """Outcome of adding a word: the note id, the form the provider resolved to,
    and what was written — the note type and the rendered fields."""

    note_id: int
    word: str
    note_type: str = ""
    fields: dict[str, str] = {}


class DeckBuilder:
    """Runs the provider chain, normalizes output, routes by category, and writes to the sink."""

    def __init__(
        self,
        providers: Iterable[WordProvider],
        sink: AnkiSink,
        *,
        deck: str,
        note_type: str,
        style_css: str,
        normalize: Callable[[WordInfo], WordInfo] | None = None,
        catch_all_note: NoteDefinition | None = None,
        note_definitions: Iterable[NoteDefinition] | None = None,
        tags: list[str] | None = None,
        category_names: Iterable[str] | None = None,
    ) -> None:
        self.providers = list(providers)
        self.sink = sink
        self.deck = deck
        self.note_type = note_type
        self.style_css = style_css
        self.normalize = normalize or (lambda info: info)
        # The neutral catch-all note; falls back to the engine default when no
        # caller supplies one.
        self.catch_all_note = catch_all_note or default_catch_all()
        self.note_definitions = list(note_definitions or [])
        self.tags = tags or []
        # The pack's declared category vocabulary.
        self.category_names = list(category_names or [])

    def verify_note_types(self) -> list[str]:
        """Provision/validate note types; call once before adding words. Returns
        the names of the note types created.

        The owned catch-all is provisioned only when routing actually writes into
        it — i.e. `note_type` still names it. If the user repointed the catch-all
        at a foreign model with --note-type, we write into that model and must not
        create ours (and assume the foreign one already exists).
        """
        definitions = list(self.note_definitions)
        if self.note_type == self.catch_all_note.name:
            definitions.append(self.catch_all_note)
        return self.sink.verify_note_types(
            definitions,
            default_css=self.style_css,
            catch_all=self.note_type,
        ) or []

    def add_word(self, word: str, *, category_hint: str | None = None) -> AddResult | None:
        """Look up, build, and write a note; returns the result or None on a clean miss.

        The result's `word` is the form the provider resolved to, which may differ
        from the requested `word` (e.g. an inflection redirected to its lemma).
        `category_hint`, when given, forces the routing category (see `lookup`).
        """
        info = self.lookup(word, category_hint=category_hint)
        if info is None:
            return None
        note_type, map_fields = self._route(info)
        fields = map_fields(info)
        logger.info("adding %r to deck %r as %r", info.word, self.deck, note_type)
        note_id = self.sink.add_note(
            deck=self.deck,
            note_type=note_type,
            fields=fields,
            tags=self.tags,
        )
        return AddResult(note_id=note_id, word=info.word, note_type=note_type, fields=fields)

    def _route(self, info: WordInfo) -> tuple[str, FieldMap]:
        """First note whose `applies` matches wins; else the pack default note (a
        note with `applies_to = "*"`); else the neutral catch-all.

        The catch-all renders via its own note definition but is written into the
        configured catch-all model (`note_type`), which the user can repoint with
        `--note-type`; its definition supplies only the field map.
        """
        default: NoteDefinition | None = None
        for note_def in self.note_definitions:
            if note_def.applies(info):
                logger.info(
                    "routing %r (%s) -> note %r", info.word, info.category, note_def.name
                )
                return note_def.name, note_def.render
            if note_def.is_default:
                default = note_def
        if default is not None:
            logger.info(
                "routing %r (%s) -> pack default note %r",
                info.word, info.category, default.name,
            )
            return default.name, default.render
        logger.info(
            "routing %r (%s) -> catch-all %r", info.word, info.category, self.note_type
        )
        return self.note_type, self.catch_all_note.render

    def lookup(self, word: str, *, category_hint: str | None = None) -> WordInfo | None:
        """Run the provider chain and normalize the result; re-raises last ProviderError on total miss.

        When `category_hint` is set the user has explicitly stated the word class, so
        it is authoritative: the resolved info's `category` is overwritten with it
        before normalize runs, ensuring routing and the pack filter see the intended
        category even if a provider classified it differently.
        """
        last_error: ProviderError | None = None
        for provider in self.providers:
            name = getattr(provider, "name", type(provider).__name__)
            logger.info("provider %r: fetching %r (hint=%r)", name, word, category_hint)
            try:
                info = provider.fetch(word, category_hint=category_hint)
            except ProviderError as exc:
                logger.warning("provider %r: failed: %s", name, exc)
                last_error = exc
                continue
            except Exception as exc:
                # A provider bug (e.g. a scraper hitting unexpected markup) must
                # not abort the whole run: contain it like a ProviderError so the
                # chain continues and it surfaces only if nothing else matches.
                logger.warning("provider %r: crashed: %s", name, exc)
                last_error = ProviderError(f"provider {name!r} crashed: {exc}")
                last_error.__cause__ = exc
                continue
            if info is not None:
                logger.info(
                    "provider %r: resolved %r as %s", name, info.word, info.category
                )
                if category_hint is not None:
                    if info.category != category_hint:
                        logger.info(
                            "category hint %r overrides provider category %r",
                            category_hint, info.category,
                        )
                    info.category = category_hint
                return self._normalize(info)
            logger.info("provider %r: miss", name)
        if last_error is not None:
            raise last_error
        return None

    def _normalize(self, info: WordInfo) -> WordInfo:
        try:
            return self.normalize(info)
        except Exception as exc:
            raise ProviderError(f"language pack normalize hook failed: {exc}") from exc
