import logging

import pytest

from ankery.manager import DeckBuilder
from ankery.models import Entry
from ankery.notedef import NoteDefinition
from ankery.providers.base import ProviderError


class FakeProvider:
    """A provider whose fetch behavior is scripted per test."""

    def __init__(self, name: str, *, result: Entry | None = None, error: Exception | None = None):
        self.name = name
        self._result = result
        self._error = error
        self.calls = 0
        self.last_category_hint: str | None = None

    def fetch(self, term: str, category_hint: str | None = None) -> Entry | None:
        self.calls += 1
        self.last_category_hint = category_hint
        if self._error is not None:
            raise self._error
        return self._result


class FakeSink:
    """Records the single add_note call and returns a fixed note id."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.verified: dict | None = None
        self.created_result: list[str] = []

    def add_note(self, *, deck, note_type, fields, tags=None) -> int:
        self.calls.append(
            {"deck": deck, "note_type": note_type, "fields": fields, "tags": tags}
        )
        return 42

    def verify_note_types(self, definitions, *, default_css="", catch_all=None) -> list[str]:
        self.verified = {
            "definitions": list(definitions),
            "default_css": default_css,
            "catch_all": catch_all,
        }
        return self.created_result


def _entry(term: str = "Buch") -> Entry:
    return Entry(term=term, source="test")


def _entry_with_category(category: str, term: str = "Bank") -> Entry:
    return Entry(term=term, source="test", category=category)


def _builder(providers, sink=None, **kwargs) -> DeckBuilder:
    sink = sink or FakeSink()
    kwargs.setdefault("deck", "German")
    kwargs.setdefault("note_type", "Basic")
    kwargs.setdefault("style_css", "")
    return DeckBuilder(providers, sink, **kwargs)


def test_first_provider_with_result_wins_and_short_circuits():
    first = FakeProvider("first", result=_entry())
    second = FakeProvider("second", result=_entry("other"))
    entry = _builder([first, second]).lookup("Buch")

    assert entry.term == "Buch"
    assert second.calls == 0


def test_clean_miss_falls_through_to_next_provider():
    first = FakeProvider("first", result=None)
    second = FakeProvider("second", result=_entry())
    entry = _builder([first, second]).lookup("Buch")

    assert entry.term == "Buch"
    assert first.calls == 1


def test_provider_error_falls_through_to_next_provider():
    first = FakeProvider("first", error=ProviderError("boom"))
    second = FakeProvider("second", result=_entry())
    entry = _builder([first, second]).lookup("Buch")

    assert entry.term == "Buch"
    assert first.calls == 1


def test_category_hint_is_forwarded_to_each_provider():
    first = FakeProvider("first", result=None)
    second = FakeProvider("second", result=_entry())
    _builder([first, second]).lookup("Bank", category_hint="noun")

    assert first.last_category_hint == "noun"
    assert second.last_category_hint == "noun"


def test_category_hint_overrides_the_providers_classification():
    # The user's explicit hint is authoritative: it wins over whatever category the
    # provider stamped, so routing and the pack filter see the intended category.
    provider = FakeProvider("p", result=_entry_with_category("verb"))
    entry = _builder([provider]).lookup("Bank", category_hint="noun")

    assert entry.category == "noun"


def test_no_category_hint_leaves_the_providers_classification_intact():
    provider = FakeProvider("p", result=_entry_with_category("verb"))
    entry = _builder([provider]).lookup("laufen")

    assert entry.category == "verb"


def test_all_clean_misses_returns_none():
    providers = [FakeProvider("a", result=None), FakeProvider("b", result=None)]
    entry = _builder(providers).lookup("Buch")

    assert entry is None


def test_exhausted_chain_with_errors_reraises_last_error():
    providers = [
        FakeProvider("a", error=ProviderError("first failure")),
        FakeProvider("b", error=ProviderError("second failure")),
    ]
    with pytest.raises(ProviderError, match="second failure"):
        _builder(providers).lookup("Buch")


def test_error_then_clean_miss_still_reraises():
    # A clean miss after a hard failure must not mask the failure.
    providers = [
        FakeProvider("a", error=ProviderError("boom")),
        FakeProvider("b", result=None),
    ]
    with pytest.raises(ProviderError, match="boom"):
        _builder(providers).lookup("Buch")


def test_unexpected_provider_exception_falls_through_to_next_provider():
    # A provider bug (not a ProviderError) must not abort the chain: a later
    # provider can still satisfy the lookup.
    first = FakeProvider("first", error=AttributeError("scraper hit bad markup"))
    second = FakeProvider("second", result=_entry())
    entry = _builder([first, second]).lookup("Buch")

    assert entry.term == "Buch"
    assert first.calls == 1


def test_unexpected_provider_exception_is_wrapped_as_provider_error():
    # On a total miss, the crash surfaces as a ProviderError naming the provider,
    # with the original exception chained — never an uncaught AttributeError.
    provider = FakeProvider("scraper", error=AttributeError("bad markup"))
    with pytest.raises(ProviderError, match="provider 'scraper' crashed") as exc_info:
        _builder([provider]).lookup("Buch")
    assert isinstance(exc_info.value.__cause__, AttributeError)


def test_normalize_hook_is_applied_to_the_result():
    # The pack's normalize hook runs on whatever a provider returns, before
    # routing. Here it uppercases the term to prove it is in the path.
    def shout(entry: Entry) -> Entry:
        entry.term = entry.term.upper()
        return entry

    entry = _builder([FakeProvider("p", result=_entry())], normalize=shout).lookup("Buch")
    assert entry.term == "BUCH"


def test_normalize_hook_failure_surfaces_as_provider_error():
    def boom(entry: Entry) -> Entry:
        raise RuntimeError("pack bug")

    builder = _builder([FakeProvider("p", result=_entry())], normalize=boom)
    with pytest.raises(ProviderError, match="normalize hook failed"):
        builder.lookup("Buch")


def test_add_term_maps_fields_and_writes_to_sink():
    sink = FakeSink()
    builder = _builder(
        [FakeProvider("p", result=_entry())], sink, deck="German", note_type="Basic", tags=["auto"]
    )

    result = builder.add_term("Buch")

    assert result.note_id == 42
    assert result.term == "Buch"
    assert result.note_type == "Basic"
    assert result.fields["Front"] == "Buch"
    assert len(sink.calls) == 1
    call = sink.calls[0]
    assert call["deck"] == "German"
    assert call["note_type"] == "Basic"
    assert call["tags"] == ["auto"]
    assert call["fields"] == result.fields  # what was reported is what was written


def test_add_term_returns_none_and_skips_sink_on_total_miss():
    sink = FakeSink()
    builder = _builder([FakeProvider("p", result=None)], sink)

    result = builder.add_term("Buch")

    assert result is None
    assert sink.calls == []


def test_custom_catch_all_note_is_used():
    # The catch-all terminus is a NoteDefinition; a caller can supply its own.
    sink = FakeSink()
    custom = NoteDefinition(name="Custom", field_map={"Word": "{{ term }}"})
    builder = _builder([FakeProvider("p", result=_entry())], sink, catch_all_note=custom)

    builder.add_term("Buch")

    assert sink.calls[0]["fields"] == {"Word": "Buch"}


def test_matching_note_definition_picks_its_note_type_and_map():
    sink = FakeSink()
    note_def = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ term }}"}, applies_to="verb"
    )
    builder = _builder(
        [FakeProvider("p", result=Entry(term="sehen", source="t", category="verb"))],
        sink,
        note_definitions=[note_def],
    )

    builder.add_term("sehen")

    assert sink.calls[0]["note_type"] == "Ankery DE: Verb"
    assert sink.calls[0]["fields"] == {"Infinitive": "sehen"}


def test_term_matching_no_note_definition_falls_back_to_default_note_type():
    sink = FakeSink()
    note_def = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ term }}"}, applies_to="verb"
    )
    builder = _builder(
        [FakeProvider("p", result=Entry(term="schön", source="t", category="adjective"))],
        sink,
        note_type="Basic",
        note_definitions=[note_def],
    )

    builder.add_term("schön")

    assert sink.calls[0]["note_type"] == "Basic"
    assert "Front" in sink.calls[0]["fields"]


def test_unmatched_category_routes_to_the_pack_default_note_over_the_catch_all():
    # A pack default note (applies_to "*") takes precedence over the language-
    # neutral catch-all for any category no bespoke note claims.
    sink = FakeSink()
    verb = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ term }}"}, applies_to="verb"
    )
    default = NoteDefinition(
        name="Ankery DE: Word", field_map={"Word": "{{ term }}"}, applies_to="*"
    )
    builder = _builder(
        [FakeProvider("p", result=Entry(term="mit", source="t", category="preposition"))],
        sink,
        note_type="Basic",
        note_definitions=[default, verb],
    )

    builder.add_term("mit")

    assert sink.calls[0]["note_type"] == "Ankery DE: Word"
    assert sink.calls[0]["fields"] == {"Word": "mit"}


def test_bespoke_category_note_wins_over_the_pack_default_note():
    # The default note is fallback-only: a category with its own note still gets it,
    # regardless of the default note's position in the list.
    sink = FakeSink()
    default = NoteDefinition(
        name="Ankery DE: Word", field_map={"Word": "{{ term }}"}, applies_to="*"
    )
    verb = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ term }}"}, applies_to="verb"
    )
    builder = _builder(
        [FakeProvider("p", result=Entry(term="sehen", source="t", category="verb"))],
        sink,
        note_definitions=[default, verb],
    )

    builder.add_term("sehen")

    assert sink.calls[0]["note_type"] == "Ankery DE: Verb"
    assert sink.calls[0]["fields"] == {"Infinitive": "sehen"}


def test_verify_provisions_the_owned_catch_all_model():
    # When note_type still names the owned catch-all, routing writes into it, so
    # verify must provision it alongside the pack/bespoke notes.
    sink = FakeSink()
    verb = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ term }}"}, applies_to="verb"
    )
    builder = _builder(
        [FakeProvider("p", result=_entry())],
        sink,
        note_type="Ankery Basic",
        note_definitions=[verb],
    )

    builder.verify_note_types()

    names = [d.name for d in sink.verified["definitions"]]
    assert names == ["Ankery DE: Verb", "Ankery Basic"]
    assert sink.verified["catch_all"] == "Ankery Basic"


def test_verify_skips_owned_catch_all_when_note_type_points_at_a_foreign_model():
    # --note-type repointed the catch-all at a foreign model: we write into that
    # model (assumed to exist) and must not provision ours.
    sink = FakeSink()
    builder = _builder(
        [FakeProvider("p", result=_entry())], sink, note_type="Basic"
    )

    builder.verify_note_types()

    assert sink.verified["definitions"] == []
    assert sink.verified["catch_all"] == "Basic"


def test_preview_renders_without_writing_to_sink():
    sink = FakeSink()
    builder = _builder([FakeProvider("p", result=_entry())], sink, note_type="Basic")

    result = builder.preview("Buch")

    assert result.note_id is None
    assert result.note_type == "Basic"
    assert result.fields["Front"] == "Buch"
    assert sink.calls == []  # nothing written


def test_preview_returns_none_on_clean_miss():
    sink = FakeSink()
    builder = _builder([FakeProvider("p", result=None)], sink)

    assert builder.preview("Buch") is None
    assert sink.calls == []


def test_verify_note_types_returns_created_names():
    sink = FakeSink()
    sink.created_result = ["Ankery Basic"]
    builder = _builder([FakeProvider("p")], sink)

    assert builder.verify_note_types() == ["Ankery Basic"]


def test_lookup_logs_provider_attempts(caplog):
    caplog.set_level(logging.INFO, logger="ankery")
    first = FakeProvider("first", result=None)
    second = FakeProvider("second", result=_entry())

    _builder([first, second]).lookup("Buch")

    assert "provider 'first': miss" in caplog.text
    assert "provider 'second': resolved 'Buch'" in caplog.text


def test_unmatched_term_with_no_notes_routes_through_the_catch_all_terminus():
    # With no note definitions at all, routing falls straight through to the
    # engine-shipped catch-all note, written into the configured note_type.
    sink = FakeSink()
    builder = _builder(
        [FakeProvider("p", result=_entry())], sink, note_type="Basic"
    )

    builder.add_term("Buch")

    assert sink.calls[0]["note_type"] == "Basic"
    assert sink.calls[0]["fields"]["Front"] == "Buch"
