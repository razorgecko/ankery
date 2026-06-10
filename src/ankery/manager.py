import logging
from collections.abc import Callable, Iterable
from typing import NamedTuple

from ankery.defaults import default_catch_all
from ankery.models import Entry
from ankery.notedef import FieldMap, NoteDefinition
from ankery.providers.base import Provider, ProviderError
from ankery.sinks.base import AnkiSink

logger = logging.getLogger(__name__)


class AddResult(NamedTuple):
    """Outcome of adding (or previewing) a term: the note id (None for a
    preview), the form the provider resolved to, and what was (or would be)
    written — the note type and the rendered fields."""

    note_id: int | None
    term: str
    note_type: str = ""
    fields: dict[str, str] = {}


class DeckBuilder:
    """Runs the provider chain, normalizes output, routes by category, and writes to the sink."""

    def __init__(
        self,
        providers: Iterable[Provider],
        sink: AnkiSink,
        *,
        deck: str,
        note_type: str,
        style_css: str,
        normalize: Callable[[Entry], Entry] | None = None,
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
        self.normalize = normalize or (lambda entry: entry)
        # The neutral catch-all note; falls back to the engine default when no
        # caller supplies one.
        self.catch_all_note = catch_all_note or default_catch_all()
        self.note_definitions = list(note_definitions or [])
        self.tags = tags or []
        # The pack's declared category vocabulary.
        self.category_names = list(category_names or [])

    def verify_note_types(self) -> list[str]:
        """Provision/validate note types; call once before adding terms. Returns
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

    def preview(self, term: str, *, category_hint: str | None = None) -> AddResult | None:
        """Look up, route, and render a note without writing it; None on a clean miss.

        The same path as `add_term` up to the sink — chain, normalize, route,
        render — so the result is exactly what `add_term` would write. `note_id`
        is None.
        """
        entry = self.lookup(term, category_hint=category_hint)
        if entry is None:
            return None
        note_type, map_fields = self._route(entry)
        return AddResult(
            note_id=None, term=entry.term, note_type=note_type, fields=map_fields(entry)
        )

    def add_term(self, term: str, *, category_hint: str | None = None) -> AddResult | None:
        """Look up, build, and write a note; returns the result or None on a clean miss.

        The result's `term` is the form the provider resolved to, which may differ
        from the requested `term` (e.g. an inflection redirected to its lemma).
        `category_hint`, when given, forces the routing category (see `lookup`).
        """
        result = self.preview(term, category_hint=category_hint)
        if result is None:
            return None
        logger.info("adding %r to deck %r as %r", result.term, self.deck, result.note_type)
        note_id = self.sink.add_note(
            deck=self.deck,
            note_type=result.note_type,
            fields=result.fields,
            tags=self.tags,
        )
        return result._replace(note_id=note_id)

    def _route(self, entry: Entry) -> tuple[str, FieldMap]:
        """First note whose `applies` matches wins; else the pack default note (a
        note with `applies_to = "*"`); else the neutral catch-all.

        The catch-all renders via its own note definition but is written into the
        configured catch-all model (`note_type`), which the user can repoint with
        `--note-type`; its definition supplies only the field map.
        """
        default: NoteDefinition | None = None
        for note_def in self.note_definitions:
            if note_def.applies(entry):
                logger.info(
                    "routing %r (%s) -> note %r", entry.term, entry.category, note_def.name
                )
                return note_def.name, note_def.render
            if note_def.is_default:
                default = note_def
        if default is not None:
            logger.info(
                "routing %r (%s) -> pack default note %r",
                entry.term, entry.category, default.name,
            )
            return default.name, default.render
        logger.info(
            "routing %r (%s) -> catch-all %r", entry.term, entry.category, self.note_type
        )
        return self.note_type, self.catch_all_note.render

    def lookup(self, term: str, *, category_hint: str | None = None) -> Entry | None:
        """Run the provider chain and normalize the result; re-raises last ProviderError on total miss.

        When `category_hint` is set the user has explicitly stated the category, so
        it is authoritative: the resolved entry's `category` is overwritten with it
        before normalize runs, ensuring routing and the pack filter see the intended
        category even if a provider classified it differently.
        """
        last_error: ProviderError | None = None
        for provider in self.providers:
            name = getattr(provider, "name", type(provider).__name__)
            logger.info("provider %r: fetching %r (hint=%r)", name, term, category_hint)
            try:
                entry = provider.fetch(term, category_hint=category_hint)
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
            if entry is not None:
                logger.info(
                    "provider %r: resolved %r as %s", name, entry.term, entry.category
                )
                if category_hint is not None:
                    if entry.category != category_hint:
                        logger.info(
                            "category hint %r overrides provider category %r",
                            category_hint, entry.category,
                        )
                    entry.category = category_hint
                return self._normalize(entry)
            logger.info("provider %r: miss", name)
        if last_error is not None:
            raise last_error
        return None

    def _normalize(self, entry: Entry) -> Entry:
        try:
            return self.normalize(entry)
        except Exception as exc:
            raise ProviderError(f"language pack normalize hook failed: {exc}") from exc
