import warnings

import pytest

from pathlib import Path

from ankery.config import (
    Config,
    ConfigError,
    _config_dir,
    build_deck_builder,
    build_sink,
    resolve_variables,
    sync_collection,
    sync_note_types,
)
from ankery.pack import load_pack
from ankery.manager import DeckBuilder
from ankery.providers.llm import LLMProvider
from ankery.sinks.ankiconnect import AnkiConnectSink
from ankery.sinks.base import SyncResult


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


def _write_params(tmp_path, text: str):
    # llm_params.json is read from the config dir, which the autouse fixture
    # points at tmp_path / "_xdg".
    path = tmp_path / "_xdg" / "ankery" / "llm_params.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _provider_named(builder, name):
    [provider] = [p for p in builder.providers if p.name == name]
    return provider


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def test_note_type_default_is_the_owned_catch_all_model():
    # The default must equal the catch-all asset's own name, derived from one
    # source so the model ankery provisions and the model routing writes into
    # can't drift apart.
    from ankery.defaults import default_catch_all

    assert Config().note_type == default_catch_all().name


def test_from_env_uses_defaults_when_unset():
    config = Config.from_env({})

    assert config.llm_backend == "chat-completions"
    assert config.llm_base_url is None  # the transport's default
    assert config.llm_model is None  # the transport's default
    assert config.anki_url == "http://localhost:8765"
    assert config.deck == "Default"
    assert config.note_type == "Ankery Basic"
    assert config.tags == ()
    assert config.allow_duplicate is False
    assert config.pack is None  # the operator must choose one
    assert config.variables == {}  # seeded from the pack only at build time
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

    assert config.llm_base_url is None  # default, env ignored
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
    assert config.llm_base_url is None


def test_load_reads_file_values(tmp_path):
    path = _write(
        tmp_path,
        'deck = "German::Vocab"\n'
        'llm_base_url = "http://llm.local/v1"\n'
        "llm_timeout = 12\n"
        "anki_sync_timeout = 120\n"
        'tags = ["auto", "de"]\n',
    )
    config = Config.load(path=path, environ={})

    assert config.deck == "German::Vocab"
    assert config.llm_base_url == "http://llm.local/v1"
    assert config.llm_timeout == 12.0  # int in TOML coerced to float
    assert config.anki_sync_timeout == 120.0
    assert isinstance(config.anki_sync_timeout, float)
    assert config.tags == ("auto", "de")  # list coerced to tuple


def test_load_reads_packs_dir_as_path(tmp_path):
    path = _write(tmp_path, 'packs_dir = "/srv/packs"\n')
    config = Config.load(path=path, environ={})

    assert config.packs_dir == Path("/srv/packs")  # string coerced to Path


def test_load_reads_notes_dir_as_path(tmp_path):
    path = _write(tmp_path, 'notes_dir = "/srv/notes"\n')
    config = Config.load(path=path, environ={})

    assert config.notes_dir == Path("/srv/notes")  # string coerced to Path


def test_load_reads_variables_table(tmp_path):
    path = _write(tmp_path, "[variables]\ntarget_language = \"fr\"\n")
    config = Config.load(path=path, environ={})

    assert config.variables == {"target_language": "fr"}  # table maps onto the field


def test_load_rejects_engine_key_captured_under_variables_table(tmp_path):
    # `deck` written after the [variables] header is swallowed into the bag; the
    # error must point at the ordering, not later masquerade as an unknown variable.
    path = _write(tmp_path, "[variables]\ntarget_language = \"fr\"\ndeck = \"German\"\n")
    with pytest.raises(ConfigError, match="deck appear under \\[variables\\]"):
        Config.load(path=path, environ={})


def test_load_coerces_variable_values_to_str(tmp_path):
    # A TOML number is coerced to text; the bag is opaque key/value strings.
    path = _write(tmp_path, "[variables]\ncount = 3\n")
    config = Config.load(path=path, environ={})

    assert config.variables == {"count": "3"}


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


def test_load_without_auth_skips_auth_file_and_env_secret(tmp_path):
    path = _write(tmp_path, 'deck = "German"\n')
    auth = _write_auth(tmp_path, "llm_api_key = \n")  # malformed: would raise if read
    config = Config.load(
        path=path,
        auth_path=auth,
        environ={"ANKERY_LLM_API_KEY": "sk-from-env"},
        with_auth=False,
    )

    assert config.deck == "German"
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
    path = _write(tmp_path, "allow_duplicate = true\n")
    config = Config.load(path=path, environ={})

    assert config.allow_duplicate is True


