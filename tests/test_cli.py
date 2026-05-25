import pytest

from anki_deckbuilder import __main__ as cli
from anki_deckbuilder.config import Config, ConfigError
from anki_deckbuilder.providers.base import ProviderError
from anki_deckbuilder.sinks.base import SinkError


class FakeBuilder:
    """Captures add_word calls and replays a scripted result per word."""

    def __init__(self, results: dict[str, object]):
        self._results = results
        self.calls: list[dict] = []

    def add_word(self, word, *, source_language, target_language):
        self.calls.append(
            {"word": word, "source": source_language, "target": target_language}
        )
        outcome = self._results[word]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def patched(monkeypatch):
    """Patch build_deck_builder; return (captured_config, set_results)."""
    captured: dict[str, object] = {}

    def factory(results):
        builder = FakeBuilder(results)

        def fake_build(config, *, map_fields=None):
            captured["config"] = config
            captured["builder"] = builder
            return builder

        monkeypatch.setattr(cli, "build_deck_builder", fake_build)
        # Keep env out of the picture so defaults are predictable.
        monkeypatch.setattr(Config, "from_env", classmethod(lambda cls, *a, **k: cls()))
        return builder

    return captured, factory


def test_added_word_prints_note_id_and_exits_zero(patched, capsys):
    captured, set_results = patched
    set_results({"Buch": 42})

    code = cli.main(["Buch"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Buch: added (note 42)" in out


def test_not_found_reports_and_exits_nonzero(patched, capsys):
    captured, set_results = patched
    set_results({"Xyz": None})

    code = cli.main(["Xyz"])

    assert code == 1
    assert "Xyz: not found" in capsys.readouterr().err


def test_provider_error_reports_and_exits_nonzero(patched, capsys):
    captured, set_results = patched
    set_results({"Buch": ProviderError("llm down")})

    code = cli.main(["Buch"])

    assert code == 1
    assert "lookup failed: llm down" in capsys.readouterr().err


def test_sink_error_reports_and_exits_nonzero(patched, capsys):
    captured, set_results = patched
    set_results({"Buch": SinkError("anki offline")})

    code = cli.main(["Buch"])

    assert code == 1
    assert "could not add note: anki offline" in capsys.readouterr().err


def test_multiple_words_one_failure_still_processes_all(patched, capsys):
    captured, set_results = patched
    set_results({"a": 1, "b": None, "c": 2})

    code = cli.main(["a", "b", "c"])

    assert code == 1  # one miss
    assert [c["word"] for c in captured["builder"].calls] == ["a", "b", "c"]


def test_flags_override_config(patched):
    captured, set_results = patched
    set_results({"Buch": 1})

    cli.main(
        [
            "--deck", "German::Verbs",
            "--source-lang", "de",
            "--target-lang", "ru",
            "--note-type", "Cloze",
            "--allow-duplicate",
            "Buch",
        ]
    )

    config = captured["config"]
    assert config.deck == "German::Verbs"
    assert config.target_language == "ru"
    assert config.note_type == "Cloze"
    assert config.allow_duplicate is True


def test_languages_passed_through_to_builder(patched):
    captured, set_results = patched
    set_results({"Buch": 1})

    cli.main(["--source-lang", "de", "--target-lang", "en", "Buch"])

    call = captured["builder"].calls[0]
    assert call["source"] == "de"
    assert call["target"] == "en"


def test_config_error_reports_and_exits_two(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise ConfigError("unknown config keys: dekc")

    monkeypatch.setattr(Config, "load", classmethod(lambda cls, **k: boom()))

    code = cli.main(["Buch"])

    assert code == 2  # distinct from the 1 used for per-word failures
    assert "config error: unknown config keys: dekc" in capsys.readouterr().err


def test_missing_word_argument_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    assert exc.value.code == 2  # argparse's usage-error exit code
    assert "the following arguments are required: words" in capsys.readouterr().err
