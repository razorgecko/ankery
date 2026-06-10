import logging
from pathlib import Path

import pytest

from ankery import __main__ as cli
from ankery.config import Config, ConfigError
from ankery.manager import AddResult
from ankery.providers.base import ProviderError
from ankery.sinks.base import SinkError


class FakeBuilder:
    """Captures add_term calls and replays a scripted result per term."""

    def __init__(self, results: dict[str, object], category_names=("noun", "verb", "adjective")):
        self._results = results
        self.calls: list[str] = []
        self.hint_calls: list[tuple[str, str | None]] = []
        self.category_names = list(category_names)
        self.verified = False
        self.verify_error: Exception | None = None
        self.created: list[str] = []
        self.preview_calls: list[tuple[str, str | None]] = []

    def verify_note_types(self):
        if self.verify_error is not None:
            raise self.verify_error
        self.verified = True
        return self.created

    def add_term(self, term, *, category_hint=None):
        self.calls.append(term)
        self.hint_calls.append((term, category_hint))
        return self._outcome(term)

    def preview(self, term, *, category_hint=None):
        self.preview_calls.append((term, category_hint))
        return self._outcome(term)

    def _outcome(self, term):
        outcome = self._results[term]
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


# ---------------------------------------------------------------------------
# Category-hint parsing and resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Buch", ("Buch", None)),
        ("schnell:adj", ("schnell", "adj")),
        ("  Haus : noun ", ("Haus", "noun")),
        (":noun", ("", "noun")),
        ("auf:", ("auf", "")),
    ],
)
def test_split_category_hint(raw, expected):
    assert cli.split_category_hint(raw) == expected


def test_resolve_category_hint_exact_and_prefix():
    names = ["adjective", "adverb", "noun", "preposition", "verb"]
    assert cli.resolve_category_hint("noun", names) == "noun"  # exact
    assert cli.resolve_category_hint("v", names) == "verb"  # unique prefix
    assert cli.resolve_category_hint("adj", names) == "adjective"
    assert cli.resolve_category_hint("PREP", names) == "preposition"  # case-insensitive


def test_resolve_category_hint_rejects_unknown():
    with pytest.raises(ValueError, match="unknown category"):
        cli.resolve_category_hint("xyz", ["noun", "verb"])


def test_resolve_category_hint_rejects_ambiguous_prefix():
    with pytest.raises(ValueError, match="ambiguous"):
        cli.resolve_category_hint("ad", ["adjective", "adverb"])


def test_resolve_category_hint_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        cli.resolve_category_hint("", ["noun"])


def test_colon_hint_is_resolved_and_passed_to_add_term(patched):
    captured, set_results = patched
    builder = set_results({"Bank": AddResult(note_id=7, term="Bank")})

    code = cli.main(["Bank:n"])

    assert code == 0
    assert builder.hint_calls == [("Bank", "noun")]


def test_unknown_hint_reports_and_skips_lookup(patched, capsys):
    captured, set_results = patched
    builder = set_results({})

    code = cli.main(["schnell:xyz"])

    assert code == 1
    assert "unknown category" in capsys.readouterr().err
    assert builder.calls == []  # the word is never looked up


def test_word_without_colon_passes_no_hint(patched):
    captured, set_results = patched
    builder = set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["Buch"])

    assert builder.hint_calls == [("Buch", None)]


