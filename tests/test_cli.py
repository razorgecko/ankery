import base64
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

from ankery import __main__ as cli
from ankery import signin
from ankery.config import Config, ConfigError
from ankery.manager import AddResult
from ankery.providers.base import ProviderError
from ankery.sinks.base import SinkError, SyncResult


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
    captured: dict[str, object] = {"collection_syncs": 0}

    def fake_sync_collection(config):
        captured["collection_syncs"] += 1
        if "sync_error" in captured:
            raise captured["sync_error"]

    def factory(results):
        builder = FakeBuilder(results)

        def fake_build(config):
            captured["config"] = config
            captured["builder"] = builder
            return builder

        monkeypatch.setattr(cli, "build_deck_builder", fake_build)
        monkeypatch.setattr(cli, "sync_collection", fake_sync_collection)
        # Keep config files and env out of the picture so defaults are predictable.
        monkeypatch.setattr(Config, "load", classmethod(lambda cls, *a, **k: cls()))
        return builder

    return captured, factory


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


def test_var_flag_replaces_config_variables(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    loaded = Config(variables={"target_language": "ru", "tone": "formal"})
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *a, **k: loaded))

    cli.main(["--var", "tone=casual", "Buch"])

    # target_language from config.toml is dropped, not merged.
    assert captured["config"].variables == {"tone": "casual"}


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


def test_llm_backend_flag_overrides_config(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["--llm-backend", "chatgpt", "Buch"])

    assert captured["config"].llm_backend == "chatgpt"


def test_llm_backend_flag_rejects_an_unknown_backend(patched, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--llm-backend", "chatgtp", "Buch"])

    assert exc.value.code == 2
    assert "invalid choice: 'chatgtp'" in capsys.readouterr().err


def test_provider_flag_overrides_chain(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["--provider", "netzverb,llm", "Buch"])

    assert captured["config"].providers == ("netzverb", "llm")


def test_llm_flag_is_shorthand_for_the_llm_only_chain(patched):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})

    cli.main(["--llm", "Buch"])

    assert captured["config"].providers == ("llm",)


def test_llm_flag_conflicts_with_provider(patched):
    with pytest.raises(SystemExit):
        cli.main(["--llm", "--provider", "netzverb", "Buch"])


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

    def fake_load(cls, *, path=None, auth_path=None, with_auth=True, **kwargs):
        seen["path"] = path
        seen["auth_path"] = auth_path
        seen["with_auth"] = with_auth
        return cls()

    monkeypatch.setattr(Config, "load", classmethod(fake_load))
    return seen


def test_config_flag_sets_load_path(patched, monkeypatch):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=1, term="Buch")})
    seen = _capture_load_path(monkeypatch)

    cli.main(["--config", "/tmp/custom.toml", "Buch"])

    assert seen["path"] == Path("/tmp/custom.toml")
    assert seen["with_auth"] is True


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


def test_dry_run_rejects_sync(patched, capsys):
    captured, set_results = patched
    set_results({"Buch": AddResult(note_id=None, term="Buch")})

    with pytest.raises(SystemExit) as exc:
        cli.main(["--dry-run", "--sync", "Buch"])

    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert captured["collection_syncs"] == 0


# ---------------------------------------------------------------------------
# Collection sync (--sync)
# ---------------------------------------------------------------------------


def test_sync_flag_syncs_once_after_all_terms(patched, capsys):
    captured, set_results = patched
    set_results(
        {"a": AddResult(note_id=1, term="a"), "b": AddResult(note_id=2, term="b")}
    )

    code = cli.main(["--sync", "a", "b"])

    assert code == 0
    assert captured["collection_syncs"] == 1
    assert capsys.readouterr().out.splitlines()[-1] == "sync requested"


def test_sync_flag_still_syncs_after_a_failed_term(patched):
    captured, set_results = patched
    set_results({"a": AddResult(note_id=1, term="a"), "b": None})

    code = cli.main(["--sync", "a", "b"])

    assert code == 1  # the miss still marks the run as failed
    assert captured["collection_syncs"] == 1


def test_no_sync_without_the_flag(patched):
    captured, set_results = patched
    set_results({"a": AddResult(note_id=1, term="a")})

    cli.main(["a"])

    assert captured["collection_syncs"] == 0


