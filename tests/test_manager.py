import pytest

from ankery.manager import DeckBuilder, default_field_map
from ankery.models import WordInfo
from ankery.providers.base import ProviderError
from ankery.recipes import NoteRecipe


class FakeProvider:
    """A provider whose fetch behavior is scripted per test."""

    def __init__(self, name: str, *, result: WordInfo | None = None, error: Exception | None = None):
        self.name = name
        self._result = result
        self._error = error
        self.calls = 0

    def fetch(self, word: str, *, source_language: str, target_language: str) -> WordInfo | None:
        self.calls += 1
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


def _builder(providers, sink=None, **kwargs) -> DeckBuilder:
    sink = sink or FakeSink()
    kwargs.setdefault("deck", "German")
    kwargs.setdefault("note_type", "Basic")
    return DeckBuilder(providers, sink, **kwargs)


def test_first_provider_with_result_wins_and_short_circuits():
    first = FakeProvider("first", result=_info())
    second = FakeProvider("second", result=_info("other"))
    info = _builder([first, second]).lookup("Buch", source_language="de", target_language="en")

    assert info.word == "Buch"
    assert second.calls == 0


def test_clean_miss_falls_through_to_next_provider():
    first = FakeProvider("first", result=None)
    second = FakeProvider("second", result=_info())
    info = _builder([first, second]).lookup("Buch", source_language="de", target_language="en")

    assert info.word == "Buch"
    assert first.calls == 1


def test_provider_error_falls_through_to_next_provider():
    first = FakeProvider("first", error=ProviderError("boom"))
    second = FakeProvider("second", result=_info())
    info = _builder([first, second]).lookup("Buch", source_language="de", target_language="en")

    assert info.word == "Buch"
    assert first.calls == 1


def test_all_clean_misses_returns_none():
    providers = [FakeProvider("a", result=None), FakeProvider("b", result=None)]
    info = _builder(providers).lookup("Buch", source_language="de", target_language="en")

    assert info is None


def test_exhausted_chain_with_errors_reraises_last_error():
    providers = [
        FakeProvider("a", error=ProviderError("first failure")),
        FakeProvider("b", error=ProviderError("second failure")),
    ]
    with pytest.raises(ProviderError, match="second failure"):
        _builder(providers).lookup("Buch", source_language="de", target_language="en")


def test_error_then_clean_miss_still_reraises():
    # A clean miss after a hard failure must not mask the failure.
    providers = [
        FakeProvider("a", error=ProviderError("boom")),
        FakeProvider("b", result=None),
    ]
    with pytest.raises(ProviderError, match="boom"):
        _builder(providers).lookup("Buch", source_language="de", target_language="en")


def test_add_word_maps_fields_and_writes_to_sink():
    sink = FakeSink()
    builder = _builder(
        [FakeProvider("p", result=_info())],
        sink,
        deck="German",
        note_type="Basic",
        tags=["auto"],
    )

    note_id = builder.add_word("Buch", source_language="de", target_language="en")

    assert note_id == 42
    assert len(sink.calls) == 1
    call = sink.calls[0]
    assert call["deck"] == "German"
    assert call["note_type"] == "Basic"
    assert call["tags"] == ["auto"]
    assert call["fields"]["Front"] == "Buch"


def test_add_word_returns_none_and_skips_sink_on_total_miss():
    sink = FakeSink()
    builder = _builder([FakeProvider("p", result=None)], sink)

    note_id = builder.add_word("Buch", source_language="de", target_language="en")

    assert note_id is None
    assert sink.calls == []


def test_custom_field_map_is_used():
    sink = FakeSink()
    builder = _builder(
        [FakeProvider("p", result=_info())],
        sink,
        map_fields=lambda info: {"Word": info.word},
    )

    builder.add_word("Buch", source_language="de", target_language="en")

    assert sink.calls[0]["fields"] == {"Word": "Buch"}


def test_matching_recipe_picks_its_note_type_and_map():
    sink = FakeSink()
    recipe = NoteRecipe(
        note_type="Verb (DE)",
        map_fields=lambda info: {"Infinitive": info.word},
        applies_to=lambda info: info.part_of_speech == "verb",
    )
    builder = _builder(
        [FakeProvider("p", result=WordInfo(word="sehen", source="t", part_of_speech="verb"))],
        sink,
        recipes=[recipe],
    )

    builder.add_word("sehen", source_language="de", target_language="en")

    assert sink.calls[0]["note_type"] == "Verb (DE)"
    assert sink.calls[0]["fields"] == {"Infinitive": "sehen"}


def test_word_matching_no_recipe_falls_back_to_default_note_type():
    sink = FakeSink()
    recipe = NoteRecipe(
        note_type="Verb (DE)",
        map_fields=lambda info: {"Infinitive": info.word},
        applies_to=lambda info: info.part_of_speech == "verb",
    )
    builder = _builder(
        [FakeProvider("p", result=WordInfo(word="schön", source="t", part_of_speech="adjective"))],
        sink,
        note_type="Basic",
        recipes=[recipe],
    )

    builder.add_word("schön", source_language="de", target_language="en")

    assert sink.calls[0]["note_type"] == "Basic"
    assert "Front" in sink.calls[0]["fields"]


def test_default_field_map_prefixes_noun_with_article():
    info = WordInfo(
        word="Buch",
        source="test",
        gender="das",
        translations=["book"],
        definitions=["gebundene Seiten"],
        inflections={"plural": "Bücher"},
    )
    fields = default_field_map(info)

    assert fields["Front"] == "das Buch"
    assert "book" in fields["Back"]
    assert "plural: Bücher" in fields["Back"]
