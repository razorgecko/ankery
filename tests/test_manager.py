import pytest

from ankery.manager import DeckBuilder
from ankery.models import WordInfo
from ankery.notedef import NoteDefinition, default_field_map
from ankery.providers.base import ProviderError


class FakeProvider:
    """A provider whose fetch behavior is scripted per test."""

    def __init__(self, name: str, *, result: WordInfo | None = None, error: Exception | None = None):
        self.name = name
        self._result = result
        self._error = error
        self.calls = 0
        self.last_category_hint: str | None = None

    def fetch(self, word: str, category_hint: str | None = None) -> WordInfo | None:
        self.calls += 1
        self.last_category_hint = category_hint
        if self._error is not None:
            raise self._error
        return self._result


class FakeSink:
    """Records the single add_note call and returns a fixed note id."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add_note(self, *, deck, note_type, fields, tags=None) -> int:
        self.calls.append(
            {"deck": deck, "note_type": note_type, "fields": fields, "tags": tags}
        )
        return 42


def _info(word: str = "Buch") -> WordInfo:
    return WordInfo(word=word, source="test")


def _info_with_category(category: str, word: str = "Bank") -> WordInfo:
    return WordInfo(word=word, source="test", category=category)


def _builder(providers, sink=None, **kwargs) -> DeckBuilder:
    sink = sink or FakeSink()
    kwargs.setdefault("deck", "German")
    kwargs.setdefault("note_type", "Basic")
    kwargs.setdefault("style_css", "")
    return DeckBuilder(providers, sink, **kwargs)


def test_first_provider_with_result_wins_and_short_circuits():
    first = FakeProvider("first", result=_info())
    second = FakeProvider("second", result=_info("other"))
    info = _builder([first, second]).lookup("Buch")

    assert info.word == "Buch"
    assert second.calls == 0


def test_clean_miss_falls_through_to_next_provider():
    first = FakeProvider("first", result=None)
    second = FakeProvider("second", result=_info())
    info = _builder([first, second]).lookup("Buch")

    assert info.word == "Buch"
    assert first.calls == 1


def test_provider_error_falls_through_to_next_provider():
    first = FakeProvider("first", error=ProviderError("boom"))
    second = FakeProvider("second", result=_info())
    info = _builder([first, second]).lookup("Buch")

    assert info.word == "Buch"
    assert first.calls == 1


def test_category_hint_is_forwarded_to_each_provider():
    first = FakeProvider("first", result=None)
    second = FakeProvider("second", result=_info())
    _builder([first, second]).lookup("Bank", category_hint="noun")

    assert first.last_category_hint == "noun"
    assert second.last_category_hint == "noun"


def test_category_hint_overrides_the_providers_classification():
    # The user's explicit hint is authoritative: it wins over whatever category the
    # provider stamped, so routing and the pack filter see the intended category.
    provider = FakeProvider("p", result=_info_with_category("verb"))
    info = _builder([provider]).lookup("Bank", category_hint="noun")

    assert info.category == "noun"


def test_no_category_hint_leaves_the_providers_classification_intact():
    provider = FakeProvider("p", result=_info_with_category("verb"))
    info = _builder([provider]).lookup("laufen")

    assert info.category == "verb"


def test_all_clean_misses_returns_none():
    providers = [FakeProvider("a", result=None), FakeProvider("b", result=None)]
    info = _builder(providers).lookup("Buch")

    assert info is None


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
    second = FakeProvider("second", result=_info())
    info = _builder([first, second]).lookup("Buch")

    assert info.word == "Buch"
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
    # routing. Here it uppercases the word to prove it is in the path.
    def shout(info: WordInfo) -> WordInfo:
        info.word = info.word.upper()
        return info

    info = _builder([FakeProvider("p", result=_info())], normalize=shout).lookup("Buch")
    assert info.word == "BUCH"


def test_normalize_hook_failure_surfaces_as_provider_error():
    def boom(info: WordInfo) -> WordInfo:
        raise RuntimeError("pack bug")

    builder = _builder([FakeProvider("p", result=_info())], normalize=boom)
    with pytest.raises(ProviderError, match="normalize hook failed"):
        builder.lookup("Buch")


def test_add_word_maps_fields_and_writes_to_sink():
    sink = FakeSink()
    builder = _builder(
        [FakeProvider("p", result=_info())], sink, deck="German", note_type="Basic", tags=["auto"]
    )

    result = builder.add_word("Buch")

    assert result == (42, "Buch")
    assert len(sink.calls) == 1
    call = sink.calls[0]
    assert call["deck"] == "German"
    assert call["note_type"] == "Basic"
    assert call["tags"] == ["auto"]
    assert call["fields"]["Front"] == "Buch"


def test_add_word_returns_none_and_skips_sink_on_total_miss():
    sink = FakeSink()
    builder = _builder([FakeProvider("p", result=None)], sink)

    result = builder.add_word("Buch")

    assert result is None
    assert sink.calls == []


def test_custom_field_map_is_used():
    sink = FakeSink()
    builder = _builder(
        [FakeProvider("p", result=_info())], sink, map_fields=lambda info: {"Word": info.word}
    )

    builder.add_word("Buch")

    assert sink.calls[0]["fields"] == {"Word": "Buch"}


def test_matching_note_definition_picks_its_note_type_and_map():
    sink = FakeSink()
    note_def = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ word }}"}, applies_to="verb"
    )
    builder = _builder(
        [FakeProvider("p", result=WordInfo(word="sehen", source="t", category="verb"))],
        sink,
        note_definitions=[note_def],
    )

    builder.add_word("sehen")

    assert sink.calls[0]["note_type"] == "Ankery DE: Verb"
    assert sink.calls[0]["fields"] == {"Infinitive": "sehen"}


def test_word_matching_no_note_definition_falls_back_to_default_note_type():
    sink = FakeSink()
    note_def = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ word }}"}, applies_to="verb"
    )
    builder = _builder(
        [FakeProvider("p", result=WordInfo(word="schön", source="t", category="adjective"))],
        sink,
        note_type="Basic",
        note_definitions=[note_def],
    )

    builder.add_word("schön")

    assert sink.calls[0]["note_type"] == "Basic"
    assert "Front" in sink.calls[0]["fields"]


def test_unmatched_category_routes_to_the_pack_default_note_over_the_catch_all():
    # A pack default note (applies_to "*") takes precedence over the language-
    # neutral catch-all for any category no bespoke note claims.
    sink = FakeSink()
    verb = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ word }}"}, applies_to="verb"
    )
    default = NoteDefinition(
        name="Ankery DE: Word", field_map={"Word": "{{ word }}"}, applies_to="*"
    )
    builder = _builder(
        [FakeProvider("p", result=WordInfo(word="mit", source="t", category="preposition"))],
        sink,
        note_type="Basic",
        note_definitions=[default, verb],
    )

    builder.add_word("mit")

    assert sink.calls[0]["note_type"] == "Ankery DE: Word"
    assert sink.calls[0]["fields"] == {"Word": "mit"}


def test_bespoke_category_note_wins_over_the_pack_default_note():
    # The default note is fallback-only: a category with its own note still gets it,
    # regardless of the default note's position in the list.
    sink = FakeSink()
    default = NoteDefinition(
        name="Ankery DE: Word", field_map={"Word": "{{ word }}"}, applies_to="*"
    )
    verb = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ word }}"}, applies_to="verb"
    )
    builder = _builder(
        [FakeProvider("p", result=WordInfo(word="sehen", source="t", category="verb"))],
        sink,
        note_definitions=[default, verb],
    )

    builder.add_word("sehen")

    assert sink.calls[0]["note_type"] == "Ankery DE: Verb"
    assert sink.calls[0]["fields"] == {"Infinitive": "sehen"}


def test_default_field_map_is_language_neutral():
    # The catch-all names no language: the front is the bare word (no article),
    # and every declared feature shows in the back as key: value.
    info = WordInfo(
        word="Buch",
        source="test",
        translations=["book"],
        definitions=["gebundene Seiten"],
        features={"gender": "das", "nominative_pl": "Bücher"},
    )
    fields = default_field_map(info)

    assert fields["Front"] == "Buch"
    assert "book" in fields["Back"]
    assert "gender: das" in fields["Back"]
    assert "nominative_pl: Bücher" in fields["Back"]