def test_no_sync_when_note_type_setup_fails(patched):
    captured, set_results = patched
    builder = set_results({"a": AddResult(note_id=1, term="a")})
    builder.verify_error = SinkError("field mismatch")

    code = cli.main(["--sync", "a"])

    assert code == 1
    assert captured["collection_syncs"] == 0


def test_collection_sync_failure_exits_1(patched, capsys):
    captured, set_results = patched
    set_results({"a": AddResult(note_id=1, term="a")})
    captured["sync_error"] = SinkError("AnkiConnect error: sync: auth not configured")

    code = cli.main(["--sync", "a"])

    assert code == 1
    assert "sync failed: AnkiConnect error: sync: auth not configured" in (
        capsys.readouterr().err
    )


def test_quiet_sync_prints_nothing(patched, capsys):
    captured, set_results = patched
    set_results({"a": AddResult(note_id=1, term="a")})

    code = cli.main(["-q", "--sync", "a"])

    assert code == 0
    assert captured["collection_syncs"] == 1
    assert capsys.readouterr().out == ""


def test_missing_word_argument_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    assert exc.value.code == 2  # argparse's usage-error exit code
    assert "the following arguments are required: terms" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Note type sync
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_patched(monkeypatch):
    """Patch the sync; return a dict of what it saw."""
    seen: dict[str, object] = {"result": SyncResult([], {}), "calls": 0}

    def fake_sync(config):
        seen["calls"] += 1
        seen["config"] = config
        if isinstance(seen["result"], Exception):
            raise seen["result"]
        return seen["result"]

    def no_builder(config):
        pytest.fail("sync mode must not build a DeckBuilder")

    monkeypatch.setattr(cli, "sync_note_types", fake_sync)
    monkeypatch.setattr(cli, "build_deck_builder", no_builder)
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *a, **k: cls()))
    return seen


def test_sync_runs_and_reports_changes(sync_patched, capsys):
    sync_patched["result"] = SyncResult(
        ["Ankery DE: Verb"], {"Ankery DE: Noun": ["templates", "styling"]}
    )

    code = cli.main(["sync-note-types", "--pack", "de"])

    assert code == 0
    assert sync_patched["config"].pack == "de"
    out = capsys.readouterr().out
    assert "created note type: Ankery DE: Verb" in out
    assert "updated note type: Ankery DE: Noun (templates, styling)" in out


def test_sync_failure_exits_1(sync_patched, capsys):
    sync_patched["result"] = SinkError("card types differ")

    code = cli.main(["sync-note-types", "--pack", "de"])

    assert code == 1
    assert "note type sync failed: card types differ" in capsys.readouterr().err


def test_sync_unknown_pack_exits_2(sync_patched, capsys):
    sync_patched["result"] = ConfigError("unknown pack 'xx'")

    code = cli.main(["sync-note-types", "--pack", "xx"])

    assert code == 2
    assert "unknown pack 'xx'" in capsys.readouterr().err


def test_sync_requires_pack(sync_patched, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["sync-note-types"])

    assert exc.value.code == 2
    assert "the following arguments are required: --pack" in capsys.readouterr().err
    assert sync_patched["calls"] == 0


@pytest.mark.parametrize(
    "extra",
    [
        ["Buch"],
        ["-n"],
        ["-q"],
        ["--deck", "German"],
        ["--notes-dir", "notes"],
        ["--auth", "auth.toml"],
    ],
)
def test_sync_rejects_terms_and_other_flags(sync_patched, capsys, extra):
    with pytest.raises(SystemExit) as exc:
        cli.main(["sync-note-types", "--pack", "de", *extra])

    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert sync_patched["calls"] == 0


def test_sync_command_only_selected_by_first_token(sync_patched, patched):
    captured, set_results = patched
    builder = set_results(
        {"sync-note-types": AddResult(note_id=1, term="sync-note-types", note_type="N", fields={})}
    )

    code = cli.main(["--", "sync-note-types"])

    assert code == 0
    assert builder.calls == ["sync-note-types"]
    assert sync_patched["calls"] == 0