def test_removed_llm_request_json_format_is_an_unknown_key(tmp_path):
    path = _write(tmp_path, "llm_request_json_format = false\n")

    with pytest.raises(ConfigError, match="unknown config keys: llm_request_json_format"):
        Config.load(path=path, environ={})


# ---------------------------------------------------------------------------
# llm_params.json
# ---------------------------------------------------------------------------


def test_missing_llm_params_file_means_no_overrides(tmp_path):
    config = Config.load(path=tmp_path / "absent.toml", environ={})

    assert config.llm_params == {}


def test_load_reads_llm_params_file(tmp_path):
    _write_params(
        tmp_path, '{"chat-completions": {"temperature": 0.2, "response_format": null}}'
    )

    config = Config.load(path=tmp_path / "absent.toml", environ={}, with_auth=False)

    assert config.llm_params == {
        "chat-completions": {"temperature": 0.2, "response_format": None}
    }


def test_llm_params_invalid_json_raises(tmp_path):
    path = _write_params(tmp_path, '{"chat-completions": {')

    with pytest.raises(ConfigError, match=f"Could not read {path}"):
        Config.load(path=tmp_path / "absent.toml", environ={})


def test_llm_params_non_object_top_level_raises(tmp_path):
    _write_params(tmp_path, "[]")

    with pytest.raises(ConfigError, match="llm_params.json: the top level must be an object"):
        Config.load(path=tmp_path / "absent.toml", environ={})


def test_llm_params_non_object_section_raises(tmp_path):
    _write_params(tmp_path, '{"chat-completions": 0.2}')

    with pytest.raises(ConfigError, match="section 'chat-completions' must be an object"):
        Config.load(path=tmp_path / "absent.toml", environ={})


def test_llm_params_unknown_backend_raises(tmp_path):
    _write_params(tmp_path, '{"chat-completion": {"temperature": 0.2}}')

    with pytest.raises(ConfigError, match="unknown llm backend 'chat-completion'"):
        Config.load(path=tmp_path / "absent.toml", environ={})


@pytest.mark.parametrize(
    ("backend", "key"),
    [
        ("chat-completions", "model"),
        ("chat-completions", "messages"),
        ("chat-completions", "stream"),
        ("chatgpt", "model"),
        ("chatgpt", "input"),
        ("chatgpt", "instructions"),
        ("chatgpt", "stream"),
        ("chatgpt", "store"),
    ],
)
def test_llm_params_owned_key_raises(tmp_path, backend, key):
    # Checked in every section, whichever backend is active (chat-completions here).
    _write_params(tmp_path, f'{{"{backend}": {{"{key}": "x"}}}}')

    with pytest.raises(ConfigError, match=f"section '{backend}' sets {key}"):
        Config.load(path=tmp_path / "absent.toml", environ={})


def test_llm_params_may_not_be_set_in_config_toml(tmp_path):
    path = _write(tmp_path, '[llm_params.chat-completions]\ntemperature = 0.2\n')

    with pytest.raises(ConfigError, match="llm_params may not be set in config.toml"):
        Config.load(path=path, environ={})


def test_llm_params_section_reaches_the_transport():
    config = Config(
        pack="de",
        providers=("llm",),
        llm_params={"chat-completions": {"temperature": 0.2, "response_format": None}},
    )

    transport = _provider_named(build_deck_builder(config), "llm").transport

    assert transport.params == {"temperature": 0.2}


def test_no_llm_params_section_means_transport_defaults():
    transport = _provider_named(
        build_deck_builder(Config(pack="de", providers=("llm",))), "llm"
    ).transport

    assert transport.params == {"temperature": 0, "response_format": {"type": "json_object"}}


# ---------------------------------------------------------------------------
# llm_backend
# ---------------------------------------------------------------------------


class _StubTokens:
    def access_token(self) -> str:
        return "stub-token"


@pytest.fixture
def stub_tokens(monkeypatch):
    monkeypatch.setattr("ankery.config._token_source", lambda config: _StubTokens())


def _transport(config):
    return _provider_named(build_deck_builder(config), "llm").transport


def test_load_reads_llm_backend(tmp_path):
    path = _write(tmp_path, 'llm_backend = "chatgpt"\n')

    assert Config.load(path=path, environ={}).llm_backend == "chatgpt"


def test_load_rejects_an_unknown_llm_backend(tmp_path):
    path = _write(tmp_path, 'llm_backend = "chatgtp"\n')

    with pytest.raises(ConfigError, match=f"{path}: unknown llm_backend 'chatgtp'"):
        Config.load(path=path, environ={})


