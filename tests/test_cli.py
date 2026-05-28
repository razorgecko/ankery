from pathlib import Path

import pytest

from ankery import __main__ as cli
from ankery.config import Config, ConfigError
from ankery.manager import AddResult
from ankery.providers.base import ProviderError
from ankery.sinks.base import SinkError


class FakeBuilder:
    """Captures add_word calls and replays a scripted result per word."""

    def __init__(self, results: dict[str, object]):
        self._results = results
        self.calls: list[str] = []
        self.verified = False
        self.verify_error: Exception | None = None

    def verify_note_types(self):
        if self.verify_error is not None:
            raise self.verify_error
        self.verified = True

    def add_word(self, word):
        self.calls.append(word)
        outcome = self._results[word]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def patched(monkeypatch):
    """Patch build_deck_builder; return (captured, set_results)."""
    captured: dict[str, object] = {}

    def factory(results):
        builder = FakeBuilder(results)

        def fake_build(config):
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
    set_results({"Buch": AddResult(note_id=42, word="Buch")})

    code = cli.main(["Buch"])

    assert code == 0
    assert "Buch: added (note 42)" in capsys.readouterr().out


def test_resolved_word_shows_redirect(patched, capsys):
    captured, set_results = patched
    set_results({"Hause": AddResult(note_id=42, word="Haus")})

    code = cli.main(["Hause"])

    assert code == 0
    assert "Hause -> Haus: added (note 42)" in capsys.readouterr().out


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


def test_note_type_verification_failure_exits_before_words(patched, capsys):
    captured, set_results = patched
    builder = set_results({"Buch": AddResult(note_id=1, word="Buch")})
    builder.verify_error = SinkError("note type 'Noun (DE)' already exists")

    code = cli.main(["Buch"])

    assert code == 1
    assert "note type setup failed:" in capsys.readouterr().err
    assert builder.calls == []  # no words processed once verification fails


def test_multiple_words_one_failure_still_processes_all(patched, capsys):
    captured, set_results = patched
    set_results(
        {
            "a": AddResult(note_id=1, word="a"),
            "b": None,
            "c": AddResult(note_id=2, word="c"),
        }
    )

    code = cli.main(["a", "b", "c"])

    assert code == 1  # one miss
    assert captured["builder"].calls == ["a", "b", "c"]


def test_empty_or_whitespace_word_is_skipped_not_looked_up(patched, capsys):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})

    code = cli.main(["   ", "Buch"])

    assert code == 1  # the empty word marks the run as failed
    assert "skipping empty word" in capsys.readouterr().err
    assert captured["builder"].calls == ["Buch"]  # whitespace word never reached the builder


def test_surrounding_whitespace_is_stripped_before_lookup(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})

    code = cli.main(["  Buch  "])

    assert code == 0
    assert captured["builder"].calls == ["Buch"]


def test_flags_override_config(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})

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
    assert config.source_language == "de"
    assert config.target_language == "ru"
    assert config.note_type == "Cloze"
    assert config.allow_duplicate is True


def test_infra_flags_override_config(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})

    cli.main(
        [
            "--llm-url", "https://api.groq.com/openai/v1",
            "--llm-model", "llama-3.3-70b",
            "--anki-url", "http://anki.local:8765",
            "Buch",
        ]
    )

    config = captured["config"]
    assert config.llm_base_url == "https://api.groq.com/openai/v1"
    assert config.llm_model == "llama-3.3-70b"
    assert config.anki_url == "http://anki.local:8765"


def test_provider_flag_overrides_chain(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})

    cli.main(["--provider", "verbformen,llm", "Buch"])

    assert captured["config"].providers == ("verbformen", "llm")


def test_langs_dir_flag_sets_user_pack_dir(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})

    cli.main(["--langs-dir", "/srv/packs", "Buch"])

    assert captured["config"].langs_dir == Path("/srv/packs")


def test_notes_dir_flag_sets_notes_dir(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})

    cli.main(["--notes-dir", "/srv/notes", "Buch"])

    assert captured["config"].notes_dir == Path("/srv/notes")


def test_config_error_reports_and_exits_two(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise ConfigError("unknown config keys: dekc")

    monkeypatch.setattr(Config, "load", classmethod(lambda cls, **k: boom()))

    code = cli.main(["Buch"])

    assert code == 2  # distinct from the 1 used for per-word failures
    assert "config error: unknown config keys: dekc" in capsys.readouterr().err


def test_pack_error_at_wiring_reports_and_exits_two(patched, monkeypatch, capsys):
    # A bad source_language surfaces from build_deck_builder as ConfigError; the
    # CLI catches it and exits 2, like any other config problem.
    captured, set_results = patched

    def boom(config):
        raise ConfigError("no language pack for 'zz'")

    monkeypatch.setattr(cli, "build_deck_builder", boom)

    code = cli.main(["Buch"])

    assert code == 2
    assert "config error: no language pack for 'zz'" in capsys.readouterr().err


def _capture_load_path(monkeypatch) -> dict:
    """Patch Config.load to record the paths it was called with."""
    seen: dict = {}

    def fake_load(cls, *, path=None, auth_path=None, **kwargs):
        seen["path"] = path
        seen["auth_path"] = auth_path
        return cls()

    monkeypatch.setattr(Config, "load", classmethod(fake_load))
    return seen


def test_config_flag_sets_load_path(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})
    seen = _capture_load_path(monkeypatch)

    cli.main(["--config", "/tmp/custom.toml", "Buch"])

    assert seen["path"] == Path("/tmp/custom.toml")


def test_config_flag_passed_through_even_with_env_set(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})
    seen = _capture_load_path(monkeypatch)
    monkeypatch.setenv("ANKERY_CONFIG", "/tmp/from-env.toml")

    cli.main(["--config", "/tmp/from-flag.toml", "Buch"])

    assert seen["path"] == Path("/tmp/from-flag.toml")


def test_no_config_source_loads_default_path(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})
    seen = _capture_load_path(monkeypatch)
    monkeypatch.delenv("ANKERY_CONFIG", raising=False)

    cli.main(["Buch"])

    assert seen["path"] is None  # Config.load falls back to its default path


def test_auth_flag_sets_auth_path(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})
    seen = _capture_load_path(monkeypatch)

    cli.main(["--auth", "/tmp/custom-auth.toml", "Buch"])

    assert seen["auth_path"] == Path("/tmp/custom-auth.toml")


def test_auth_flag_passed_through_even_with_env_set(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})
    seen = _capture_load_path(monkeypatch)
    monkeypatch.setenv("ANKERY_AUTH", "/tmp/auth-from-env.toml")

    cli.main(["--auth", "/tmp/auth-from-flag.toml", "Buch"])

    assert seen["auth_path"] == Path("/tmp/auth-from-flag.toml")


def test_no_auth_source_loads_default_path(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, word="Buch")})
    seen = _capture_load_path(monkeypatch)
    monkeypatch.delenv("ANKERY_AUTH", raising=False)

    cli.main(["Buch"])

    assert seen["auth_path"] is None  # Config.load falls back to its default path


def test_missing_word_argument_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    assert exc.value.code == 2  # argparse's usage-error exit code
    assert "the following arguments are required: words" in capsys.readouterr().err