def test_sync_accepts_config_packs_dir_and_anki_url(sync_patched, monkeypatch):
    seen = _capture_load_path(monkeypatch)

    code = cli.main([
        "sync-note-types", "--pack", "de",
        "--config", "/tmp/custom.toml", "--packs-dir", "/tmp/packs",
        "--anki-url", "http://anki.local:8765",
    ])

    assert code == 0
    assert sync_patched["calls"] == 1
    assert seen["path"] == Path("/tmp/custom.toml")
    assert seen["with_auth"] is False
    assert sync_patched["config"].packs_dir == Path("/tmp/packs")
    assert sync_patched["config"].anki_url == "http://anki.local:8765"


# ---------------------------------------------------------------------------
# Sign in with ChatGPT: login, logout, status
# ---------------------------------------------------------------------------

ISSUED = "oaiapp_123"


def _jwt(claims: dict) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.sig"


def _signed_in_record(**overrides) -> dict:
    return {
        "email": "a@example.com",
        "issuer": "https://auth.openai.com",
        "subject": "user-1",
        "client_id": ISSUED,
        "ext_agent_host_id": "urn:uuid:host",
        "id_token": "id-secret",
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "expires_in": 3600,
        "scopes": ["openid"],
        "saved_at": datetime.now(UTC).isoformat(),
        **overrides,
    }


class _FakeServer:
    """CallbackServer stand-in that returns a scripted redirect."""

    def __init__(self, redirect=None):
        self.redirect = redirect
        self.closed = False

    def wait(self, timeout):
        return self.redirect()

    def close(self):
        self.closed = True