def test_unknown_llm_backend_raises():
    config = Config(pack="de", providers=("llm",), llm_backend="chatgtp")

    with pytest.raises(ConfigError, match="unknown llm_backend 'chatgtp'; known: chat-completions, chatgpt"):
        build_deck_builder(config)


def test_chat_completions_backend_defaults():
    transport = _transport(Config(pack="de", providers=("llm",)))

    assert transport.name == "chat-completions"
    assert transport.base_url == "http://localhost:8080/v1"
    assert transport.model == "local-model"


def test_chat_completions_backend_uses_the_configured_url_and_model():
    transport = _transport(
        Config(
            pack="de",
            providers=("llm",),
            llm_base_url="https://llm.example/v1",
            llm_model="my-model",
        )
    )

    assert transport.base_url == "https://llm.example/v1"
    assert transport.model == "my-model"


def test_chatgpt_backend_silently_ignores_llm_base_url(stub_tokens):
    # The token is scoped to OpenAI's API; a configured URL must never receive it.
    config = Config(
        pack="de",
        providers=("llm",),
        llm_backend="chatgpt",
        llm_model="gpt-5.5",
        llm_base_url="http://example.com/v1",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        transport = _transport(config)

    assert transport.name == "chatgpt"
    assert transport.URL == "https://api.openai.com/v1/responses"
    assert not hasattr(transport, "base_url")
    assert transport.model == "gpt-5.5"
    assert transport.token_source.access_token() == "stub-token"


def test_chatgpt_backend_without_a_model_raises(stub_tokens):
    config = Config(pack="de", providers=("llm",), llm_backend="chatgpt")

    with pytest.raises(ConfigError, match="no default model.*`ankery status`"):
        build_deck_builder(config)


def test_chatgpt_section_reaches_the_chatgpt_transport_only(stub_tokens):
    params = {
        "chat-completions": {"temperature": 0.2},
        "chatgpt": {"reasoning": {"effort": "low"}},
    }
    transport = _transport(
        Config(
            pack="de",
            providers=("llm",),
            llm_backend="chatgpt",
            llm_model="gpt-5.5",
            llm_params=params,
        )
    )

    assert transport.params == {"reasoning": {"effort": "low"}}


def test_chatgpt_backend_silently_ignores_the_api_key(stub_tokens):
    config = Config(
        pack="de",
        providers=("llm",),
        llm_backend="chatgpt",
        llm_model="gpt-5.5",
        llm_api_key="sk-secret",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        transport = _transport(config)

    assert not hasattr(transport, "api_key")


def test_chatgpt_backend_needs_a_sign_in():
    config = Config(pack="de", providers=("llm",), llm_backend="chatgpt", llm_model="gpt-5.5")

    with pytest.raises(ConfigError, match="needs a ChatGPT sign-in"):
        build_deck_builder(config)


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
    builder = build_deck_builder(Config(pack="de", llm_api_key="sk-123", providers=("llm",)))
    assert _provider_named(builder, "llm").transport.api_key == "sk-123"


def test_build_deck_builder_wires_provider_and_sink():
    config = Config(
        pack="de",
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
    # The catch-all terminus is the engine-shipped neutral note.
    assert builder.catch_all_note.name == "Ankery Basic"

    provider = _provider_named(builder, "llm")
    assert isinstance(provider, LLMProvider)
    assert provider.transport.base_url == "http://llm.local/v1"
    assert provider.transport.model == "my-model"

    assert isinstance(builder.sink, AnkiConnectSink)
    assert builder.sink.base_url == "http://anki.local:8765"


def test_build_deck_builder_loads_the_packs_notes_and_style():
    builder = build_deck_builder(Config(pack="de"))

    assert [d.name for d in builder.note_definitions] == [
        "Ankery DE: Word", "Ankery DE: Noun", "Ankery DE: Phrase", "Ankery DE: Verb",
    ]
    assert ".card" in builder.style_css


def _write_note(directory: Path, stem: str, name: str, applies_to: str | None):
    directory.mkdir(parents=True, exist_ok=True)
    applies = f'applies_to = "{applies_to}"\n' if applies_to is not None else ""
    (directory / f"{stem}.toml").write_text(
        f'name = "{name}"\n{applies}[map]\nFront = "{{{{ term }}}}"\n', "utf-8"
    )


def test_notes_dir_merges_over_the_packs_notes_by_category(tmp_path):
    # A generic noun layout replaces the pack's "Ankery DE: Noun" for nouns; a new
    # category (adjective) is added; the pack's verb is left in place.
    _write_note(tmp_path, "noun", "Simple Noun", "noun")
    _write_note(tmp_path, "adj", "Simple Adjective", "adjective")
    builder = build_deck_builder(Config(pack="de", notes_dir=tmp_path))

    names = [d.name for d in builder.note_definitions]
    assert names == [
        "Ankery DE: Word", "Simple Noun", "Ankery DE: Phrase", "Ankery DE: Verb",
        "Simple Adjective",
    ]


def test_notes_dir_unset_leaves_the_packs_notes_alone():
    builder = build_deck_builder(Config(pack="de"))  # notes_dir is None by default

    assert [d.name for d in builder.note_definitions] == [
        "Ankery DE: Word", "Ankery DE: Noun", "Ankery DE: Phrase", "Ankery DE: Verb",
    ]


def test_notes_dir_with_duplicate_category_surfaces_as_config_error(tmp_path):
    _write_note(tmp_path, "a_noun", "Noun A", "noun")
    _write_note(tmp_path, "b_noun", "Noun B", "noun")
    with pytest.raises(ConfigError, match="both serve category 'noun'"):
        build_deck_builder(Config(pack="de", notes_dir=tmp_path))


def test_empty_chain_falls_back_to_the_packs_preferred_chain():
    # config.providers is () by default, so the pack's chain is used.
    builder = build_deck_builder(Config(pack="de"))

    assert [p.name for p in builder.providers] == ["netzverb", "llm"]


def test_build_deck_builder_honors_provider_order():
    builder = build_deck_builder(Config(pack="de", providers=("llm", "netzverb")))

    assert [p.name for p in builder.providers] == ["llm", "netzverb"]


def test_pack_local_provider_gets_options_from_lang_toml():
    # netzverb's timeout comes from the de pack's [provider_options], not Config.
    builder = build_deck_builder(Config(pack="de", providers=("netzverb",)))
    assert _provider_named(builder, "netzverb")._timeout == 15.0


def test_llm_provider_gets_the_pack_rendered_prompt():
    builder = build_deck_builder(Config(pack="de", providers=("llm",)))
    assert "German" in _provider_named(builder, "llm").system_prompt_for(None)


def test_build_deck_builder_rejects_unknown_provider():
    with pytest.raises(ConfigError, match="unknown provider 'nope'"):
        build_deck_builder(Config(pack="de", providers=("nope",)))


def test_build_deck_builder_requires_a_pack():
    with pytest.raises(ConfigError, match="no pack chosen"):
        build_deck_builder(Config())


def test_build_deck_builder_rejects_unknown_pack():
    with pytest.raises(ConfigError, match="no pack for 'zz'"):
        build_deck_builder(Config(pack="zz"))


def test_pack_selects_a_user_pack_via_packs_dir(tmp_path):
    pack_dir = tmp_path / "xx"
    (pack_dir / "notes").mkdir(parents=True)
    # The engine default prompt template is domain-neutral: it declares no
    # variables, so a pack riding it needs none to render.
    (pack_dir / "pack.toml").write_text(
        'name = "Examplish"\nproviders = ["llm"]\n[category]\nname = "pos"\n'
        '[pos.noun]\n[pos.noun.properties]\nplural = "plural"\n',
        "utf-8",
    )
    builder = build_deck_builder(Config(pack="xx", packs_dir=tmp_path))

    assert [p.name for p in builder.providers] == ["llm"]
    assert "Examplish" in _provider_named(builder, "llm").system_prompt_for(None)


def test_empty_chain_with_packless_chain_is_an_error(tmp_path):
    # A pack that declares no providers and a config that overrides none leaves
    # nothing to build.
    pack_dir = tmp_path / "yy"
    (pack_dir / "notes").mkdir(parents=True)
    (pack_dir / "pack.toml").write_text(
        'name = "Y"\nproviders = []\n[category]\nname = "pos"\n[pos.noun]\n', "utf-8"
    )

    with pytest.raises(ConfigError, match="no providers configured"):
        build_deck_builder(Config(pack="yy", packs_dir=tmp_path))


# ---------------------------------------------------------------------------
# Variables: seeded from the pack, overridden by the operator, typo-validated
# ---------------------------------------------------------------------------


def test_resolve_variables_applies_pack_default():
    # The operator supplies nothing; the de pack's target_language default seeds it.
    assert resolve_variables({}, load_pack("de")) == {"target_language": "en"}


def test_resolve_variables_override_wins_over_default():
    resolved = resolve_variables({"target_language": "fr"}, load_pack("de"))
    assert resolved == {"target_language": "fr"}


def test_resolve_variables_rejects_undeclared_key():
    with pytest.raises(ConfigError, match="does not declare variable"):
        resolve_variables({"bogus": "x"}, load_pack("de"))


def test_build_deck_builder_resolves_variables_into_providers():
    # The resolved bag (pack default for target_language) reaches the llm provider.
    builder = build_deck_builder(Config(pack="de", providers=("llm",)))
    assert _provider_named(builder, "llm").variables == {"target_language": "en"}


def test_build_deck_builder_override_variable_reaches_provider():
    builder = build_deck_builder(
        Config(pack="de", providers=("llm",), variables={"target_language": "french"})
    )
    assert _provider_named(builder, "llm").variables == {"target_language": "french"}


# ---------------------------------------------------------------------------
# Security warnings (warn, don't block)
# ---------------------------------------------------------------------------


def test_load_warns_on_world_readable_auth_file(tmp_path):
    auth = _write_auth(tmp_path, 'llm_api_key = "sk-secret"\n')
    auth.chmod(0o644)
    with pytest.warns(UserWarning, match="accessible to group/others") as record:
        Config.load(path=tmp_path / "absent.toml", auth_path=auth, environ={})

    assert record[0].filename == __file__  # blames the caller, not ankery


def test_load_silent_when_auth_file_locked_down(tmp_path):
    auth = _write_auth(tmp_path, 'llm_api_key = "sk-secret"\n')
    auth.chmod(0o600)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning would raise
        config = Config.load(path=tmp_path / "absent.toml", auth_path=auth, environ={})
    assert config.llm_api_key == "sk-secret"


def test_build_warns_on_api_key_over_plaintext_http_to_remote():
    config = Config(
        pack="de",
        providers=("llm",),
        llm_base_url="http://example.com:8080/v1",
        llm_api_key="sk-secret",
    )
    with pytest.warns(UserWarning, match="plaintext http") as record:
        build_deck_builder(config)

    assert record[0].filename == __file__  # blames the caller, not ankery


def test_build_silent_for_api_key_over_http_to_localhost():
    config = Config(
        pack="de",
        providers=("llm",),
        llm_base_url="http://localhost:8080/v1",
        llm_api_key="sk-secret",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_deck_builder(config)


def test_build_silent_for_api_key_over_https():
    config = Config(
        pack="de",
        providers=("llm",),
        llm_base_url="https://example.com/v1",
        llm_api_key="sk-secret",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_deck_builder(config)


def test_build_sink_passes_anki_settings():
    sink = build_sink(
        Config(
            anki_url="http://anki.local:8765",
            anki_timeout=3.0,
            anki_sync_timeout=90.0,
            allow_duplicate=True,
        )
    )

    assert isinstance(sink, AnkiConnectSink)
    assert sink.base_url == "http://anki.local:8765"
    assert sink.timeout == 3.0
    assert sink.sync_timeout == 90.0
    assert sink.allow_duplicate is True


def test_sync_collection_uses_the_configured_endpoint_without_a_pack(httpx_mock):
    httpx_mock.add_response(
        url="http://anki.local:8765", json={"result": None, "error": None}
    )

    sync_collection(Config(anki_url="http://anki.local:8765", anki_sync_timeout=45.0))

    [request] = httpx_mock.get_requests()
    assert request.extensions["timeout"]["read"] == 45.0


def test_sync_note_types_syncs_the_packs_notes_with_its_style(monkeypatch):
    seen: dict[str, object] = {}

    def fake_sync(self, definitions, *, default_css=""):
        seen["definitions"] = list(definitions)
        seen["default_css"] = default_css
        return SyncResult(["created"], {})

    monkeypatch.setattr(AnkiConnectSink, "sync_note_types", fake_sync)
    pack = load_pack("de")

    result = sync_note_types(Config(pack="de"))

    assert result == SyncResult(["created"], {})
    assert [d.name for d in seen["definitions"]] == [d.name for d in pack.notes]
    assert seen["default_css"] == pack.style_css


def test_sync_note_types_requires_a_pack():
    with pytest.raises(ConfigError, match="no pack chosen"):
        sync_note_types(Config())


def test_sync_note_types_unknown_pack_raises_config_error():
    with pytest.raises(ConfigError, match="zz"):
        sync_note_types(Config(pack="zz"))
