import pytest

from anki_deckbuilder import config as config_mod
from anki_deckbuilder.config import Config, ConfigError, build_deck_builder
from anki_deckbuilder.manager import DeckBuilder, default_field_map
from anki_deckbuilder.providers.llm import LLMProvider
from anki_deckbuilder.sinks.ankiconnect import AnkiConnectSink


@pytest.fixture(autouse=True)
def _isolate_default_auth(monkeypatch, tmp_path):
    # Keep Config.load() hermetic: tests that don't pass auth_path must not read
    # the developer's real ~/.config/anki_deckbuilder/auth.toml. Point the
    # default at an absent file under tmp instead.
    monkeypatch.setattr(config_mod, "DEFAULT_AUTH_PATH", tmp_path / "_no_auth.toml")


def _write(tmp_path, text: str):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def _write_auth(tmp_path, text: str):
    path = tmp_path / "auth.toml"
    path.write_text(text)
    return path


def test_from_env_uses_defaults_when_unset():
    config = Config.from_env({})

    assert config.llm_base_url == "http://localhost:8080/v1"
    assert config.anki_url == "http://localhost:8765"
    assert config.deck == "Default"
    assert config.note_type == "Basic"
    assert config.tags == ()
    assert config.allow_duplicate is False
    assert config.source_language == "de"


def test_from_env_overrides_scalars():
    env = {
        "ANKIDECK_LLM_URL": "https://api.groq.com/openai/v1",
        "ANKIDECK_LLM_MODEL": "llama-3.3-70b",
        "ANKIDECK_LLM_TIMEOUT": "5.5",
        "ANKIDECK_ANKI_URL": "http://anki.local:8765",
        "ANKIDECK_DECK": "German::Verbs",
        "ANKIDECK_NOTE_TYPE": "Cloze",
        "ANKIDECK_TARGET_LANG": "ru",
    }
    config = Config.from_env(env)

    assert config.llm_base_url == "https://api.groq.com/openai/v1"
    assert config.llm_model == "llama-3.3-70b"
    assert config.llm_timeout == 5.5
    assert config.anki_url == "http://anki.local:8765"
    assert config.deck == "German::Verbs"
    assert config.note_type == "Cloze"
    assert config.target_language == "ru"


def test_from_env_parses_bools():
    assert Config.from_env({"ANKIDECK_ALLOW_DUPLICATE": "true"}).allow_duplicate is True
    assert Config.from_env({"ANKIDECK_ALLOW_DUPLICATE": "1"}).allow_duplicate is True
    assert Config.from_env({"ANKIDECK_ALLOW_DUPLICATE": "no"}).allow_duplicate is False
    assert Config.from_env({"ANKIDECK_LLM_JSON_FORMAT": "off"}).llm_request_json_format is False


def test_from_env_parses_comma_separated_tags():
    config = Config.from_env({"ANKIDECK_TAGS": "auto, de , vocab"})

    assert config.tags == ("auto", "de", "vocab")


def test_from_env_layers_on_top_of_base():
    base = Config(deck="FromFile", llm_model="file-model")
    config = Config.from_env({"ANKIDECK_DECK": "FromEnv"}, base=base)

    assert config.deck == "FromEnv"  # env wins
    assert config.llm_model == "file-model"  # base shows through where env is silent


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


def test_load_env_overrides_file(tmp_path):
    path = _write(tmp_path, 'deck = "FromFile"\nnote_type = "Cloze"\n')
    config = Config.load(path=path, environ={"ANKIDECK_DECK": "FromEnv"})

    assert config.deck == "FromEnv"  # env beats file
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
    # The api_key message must win over the generic unknown-keys message.
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
        environ={"ANKIDECK_LLM_API_KEY": "sk-from-env"},
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


def test_load_reads_bool_from_file(tmp_path):
    path = _write(tmp_path, "llm_request_json_format = false\n")
    config = Config.load(path=path, environ={})

    assert config.llm_request_json_format is False


def test_load_wraps_malformed_toml(tmp_path):
    path = _write(tmp_path, "deck = \n")  # value missing -> TOMLDecodeError
    with pytest.raises(ConfigError, match="Could not read config file"):
        Config.load(path=path, environ={})


def test_from_env_reads_process_environ_when_unset(monkeypatch):
    monkeypatch.setenv("ANKIDECK_DECK", "FromProcessEnv")
    config = Config.from_env()  # environ=None -> falls back to os.environ

    assert config.deck == "FromProcessEnv"


def test_api_key_read_from_env_only():
    assert Config.from_env({}).llm_api_key is None
    assert Config.from_env({"ANKIDECK_LLM_API_KEY": "sk-123"}).llm_api_key == "sk-123"


def test_build_deck_builder_passes_api_key():
    builder = build_deck_builder(Config(llm_api_key="sk-123"))
    [provider] = builder.providers
    assert provider.api_key == "sk-123"


def test_build_deck_builder_wires_provider_and_sink():
    config = Config(
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

    [provider] = builder.providers
    assert isinstance(provider, LLMProvider)
    assert provider.base_url == "http://llm.local/v1"
    assert provider.model == "my-model"

    assert isinstance(builder.sink, AnkiConnectSink)
    assert builder.sink.base_url == "http://anki.local:8765"


def test_build_deck_builder_accepts_custom_field_map():
    custom = lambda info: {"Word": info.word}
    builder = build_deck_builder(Config(), map_fields=custom)

    assert builder.map_fields is custom
