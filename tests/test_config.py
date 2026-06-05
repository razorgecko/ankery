import warnings

import pytest

from pathlib import Path

from ankery.config import Config, ConfigError, _config_dir, build_deck_builder
from ankery.manager import DeckBuilder
from ankery.notedef import default_field_map
from ankery.providers.llm import LLMProvider
from ankery.sinks.ankiconnect import AnkiConnectSink


@pytest.fixture(autouse=True)
def _isolate_default_config_dir(monkeypatch, tmp_path):
    # Keep Config.load() hermetic: tests that don't pass path/auth_path must not
    # read the developer's real ~/.config/ankery/. Point the XDG base dir at an
    # empty tmp dir so both config.toml and auth.toml defaults miss.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "_xdg"))


def _write(tmp_path, text: str):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def _write_auth(tmp_path, text: str):
    path = tmp_path / "auth.toml"
    path.write_text(text)
    path.chmod(0o600)  # default: locked down, so secret-file tests don't warn
    return path


def _provider_named(builder, name):
    [provider] = [p for p in builder.providers if p.name == name]
    return provider


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def test_from_env_uses_defaults_when_unset():
    config = Config.from_env({})

    assert config.llm_base_url == "http://localhost:8080/v1"
    assert config.anki_url == "http://localhost:8765"
    assert config.deck == "Default"
    assert config.note_type == "Basic"
    assert config.tags == ()
    assert config.allow_duplicate is False
    assert config.source_language == "de"
    assert config.providers == ()  # empty => use the pack's preferred chain


def test_from_env_ignores_non_secret_vars():
    # Env carries only the secret; every other field is set in config.toml or via
    # CLI flags. Legacy ANKERY_* names for those fields are deliberately ignored.
    base = Config(deck="FromFile", llm_model="file-model")
    env = {
        "ANKERY_LLM_URL": "https://api.groq.com/openai/v1",
        "ANKERY_DECK": "German::Verbs",
        "ANKERY_PROVIDERS": "llm, netzverb",
        "ANKERY_ALLOW_DUPLICATE": "true",
    }
    config = Config.from_env(env, base=base)

    assert config.llm_base_url == "http://localhost:8080/v1"  # default, env ignored
    assert config.llm_model == "file-model"  # base shows through, env ignored
    assert config.deck == "FromFile"
    assert config.providers == ()
    assert config.allow_duplicate is False


def test_from_env_layers_api_key_on_top_of_base():
    base = Config(deck="FromFile", llm_model="file-model")
    config = Config.from_env({"ANKERY_LLM_API_KEY": "sk-from-env"}, base=base)

    assert config.llm_api_key == "sk-from-env"  # env supplies the secret
    assert config.deck == "FromFile"  # everything else shows through from base
    assert config.llm_model == "file-model"


def test_config_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert _config_dir() == tmp_path / "xdg" / "ankery"


def test_config_dir_falls_back_to_home_config(monkeypatch, tmp_path):
    # Unset, empty, and non-absolute XDG_CONFIG_HOME all fall back to ~/.config.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    expected = tmp_path / "home" / ".config" / "ankery"
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert _config_dir() == expected
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert _config_dir() == expected
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    assert _config_dir() == expected


def test_load_missing_file_uses_defaults(tmp_path):
    config = Config.load(path=tmp_path / "absent.toml", environ={})

    assert config.deck == "Default"
    assert config.llm_base_url == "http://localhost:8080/v1"


def test_load_reads_file_values(tmp_path):
    path = _write(
        tmp_path,
        'deck = "German::Vocab"\n'
        'llm_base_url = "http://llm.local/v1"\n'
        "llm_timeout = 12\n"
        'tags = ["auto", "de"]\n',
    )
    config = Config.load(path=path, environ={})

    assert config.deck == "German::Vocab"
    assert config.llm_base_url == "http://llm.local/v1"
    assert config.llm_timeout == 12.0  # int in TOML coerced to float
    assert config.tags == ("auto", "de")  # list coerced to tuple


def test_load_reads_langs_dir_as_path(tmp_path):
    path = _write(tmp_path, 'langs_dir = "/srv/packs"\n')
    config = Config.load(path=path, environ={})

    assert config.langs_dir == Path("/srv/packs")  # string coerced to Path