@pytest.fixture
def signin_env(monkeypatch, tmp_path):
    """Isolate the token store, the display and the prompt; return the shared state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *a, **k: cls()))
    env = {"answers": [], "prompts": [], "auths": [], "opened": [], "server": None, "display": False}

    def fake_input(prompt=""):
        env["prompts"].append(prompt.strip())
        if not env["answers"]:
            raise EOFError
        answer = env["answers"].pop(0)
        return answer(env) if callable(answer) else answer

    real_begin = signin.begin

    def begin(record):
        env["auths"].append(real_begin(record))
        return env["auths"][-1]

    def make_server(state):
        env["server_state"] = state
        if isinstance(env["server"], Exception):
            raise env["server"]
        return env["server"]

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(signin, "begin", begin)
    monkeypatch.setattr(signin, "can_open_browser", lambda: env["display"])
    monkeypatch.setattr(signin, "CallbackServer", make_server)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: env["opened"].append(url) or True)
    env["store"] = signin.TokenStore(signin.tokens_path())
    return env


def _pasted_redirect(env, **params) -> str:
    auth = env["auths"][-1]
    query = {"code": "the-code", "state": auth.state, "client_id": ISSUED, **params}
    return f"{signin.REDIRECT_URI}?{urlencode(query)}"


def _mock_sign_in(httpx_mock, env, *, models=("gpt-5.5",), subject="user-1"):
    def token_response(request):
        claims = {
            "iss": "https://auth.openai.com",
            "aud": ISSUED,
            "sub": subject,
            "email": "a@example.com",
            "nonce": env["auths"][-1].nonce,
            "exp": time.time() + 3600,
        }
        return httpx.Response(200, json={
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "id_token": _jwt(claims),
            "expires_in": 3600,
            "scope": "openid chatgpt.tokens.use.direct",
        })

    httpx_mock.add_callback(token_response, url=signin.TOKEN_URL)
    if models is not None:
        httpx_mock.add_response(
            url=signin.MODELS_URL,
            json={"models": [{"slug": slug, "visibility": "list"} for slug in models]},
        )


PASTE_OR_OPEN = "Paste the redirect URL, or enter o to open the link in a browser, q to cancel."
PASTE_ONLY = "Paste the redirect URL, or enter q to cancel."


def test_login_paste_saves_the_sign_in_and_lists_models(signin_env, httpx_mock, capsys):
    _mock_sign_in(httpx_mock, signin_env, models=("gpt-5.5", "gpt-6-astra"))
    signin_env["answers"] = [_pasted_redirect]

    code = cli.main(["login"])

    assert code == 0
    record = signin_env["store"].load()
    assert record["client_id"] == ISSUED
    assert record["refresh_token"] == "refresh-secret"
    out = capsys.readouterr().out
    assert signin_env["auths"][0].url in out
    assert "signed in as a@example.com" in out
    assert "models:\n  gpt-5.5\n  gpt-6-astra" in out


def test_login_output_holds_no_secret(signin_env, httpx_mock, capsys):
    _mock_sign_in(httpx_mock, signin_env)
    signin_env["answers"] = [_pasted_redirect]

    cli.main(["login"])

    captured = capsys.readouterr()
    for secret in ("the-code", "access-secret", "refresh-secret", signin_env["auths"][0].verifier):
        assert secret not in captured.out + captured.err


def test_login_without_a_display_offers_no_browser(signin_env, capsys):
    signin_env["display"] = False
    signin_env["server"] = AssertionError("bound without a display")
    signin_env["answers"] = ["q"]

    code = cli.main(["login"])

    assert code == 1
    out = capsys.readouterr().out
    assert PASTE_ONLY in out
    assert "enter o" not in out


def test_login_without_a_display_refuses_open_and_keeps_asking(signin_env, capsys):
    signin_env["answers"] = ["o", "q"]

    code = cli.main(["login"])

    assert code == 1
    assert "No browser can be opened here; paste the redirect URL." in capsys.readouterr().out
    assert signin_env["opened"] == []


@pytest.mark.parametrize("answer", ["", "1", "oops"])
def test_login_repeats_the_usage_for_input_that_is_no_url_or_command(
    signin_env, httpx_mock, capsys, answer
):
    _mock_sign_in(httpx_mock, signin_env)
    signin_env["answers"] = [answer, _pasted_redirect]

    code = cli.main(["login"])

    assert code == 0
    assert capsys.readouterr().out.count(PASTE_ONLY) == 2
    assert signin.is_signed_in(signin_env["store"].load())


@pytest.mark.parametrize("answer", ["O", "open"])
def test_login_open_command_is_case_insensitive_and_has_a_long_form(
    signin_env, httpx_mock, answer
):
    _mock_sign_in(httpx_mock, signin_env)
    server = _FakeServer(lambda: _pasted_redirect(signin_env))
    signin_env.update(display=True, server=server, answers=[answer])

    assert cli.main(["login"]) == 0
    assert signin_env["opened"] == [signin_env["auths"][0].browser_url]


def test_login_busy_port_names_it_and_keeps_paste(signin_env, httpx_mock, capsys):
    _mock_sign_in(httpx_mock, signin_env)
    signin_env["display"] = True
    signin_env["server"] = OSError(98, "Address already in use")
    signin_env["answers"] = [_pasted_redirect]

    code = cli.main(["login"])

    assert code == 0
    captured = capsys.readouterr()
    assert "port 1455 is in use (Address already in use)" in captured.err
    assert PASTE_ONLY in captured.out
    assert signin.is_signed_in(signin_env["store"].load())


def test_login_browser_waits_for_the_loopback_redirect(signin_env, httpx_mock, capsys):
    _mock_sign_in(httpx_mock, signin_env)
    server = _FakeServer(lambda: _pasted_redirect(signin_env))
    signin_env.update(display=True, server=server, answers=["o"])

    code = cli.main(["login"])

    assert code == 0
    assert PASTE_OR_OPEN in capsys.readouterr().out
    assert signin_env["opened"] == [signin_env["auths"][0].browser_url]
    assert signin_env["server_state"] == signin_env["auths"][0].state
    assert server.closed
    assert signin.is_signed_in(signin_env["store"].load())


def test_repeat_login_prints_no_id_token_but_hints_it_to_the_browser(
    signin_env, httpx_mock, capsys
):
    signin_env["store"].save(_signed_in_record())
    _mock_sign_in(httpx_mock, signin_env)
    server = _FakeServer(lambda: _pasted_redirect(signin_env))
    signin_env.update(display=True, server=server, answers=["o"])

    code = cli.main(["login"])

    assert code == 0
    captured = capsys.readouterr()
    assert "id-secret" not in captured.out + captured.err
    assert signin_env["auths"][0].url in captured.out
    assert "id_token_hint=id-secret" in signin_env["opened"][0]


def test_login_loopback_redirect_with_another_state_writes_nothing(signin_env, httpx_mock, capsys):
    server = _FakeServer(lambda: _pasted_redirect(signin_env, state="forged"))
    signin_env.update(display=True, server=server, answers=["o"])

    code = cli.main(["login"])

    assert code == 1
    assert "sign-in failed: the redirect URL is not from this sign-in" in capsys.readouterr().err
    assert signin_env["store"].load() is None
    assert httpx_mock.get_requests() == []


def test_login_pasted_redirect_with_another_state_writes_nothing(signin_env, httpx_mock, capsys):
    signin_env["answers"] = [lambda env: _pasted_redirect(env, state="forged")]

    code = cli.main(["login"])

    assert code == 1
    assert "state mismatch" in capsys.readouterr().err
    assert signin_env["store"].load() is None
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize("answers", [["q"], ["quit"], []], ids=["q", "quit", "eof"])
def test_login_cancel_writes_nothing(signin_env, capsys, answers):
    signin_env["answers"] = answers

    code = cli.main(["login"])

    assert code == 1
    assert "sign-in cancelled" in capsys.readouterr().err
    assert not signin.tokens_path().exists()


def test_login_ctrl_c_while_waiting_cancels(signin_env, capsys):
    def interrupted():
        raise KeyboardInterrupt

    server = _FakeServer(interrupted)
    signin_env.update(display=True, server=server, answers=["o"])

    code = cli.main(["login"])

    assert code == 1
    assert server.closed
    assert not signin.tokens_path().exists()


def test_login_failure_keeps_the_previous_sign_in(signin_env, httpx_mock, capsys):
    record = _signed_in_record()
    signin_env["store"].save(record)
    httpx_mock.add_response(url=signin.TOKEN_URL, status_code=400, json={"error": "invalid_grant"})
    signin_env["answers"] = [_pasted_redirect]

    code = cli.main(["login"])

    assert code == 1
    assert "sign-in failed: token request failed: HTTP 400: invalid_grant" in capsys.readouterr().err
    assert signin_env["store"].load() == record


def test_repeat_login_reuses_the_registration(signin_env, httpx_mock):
    signin_env["store"].save(_signed_in_record())
    _mock_sign_in(httpx_mock, signin_env)
    signin_env["answers"] = [_pasted_redirect]

    cli.main(["login"])

    auth = signin_env["auths"][0]
    assert auth.client_id == ISSUED
    assert auth.ext_agent_host_id == "urn:uuid:host"
    assert signin_env["store"].load()["ext_agent_host_id"] == "urn:uuid:host"


def test_login_as_another_account_fails_and_keeps_the_registration(signin_env, httpx_mock, capsys):
    registration = signin.without_tokens(_signed_in_record())
    signin_env["store"].save(registration)
    _mock_sign_in(httpx_mock, signin_env, models=None, subject="user-2")
    signin_env["answers"] = [_pasted_redirect]

    code = cli.main(["login"])

    assert code == 1
    assert "another ChatGPT account" in capsys.readouterr().err
    assert signin_env["store"].load() == registration


def test_login_after_logout_accepts_another_account(signin_env, httpx_mock, capsys):
    signin_env["store"].save(signin.signed_out(_signed_in_record()))
    _mock_sign_in(httpx_mock, signin_env, subject="user-2")
    signin_env["answers"] = [_pasted_redirect]

    code = cli.main(["login"])

    assert code == 0
    record = signin_env["store"].load()
    assert record["subject"] == "user-2"
    assert record["ext_agent_host_id"] == "urn:uuid:host"


def test_logout_revokes_and_forgets_the_account(signin_env, httpx_mock, capsys):
    signin_env["store"].save(_signed_in_record())
    signin_env["answers"] = ["y"]
    httpx_mock.add_response(url=signin.REVOKE_URL)

    code = cli.main(["logout"])

    assert code == 0
    assert "signed out a@example.com" in capsys.readouterr().out
    assert signin_env["store"].load() == {"ext_agent_host_id": "urn:uuid:host"}
    assert "token=refresh-secret" in httpx_mock.get_request().content.decode()


def test_logout_deletes_tokens_even_if_revocation_fails(signin_env, httpx_mock, capsys):
    signin_env["store"].save(_signed_in_record())
    signin_env["answers"] = ["y"]
    httpx_mock.add_response(url=signin.REVOKE_URL, status_code=503)

    code = cli.main(["logout"])

    assert code == 0
    assert "warning: revocation request failed: HTTP 503" in capsys.readouterr().err
    assert not signin.is_signed_in(signin_env["store"].load())


@pytest.mark.parametrize("answer", ["Y", "yes"])
def test_logout_accepts_yes(signin_env, httpx_mock, capsys, answer):
    signin_env["store"].save(_signed_in_record())
    signin_env["answers"] = [answer]
    httpx_mock.add_response(url=signin.REVOKE_URL)

    code = cli.main(["logout"])

    assert code == 0
    assert "Sign out a@example.com? [y/N]" in signin_env["prompts"]
    assert not signin.is_signed_in(signin_env["store"].load())


@pytest.mark.parametrize("answers", [["n"], [""], ["sure"], []], ids=["no", "enter", "other", "eof"])
def test_logout_declined_keeps_the_sign_in(signin_env, httpx_mock, capsys, answers):
    record = _signed_in_record()
    signin_env["store"].save(record)
    signin_env["answers"] = answers

    code = cli.main(["logout"])

    assert code == 1
    assert "logout cancelled" in capsys.readouterr().err
    assert signin_env["store"].load() == record
    assert httpx_mock.get_requests() == []


def test_logout_after_cleared_tokens_forgets_the_account(signin_env, httpx_mock, capsys):
    signin_env["store"].save(signin.without_tokens(_signed_in_record()))

    code = cli.main(["logout"])

    assert code == 0
    assert "not signed in" in capsys.readouterr().out
    assert signin_env["store"].load() == {"ext_agent_host_id": "urn:uuid:host"}
    assert httpx_mock.get_requests() == []


def test_logout_when_signed_out_is_a_no_op(signin_env, httpx_mock, capsys):
    code = cli.main(["logout"])

    assert code == 0
    assert "not signed in" in capsys.readouterr().out
    assert not signin.tokens_path().exists()


def test_status_shows_account_expiry_and_models_without_tokens(signin_env, httpx_mock, capsys):
    signin_env["store"].save(_signed_in_record())
    httpx_mock.add_response(
        url=signin.MODELS_URL, json={"models": [{"slug": "gpt-5.5", "visibility": "list"}]}
    )

    code = cli.main(["status"])

    assert code == 0
    out = capsys.readouterr().out
    assert "signed in as a@example.com" in out
    assert "access token expires " in out
    assert "models:\n  gpt-5.5" in out
    for secret in ("access-secret", "refresh-secret", "id-secret"):
        assert secret not in out


def test_status_refreshes_an_expired_token(signin_env, httpx_mock, capsys):
    signin_env["store"].save(_signed_in_record(saved_at="2026-01-01T00:00:00+00:00"))
    httpx_mock.add_response(url=signin.TOKEN_URL, json={
        "access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600,
    })
    httpx_mock.add_response(url=signin.MODELS_URL, json={"models": []})

    code = cli.main(["status"])

    assert code == 0
    assert signin_env["store"].load()["refresh_token"] == "refresh-2"
    assert httpx_mock.get_requests()[-1].headers["Authorization"] == "Bearer access-2"


def test_status_when_signed_out_exits_1(signin_env, capsys):
    code = cli.main(["status"])

    assert code == 1
    assert "not signed in; run `ankery login`" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["login", "logout", "status"])
def test_signin_commands_load_config_without_auth(signin_env, monkeypatch, command):
    seen = _capture_load_path(monkeypatch)
    signin_env["answers"] = ["q"]

    cli.main([command, "--config", "/tmp/custom.toml"])

    assert seen["path"] == Path("/tmp/custom.toml")
    assert seen["with_auth"] is False


@pytest.mark.parametrize("command", ["login", "logout", "status"])
def test_signin_commands_reject_add_flags(signin_env, capsys, command):
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "--pack", "de"])

    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["login", "logout", "status"])
def test_signin_commands_only_selected_by_first_token(patched, monkeypatch, command):
    captured, set_results = patched
    builder = set_results(
        {command: AddResult(note_id=1, term=command, note_type="N", fields={})}
    )
    monkeypatch.setitem(cli.COMMANDS, command, lambda argv: pytest.fail("command ran"))

    code = cli.main(["--", command])

    assert code == 0
    assert builder.calls == [command]
