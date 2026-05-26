import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

from anki_deckbuilder.manager import DeckBuilder
from anki_deckbuilder.providers.llm import LLMProvider
from anki_deckbuilder.recipes import FieldMap, build_recipes, default_field_map
from anki_deckbuilder.sinks.ankiconnect import AnkiConnectSink

ENV_PREFIX = "ANKIDECK_"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "anki_deckbuilder" / "config.toml"


class ConfigError(Exception):
    """Raised when a config file is unreadable or holds unknown/invalid keys."""


@dataclass(frozen=True)
class Config:
    """All the values that were hardcoded across the slices, in one place.

    Plain dataclass rather than pydantic-settings (deliberately omitted): the
    settings are a handful of scalars, and `from_env` does the small amount of
    string parsing explicitly. The field-mapping function is not here because it
    is a callable, not a value — pass it to `build_deck_builder` instead.
    """

    # LLM provider. base_url is the OpenAI-compatible root; the defaults target
    # a local llama.cpp `llama-server` (port 8080), but any compatible server
    # works. llama-server ignores the model field and serves whatever GGUF is
    # loaded, so the model is just a non-empty placeholder here.
    llm_base_url: str = "http://localhost:8080/v1"
    llm_model: str = "local-model"
    llm_timeout: float = 30.0
    llm_request_json_format: bool = True
    # Bearer token for hosted OpenAI-compatible endpoints. None (the default)
    # sends no Authorization header, which is correct for local servers. A
    # secret: only ever read from the env, never the config file or a CLI flag.
    llm_api_key: str | None = None

    # AnkiConnect sink.
    anki_url: str = "http://localhost:8765"
    anki_timeout: float = 10.0
    allow_duplicate: bool = False

    # Note destination. `note_type` is the catch-all model for words that match
    # no part-of-speech route (adjectives, adverbs, function words). Nouns and
    # verbs route to their own models by part of speech; set either of these to
    # "" to send that part of speech to the catch-all instead. Names match the
    # models built by scripts/build_deck.py.
    deck: str = "Default"
    note_type: str = "Basic"
    noun_note_type: str = "Noun (DE)"
    verb_note_type: str = "Verb (DE)"
    tags: tuple[str, ...] = ()

    # Default language pair for lookups (a German -> English vocab builder).
    source_language: str = "de"
    target_language: str = "en"

    @classmethod
    def load(
        cls,
        *,
        path: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> "Config":
        """Resolve config across all layers: defaults < file < env.

        The file (TOML, at ``DEFAULT_CONFIG_PATH`` unless `path` overrides) holds
        set-once preferences; env vars override it for one-off runs. CLI flags,
        the final layer, are applied by the caller on top of this result.
        """
        file_path = DEFAULT_CONFIG_PATH if path is None else path
        base = replace(cls(), **_load_file(file_path))
        return cls.from_env(environ, base=base)

    @classmethod
    def from_env(
        cls,
        environ: dict[str, str] | None = None,
        *,
        base: "Config | None" = None,
    ) -> "Config":
        """Build a Config from environment variables, falling back to `base`.

        Variables are read from the ANKIDECK_ namespace, e.g.
        ANKIDECK_LLM_MODEL, ANKIDECK_DECK, ANKIDECK_TAGS (comma-separated).
        `base` supplies the fallback values (the bare defaults when omitted); it
        is how `load` layers env on top of the config file.
        """
        env = os.environ if environ is None else environ
        base = cls() if base is None else base

        def _str(key: str, fallback: str) -> str:
            return env.get(ENV_PREFIX + key, fallback)

        def _opt_str(key: str, fallback: str | None) -> str | None:
            return env.get(ENV_PREFIX + key, fallback)

        def _float(key: str, fallback: float) -> float:
            raw = env.get(ENV_PREFIX + key)
            return fallback if raw is None else float(raw)

        def _bool(key: str, fallback: bool) -> bool:
            raw = env.get(ENV_PREFIX + key)
            if raw is None:
                return fallback
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        def _tags(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
            raw = env.get(ENV_PREFIX + key)
            if raw is None:
                return fallback
            return tuple(t.strip() for t in raw.split(",") if t.strip())

        return cls(
            llm_base_url=_str("LLM_URL", base.llm_base_url),
            llm_model=_str("LLM_MODEL", base.llm_model),
            llm_timeout=_float("LLM_TIMEOUT", base.llm_timeout),
            llm_request_json_format=_bool("LLM_JSON_FORMAT", base.llm_request_json_format),
            llm_api_key=_opt_str("LLM_API_KEY", base.llm_api_key),
            anki_url=_str("ANKI_URL", base.anki_url),
            anki_timeout=_float("ANKI_TIMEOUT", base.anki_timeout),
            allow_duplicate=_bool("ALLOW_DUPLICATE", base.allow_duplicate),
            deck=_str("DECK", base.deck),
            note_type=_str("NOTE_TYPE", base.note_type),
            noun_note_type=_str("NOUN_NOTE_TYPE", base.noun_note_type),
            verb_note_type=_str("VERB_NOTE_TYPE", base.verb_note_type),
            tags=_tags("TAGS", base.tags),
            source_language=_str("SOURCE_LANG", base.source_language),
            target_language=_str("TARGET_LANG", base.target_language),
        )


def _load_file(path: Path) -> dict:
    """Read a TOML config file into a dict of Config field overrides.

    A missing file is not an error — it just means "use defaults". Keys must
    match Config field names; unknown keys are rejected so typos surface loudly
    rather than being silently ignored. `llm_api_key` is refused on purpose: a
    secret belongs in the environment, never on disk in this file.
    """
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc

    allowed = {f.name for f in fields(Config)} - {"llm_api_key"}
    unknown = set(raw) - allowed
    if unknown:
        if "llm_api_key" in unknown:
            raise ConfigError(
                f"{path}: llm_api_key may not be set in the config file; "
                "use the ANKIDECK_LLM_API_KEY environment variable instead."
            )
        raise ConfigError(f"{path}: unknown config keys: {', '.join(sorted(unknown))}")

    # TOML gives native types; only nudge the two that Config types differently.
    if isinstance(raw.get("tags"), list):
        raw["tags"] = tuple(raw["tags"])
    for key in ("llm_timeout", "anki_timeout"):
        if key in raw:
            raw[key] = float(raw[key])
    return raw


def build_deck_builder(config: Config, *, map_fields: FieldMap | None = None) -> DeckBuilder:
    """Wire the provider chain and sink from config into a ready DeckBuilder."""
    provider = LLMProvider(
        base_url=config.llm_base_url,
        model=config.llm_model,
        timeout=config.llm_timeout,
        request_json_format=config.llm_request_json_format,
        api_key=config.llm_api_key,
    )
    sink = AnkiConnectSink(
        base_url=config.anki_url,
        timeout=config.anki_timeout,
        allow_duplicate=config.allow_duplicate,
    )
    return DeckBuilder(
        [provider],
        sink,
        deck=config.deck,
        note_type=config.note_type,
        map_fields=map_fields or default_field_map,
        recipes=build_recipes(
            noun_note_type=config.noun_note_type,
            verb_note_type=config.verb_note_type,
        ),
        tags=list(config.tags),
    )