def test_load_reads_notes_dir_as_path(tmp_path):
    path = _write(tmp_path, 'notes_dir = "/srv/notes"\n')
    config = Config.load(path=path, environ={})

    assert config.notes_dir == Path("/srv/notes")  # string coerced to Path


def test_load_reads_providers_list(tmp_path):
    path = _write(tmp_path, 'providers = ["netzverb", "llm"]\n')
    config = Config.load(path=path, environ={})

    assert config.providers == ("netzverb", "llm")  # list coerced to tuple


def test_load_env_does_not_override_non_secret_file_value(tmp_path):
    path = _write(tmp_path, 'deck = "FromFile"\nnote_type = "Cloze"\n')
    config = Config.load(path=path, environ={"ANKERY_DECK": "FromEnv"})

    assert config.deck == "FromFile"  # file wins; env ignored
    assert config.note_type == "Cloze"  # file beats default


def test_load_rejects_unknown_keys(tmp_path):
    path = _write(tmp_path, 'dekc = "typo"\n')
    with pytest.raises(ConfigError, match="unknown config keys: dekc"):
        Config.load(path=path, environ={})


def test_load_refuses_api_key_in_config_file(tmp_path):
    path = _write(tmp_path, 'llm_api_key = "secret"\n')
    with pytest.raises(ConfigError, match="auth.toml"):
        Config.load(path=path, environ={})


def test_load_refuses_api_key_even_alongside_valid_keys(tmp_path):
    path = _write(tmp_path, 'deck = "German"\nllm_api_key = "secret"\n')
    with pytest.raises(ConfigError, match="auth.toml"):
        Config.load(path=path, environ={})


def test_load_reads_api_key_from_auth_file(tmp_path):
    auth = _write_auth(tmp_path, 'llm_api_key = "sk-from-auth"\n')
    config = Config.load(path=tmp_path / "absent.toml", auth_path=auth, environ={})

    assert config.llm_api_key == "sk-from-auth"


def test_load_config_and_auth_together(tmp_path):
    path = _write(tmp_path, 'deck = "German::Vocab"\n')
    auth = _write_auth(tmp_path, 'llm_api_key = "sk-123"\n')
    config = Config.load(path=path, auth_path=auth, environ={})

    assert config.deck == "German::Vocab"  # from config.toml
    assert config.llm_api_key == "sk-123"  # from auth.toml


def test_load_env_overrides_auth_file(tmp_path):
    auth = _write_auth(tmp_path, 'llm_api_key = "sk-from-auth"\n')
    config = Config.load(
        path=tmp_path / "absent.toml",
        auth_path=auth,
        environ={"ANKERY_LLM_API_KEY": "sk-from-env"},
    )

    assert config.llm_api_key == "sk-from-env"  # env beats auth.toml


def test_load_missing_auth_file_leaves_key_unset(tmp_path):
    config = Config.load(
        path=tmp_path / "absent.toml",
        auth_path=tmp_path / "absent-auth.toml",
        environ={},
    )

    assert config.llm_api_key is None


def test_load_auth_file_rejects_non_secret_keys(tmp_path):
    auth = _write_auth(tmp_path, 'deck = "German"\n')
    with pytest.raises(ConfigError, match="move deck to config.toml"):
        Config.load(path=tmp_path / "absent.toml", auth_path=auth, environ={})


def test_load_reads_config_path_from_env(tmp_path):
    path = _write(tmp_path, 'deck = "FromEnvPath"\n')
    config = Config.load(environ={"ANKERY_CONFIG": str(path)})

    assert config.deck == "FromEnvPath"


def test_load_explicit_config_path_wins_over_env(tmp_path):
    flag_file = _write(tmp_path, 'deck = "FromFlag"\n')
    env_file = tmp_path / "from-env.toml"
    env_file.write_text('deck = "FromEnvPath"\n')
    config = Config.load(path=flag_file, environ={"ANKERY_CONFIG": str(env_file)})

    assert config.deck == "FromFlag"


def test_load_reads_auth_path_from_env(tmp_path):
    auth = _write_auth(tmp_path, 'llm_api_key = "sk-from-env-path"\n')
    config = Config.load(path=tmp_path / "absent.toml", environ={"ANKERY_AUTH": str(auth)})

    assert config.llm_api_key == "sk-from-env-path"