def test_added_word_prints_without_note_id_and_exits_zero(patched, capsys):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=42, term="Buch")})

    code = cli.main(["Buch"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Buch: added" in out
    assert "42" not in out  # the note id is -v territory


def test_resolved_word_shows_redirect(patched, capsys):
    captured, set_results = patched
    set_results({"Hause": AddResult(note_id=42, term="Haus")})

    code = cli.main(["Hause"])

    assert code == 0
    assert "Hause -> Haus: added" in capsys.readouterr().out


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
    builder = set_results({"Buch": AddResult(note_id=1, term="Buch")})
    builder.verify_error = SinkError("note type 'Ankery DE: Noun' already exists")

    code = cli.main(["Buch"])

    assert code == 1
    assert "note type setup failed:" in capsys.readouterr().err
    assert builder.calls == []  # no words processed once verification fails


def test_multiple_words_one_failure_still_processes_all(patched, capsys):
    captured, set_results = patched
    set_results(
        {
            "a": AddResult(note_id=1, term="a"),
            "b": None,
            "c": AddResult(note_id=2, term="c"),
        }
    )

    code = cli.main(["a", "b", "c"])

    assert code == 1  # one miss
    assert captured["builder"].calls == ["a", "b", "c"]


def test_empty_or_whitespace_word_is_skipped_not_looked_up(patched, capsys):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    code = cli.main(["   ", "Buch"])

    assert code == 1  # the empty word marks the run as failed
    assert "empty term" in capsys.readouterr().err
    assert captured["builder"].calls == ["Buch"]  # whitespace word never reached the builder


def test_surrounding_whitespace_is_stripped_before_lookup(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    code = cli.main(["  Buch  "])

    assert code == 0
    assert captured["builder"].calls == ["Buch"]


def test_flags_override_config(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(
        [
            "--deck", "German::Verbs",
            "--pack", "de",
            "--var", "target_language=ru",
            "--note-type", "Cloze",
            "--allow-duplicate",
            "Buch",
        ]
    )

    config = captured["config"]
    assert config.deck == "German::Verbs"
    assert config.pack == "de"
    assert config.variables == {"target_language": "ru"}
    assert config.note_type == "Cloze"
    assert config.allow_duplicate is True


def test_pack_flag_is_taken_literally(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["--pack", "German", "Buch"])

    # The pack selector is kept as given: --pack German is carried through as
    # `German`, not rewritten to `de`.
    assert captured["config"].pack == "German"


def test_var_flag_is_repeatable_and_passes_values_raw(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["--var", "target_language=english", "--var", "tone=formal", "Buch"])

    # Values are carried through verbatim; repeated flags accumulate.
    assert captured["config"].variables == {
        "target_language": "english",
        "tone": "formal",
    }


def test_var_flag_without_equals_is_a_config_error(capsys):
    code = cli.main(["--var", "noequals", "Buch"])

    assert code == 2
    assert "--var expects KEY=VALUE" in capsys.readouterr().err


def test_infra_flags_override_config(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

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
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["--provider", "netzverb,llm", "Buch"])

    assert captured["config"].providers == ("netzverb", "llm")


def test_packs_dir_flag_sets_user_pack_dir(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["--packs-dir", "/srv/packs", "Buch"])

    assert captured["config"].packs_dir == Path("/srv/packs")


def test_notes_dir_flag_sets_notes_dir(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["--notes-dir", "/srv/notes", "Buch"])

    assert captured["config"].notes_dir == Path("/srv/notes")


def test_config_error_reports_and_exits_two(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise ConfigError("unknown config keys: dekc")

    monkeypatch.setattr(Config, "load", classmethod(lambda cls, **k: boom()))

    code = cli.main(["Buch"])

    assert code == 2  # distinct from the 1 used for per-word failures
    assert "ankery: error: unknown config keys: dekc" in capsys.readouterr().err


def test_pack_error_at_wiring_reports_and_exits_two(patched, monkeypatch, capsys):
    # A bad pack surfaces from build_deck_builder as ConfigError; the
    # CLI catches it and exits 2, like any other config problem.
    captured, set_results = patched

    def boom(config):
        raise ConfigError("no pack for 'zz'")

    monkeypatch.setattr(cli, "build_deck_builder", boom)

    code = cli.main(["Buch"])

    assert code == 2
    assert "ankery: error: no pack for 'zz'" in capsys.readouterr().err


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
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    seen = _capture_load_path(monkeypatch)

    cli.main(["--config", "/tmp/custom.toml", "Buch"])

    assert seen["path"] == Path("/tmp/custom.toml")


def test_config_flag_passed_through_even_with_env_set(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    seen = _capture_load_path(monkeypatch)
    monkeypatch.setenv("ANKERY_CONFIG", "/tmp/from-env.toml")

    cli.main(["--config", "/tmp/from-flag.toml", "Buch"])

    assert seen["path"] == Path("/tmp/from-flag.toml")


def test_no_config_source_loads_default_path(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    seen = _capture_load_path(monkeypatch)
    monkeypatch.delenv("ANKERY_CONFIG", raising=False)

    cli.main(["Buch"])

    assert seen["path"] is None  # Config.load falls back to its default path


def test_auth_flag_sets_auth_path(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    seen = _capture_load_path(monkeypatch)

    cli.main(["--auth", "/tmp/custom-auth.toml", "Buch"])

    assert seen["auth_path"] == Path("/tmp/custom-auth.toml")


def test_auth_flag_passed_through_even_with_env_set(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    seen = _capture_load_path(monkeypatch)
    monkeypatch.setenv("ANKERY_AUTH", "/tmp/auth-from-env.toml")

    cli.main(["--auth", "/tmp/auth-from-flag.toml", "Buch"])

    assert seen["auth_path"] == Path("/tmp/auth-from-flag.toml")


def test_no_auth_source_loads_default_path(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    seen = _capture_load_path(monkeypatch)
    monkeypatch.delenv("ANKERY_AUTH", raising=False)

    cli.main(["Buch"])

    assert seen["auth_path"] is None  # Config.load falls back to its default path


# ---------------------------------------------------------------------------
# Verbosity levels
# ---------------------------------------------------------------------------


def test_quiet_suppresses_stdout_but_keeps_exit_code(patched, capsys):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    code = cli.main(["-q", "Buch"])

    assert code == 0
    assert capsys.readouterr().out == ""


def test_quiet_keeps_errors_on_stderr(patched, capsys):
    captured, set_results = patched
    set_results({"Xyz": None})

    code = cli.main(["-q", "Xyz"])

    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert "Xyz: not found" in err


def test_verbose_prints_note_id_type_and_fields(patched, capsys):
    captured, set_results = patched
    set_results(
        {
            "Buch": AddResult(
                note_id=42,
                term="Buch",
                note_type="Ankery DE: Noun",
                fields={"Word": "Buch", "Article": ""},
            )
        }
    )

    code = cli.main(["-v", "Buch"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Buch: added (note 42, Ankery DE: Noun)" in out
    assert "  Word: Buch" in out
    assert "  Article: " in out  # empty fields stay visible, that's the point


def test_created_note_types_are_announced(patched, capsys):
    captured, set_results = patched
    builder = set_results({"Buch": AddResult(note_id=1, term="Buch")})
    builder.created = ["Ankery Basic"]

    cli.main(["Buch"])

    assert "created note type: Ankery Basic" in capsys.readouterr().out


def test_quiet_suppresses_created_note_types(patched, capsys):
    captured, set_results = patched
    builder = set_results({"Buch": AddResult(note_id=1, term="Buch")})
    builder.created = ["Ankery Basic"]

    cli.main(["-q", "Buch"])

    assert capsys.readouterr().out == ""


def test_double_verbose_installs_the_engine_trace_once(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    log = logging.getLogger("ankery")

    try:
        cli.main(["-vv", "Buch"])
        cli.main(["-vv", "Buch"])  # repeated runs must not stack handlers

        handlers = [h for h in log.handlers if getattr(h, "_ankery_trace", False)]
        assert len(handlers) == 1
        assert log.level == logging.DEBUG
    finally:
        for handler in list(log.handlers):
            if getattr(handler, "_ankery_trace", False):
                log.removeHandler(handler)
        log.setLevel(logging.NOTSET)


def test_quiet_and_verbose_are_mutually_exclusive(patched, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["-q", "-v", "Buch"])

    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def test_dry_run_previews_without_touching_anki(patched, capsys):
    captured, set_results = patched
    builder = set_results(
        {
            "Buch": AddResult(
                note_id=None,
                term="Buch",
                note_type="Ankery DE: Noun",
                fields={"Word": "Buch", "Article": "das"},
            )
        }
    )

    code = cli.main(["--dry-run", "Buch"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Buch: would add (Ankery DE: Noun)" in out  # -v output forced
    assert "  Word: Buch" in out
    assert "  Article: das" in out
    assert builder.preview_calls == [("Buch", None)]
    assert builder.calls == []  # add_term never invoked
    assert builder.verified is False  # note type provisioning skipped


def test_dry_run_not_found_still_errors(patched, capsys):
    captured, set_results = patched
    set_results({"Xyz": None})

    code = cli.main(["--dry-run", "Xyz"])

    assert code == 1
    assert "Xyz: not found" in capsys.readouterr().err


def test_quiet_dry_run_prints_nothing(patched, capsys):
    captured, set_results = patched
    set_results(
        {"Buch": AddResult(note_id=None, term="Buch", note_type="N", fields={"W": "x"})}
    )

    code = cli.main(["-q", "-n", "Buch"])

    assert code == 0
    assert capsys.readouterr().out == ""  # explicit -q beats the implied -v


def test_missing_word_argument_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    assert exc.value.code == 2  # argparse's usage-error exit code
    assert "the following arguments are required: terms" in capsys.readouterr().err