def test_load_explicit_auth_path_wins_over_env(tmp_path):
    flag_auth = _write_auth(tmp_path, 'llm_api_key = "sk-from-flag"\n')
    env_auth = tmp_path / "auth-from-env.toml"
    env_auth.write_text('llm_api_key = "sk-from-env-path"\n')
    config = Config.load(
        path=tmp_path / "absent.toml",
        auth_path=flag_auth,
        environ={"ANKERY_AUTH": str(env_auth)},
    )

    assert config.llm_api_key == "sk-from-flag"


def test_load_reads_bool_from_file(tmp_path):
    path = _write(tmp_path, "llm_request_json_format = false\n")
    config = Config.load(path=path, environ={})

    assert config.llm_request_json_format is False


def test_load_wraps_malformed_toml(tmp_path):
    path = _write(tmp_path, "deck = \n")  # value missing -> TOMLDecodeError
    with pytest.raises(ConfigError, match="Could not read config file"):
        Config.load(path=path, environ={})


def test_from_env_reads_process_environ_when_unset(monkeypatch):
    monkeypatch.setenv("ANKERY_LLM_API_KEY", "sk-from-process-env")
    config = Config.from_env()  # environ=None -> falls back to os.environ

    assert config.llm_api_key == "sk-from-process-env"


def test_api_key_read_from_env_only():
    assert Config.from_env({}).llm_api_key is None
    assert Config.from_env({"ANKERY_LLM_API_KEY": "sk-123"}).llm_api_key == "sk-123"


# ---------------------------------------------------------------------------
# Pack-driven wiring
# ---------------------------------------------------------------------------


def test_build_deck_builder_passes_api_key():
    builder = build_deck_builder(Config(llm_api_key="sk-123", providers=("llm",)))
    assert _provider_named(builder, "llm").api_key == "sk-123"


def test_build_deck_builder_wires_provider_and_sink():
    config = Config(
        providers=("llm",),
        llm_base_url="http://llm.local/v1",
        llm_model="my-model",
        anki_url="http://anki.local:8765",
        deck="German",
        note_type="Basic",
        tags=("auto",),
    )
    builder = build_deck_builder(config)

    assert isinstance(builder, DeckBuilder)
    assert builder.deck == "German"
    assert builder.note_type == "Basic"
    assert builder.tags == ["auto"]
    assert builder.map_fields is default_field_map

    provider = _provider_named(builder, "llm")
    assert isinstance(provider, LLMProvider)
    assert provider.base_url == "http://llm.local/v1"
    assert provider.model == "my-model"

    assert isinstance(builder.sink, AnkiConnectSink)
    assert builder.sink.base_url == "http://anki.local:8765"


def test_build_deck_builder_loads_the_packs_notes_and_style():
    builder = build_deck_builder(Config())  # default source_language "de"

    assert [d.name for d in builder.note_definitions] == [
        "Ankery DE: Word", "Ankery DE: Noun", "Ankery DE: Verb",
    ]
    assert ".card" in builder.style_css


def _write_note(directory: Path, stem: str, name: str, applies_to: str | None):
    directory.mkdir(parents=True, exist_ok=True)
    applies = f'applies_to = "{applies_to}"\n' if applies_to is not None else ""
    (directory / f"{stem}.toml").write_text(
        f'name = "{name}"\n{applies}[map]\nFront = "{{{{ word }}}}"\n', "utf-8"
    )


def test_notes_dir_merges_over_the_packs_notes_by_category(tmp_path):
    # A generic noun layout replaces the pack's "Ankery DE: Noun" for nouns; a new
    # category (adjective) is added; the pack's verb is left in place.
    _write_note(tmp_path, "noun", "Simple Noun", "noun")
    _write_note(tmp_path, "adj", "Simple Adjective", "adjective")
    builder = build_deck_builder(Config(notes_dir=tmp_path))

    names = [d.name for d in builder.note_definitions]
    assert names == ["Ankery DE: Word", "Simple Noun", "Ankery DE: Verb", "Simple Adjective"]


def test_notes_dir_unset_leaves_the_packs_notes_alone():
    builder = build_deck_builder(Config())  # notes_dir is None by default

    assert [d.name for d in builder.note_definitions] == [
        "Ankery DE: Word", "Ankery DE: Noun", "Ankery DE: Verb",
    ]


def test_notes_dir_with_duplicate_category_surfaces_as_config_error(tmp_path):
    _write_note(tmp_path, "a_noun", "Noun A", "noun")
    _write_note(tmp_path, "b_noun", "Noun B", "noun")
    with pytest.raises(ConfigError, match="both serve category 'noun'"):
        build_deck_builder(Config(notes_dir=tmp_path))


def test_empty_chain_falls_back_to_the_packs_preferred_chain():
    # config.providers is () by default, so the pack's chain is used.
    builder = build_deck_builder(Config())

    assert [p.name for p in builder.providers] == ["netzverb", "llm"]


def test_build_deck_builder_honors_provider_order():
    builder = build_deck_builder(Config(providers=("llm", "netzverb")))

    assert [p.name for p in builder.providers] == ["llm", "netzverb"]


def test_pack_local_provider_gets_options_from_lang_toml():
    # netzverb's timeout comes from the de pack's [provider_options], not Config.
    builder = build_deck_builder(Config(providers=("netzverb",)))
    assert _provider_named(builder, "netzverb")._timeout == 15.0


def test_llm_provider_gets_the_pack_rendered_prompt():
    builder = build_deck_builder(Config(providers=("llm",)))
    assert "German" in _provider_named(builder, "llm").system_prompt_for(None)


def test_build_deck_builder_rejects_unknown_provider():
    with pytest.raises(ConfigError, match="unknown provider 'nope'"):
        build_deck_builder(Config(providers=("nope",)))


def test_build_deck_builder_rejects_unknown_source_language():
    with pytest.raises(ConfigError, match="no language pack for 'zz'"):
        build_deck_builder(Config(source_language="zz"))


def test_source_language_selects_a_user_pack_via_langs_dir(tmp_path):
    pack_dir = tmp_path / "xx"
    (pack_dir / "notes").mkdir(parents=True)
    (pack_dir / "lang.toml").write_text(
        'name = "Examplish"\nproviders = ["llm"]\n[category]\nname = "pos"\n[pos.noun]\n[pos.noun.features]\nplural = "plural"\n',
        "utf-8",
    )
    builder = build_deck_builder(Config(source_language="xx", langs_dir=tmp_path))

    assert [p.name for p in builder.providers] == ["llm"]
    assert "Examplish" in _provider_named(builder, "llm").system_prompt_for(None)


def test_empty_chain_with_packless_chain_is_an_error(tmp_path):
    # A pack that declares no providers and a config that overrides none leaves
    # nothing to build.
    pack_dir = tmp_path / "yy"
    (pack_dir / "notes").mkdir(parents=True)
    (pack_dir / "lang.toml").write_text(
        'name = "Y"\nproviders = []\n[category]\nname = "pos"\n[pos.noun]\n', "utf-8"
    )

    with pytest.raises(ConfigError, match="no providers configured"):
        build_deck_builder(Config(source_language="yy", langs_dir=tmp_path))


# ---------------------------------------------------------------------------
# Security warnings (warn, don't block)
# ---------------------------------------------------------------------------


def test_load_warns_on_world_readable_auth_file(tmp_path):
    auth = _write_auth(tmp_path, 'llm_api_key = "sk-secret"\n')
    auth.chmod(0o644)
    with pytest.warns(UserWarning, match="accessible to group/others"):
        Config.load(path=tmp_path / "absent.toml", auth_path=auth, environ={})


def test_load_silent_when_auth_file_locked_down(tmp_path):
    auth = _write_auth(tmp_path, 'llm_api_key = "sk-secret"\n')
    auth.chmod(0o600)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning would raise
        config = Config.load(path=tmp_path / "absent.toml", auth_path=auth, environ={})
    assert config.llm_api_key == "sk-secret"


def test_build_warns_on_api_key_over_plaintext_http_to_remote():
    config = Config(
        providers=("llm",),
        llm_base_url="http://example.com:8080/v1",
        llm_api_key="sk-secret",
    )
    with pytest.warns(UserWarning, match="plaintext http"):
        build_deck_builder(config)


def test_build_silent_for_api_key_over_http_to_localhost():
    config = Config(
        providers=("llm",),
        llm_base_url="http://localhost:8080/v1",
        llm_api_key="sk-secret",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_deck_builder(config)


def test_build_silent_for_api_key_over_https():
    config = Config(
        providers=("llm",),
        llm_base_url="https://example.com/v1",
        llm_api_key="sk-secret",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_deck_builder(config)
