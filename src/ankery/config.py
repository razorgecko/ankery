import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from pathlib import Path

from ankery.manager import DeckBuilder
from ankery.providers.base import WordProvider
from ankery.providers.llm import LLMProvider
from ankery.providers.verbformen import VerbformenProvider
from ankery.notedef import (
    FieldMap,
    NoteDefinitionError,
    default_field_map,
    load_note_definitions,
)
from ankery.sinks.ankiconnect import AnkiConnectSink

ENV_PREFIX = "ANKERY_"


def _config_dir() -> Path:
    """The ankery config directory, honoring the XDG base-dir spec.

    Read at call time (not as an import-time constant) so an env change — or a
    test pointing XDG_CONFIG_HOME at a tmp dir — takes effect. Per the spec, an
    unset, empty, or non-absolute XDG_CONFIG_HOME falls back to ~/.config.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return base / "ankery"

# The keys config.toml refuses and auth.toml requires — the split that keeps
# config.toml safe to share/commit while the secret sits in a sibling file.
SECRET_KEYS = {"llm_api_key"}


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

    # Provider chain, tried in fallback order: the first name with a result for
    # a word wins. Names index the registry in `build_deck_builder`. The German
    # default puts verbformen first (authoritative scraped gender/declension/
    # conjugation) with the LLM behind it to catch what it misses or non-German
    # words. Set to a single name to use one provider, or reorder to taste.
    providers: tuple[str, ...] = ("verbformen", "llm")

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

    # verbformen.com scraping provider.
    verbformen_timeout: float = 15.0

    # AnkiConnect sink.
    anki_url: str = "http://localhost:8765"
    anki_timeout: float = 10.0
    allow_duplicate: bool = False

    # Note destination. `note_type` is the catch-all model for words that match
    # no note definition (adjectives, adverbs, function words). Part-of-speech
    # routing to specific models (e.g. "Noun (DE)", "Verb (DE)") is owned by the
    # note definitions in notes/*.toml (their `applies_to`), not by config.
    deck: str = "Default"
    note_type: str = "Basic"
    tags: tuple[str, ...] = ()

    # Note definitions. `notes_dir`, when set, is merged over the bundled
    # notes/ by file stem — a custom file with the same stem replaces the
    # bundled one, new stems are added. `notes`, when non-empty, selects a
    # subset of definitions by stem (e.g. ("noun_de", "verb_de")) in the order
    # listed, so a collection can ship many note types and a run use only those
    # for the language at hand. Empty `notes` loads the whole merged set.
    notes_dir: Path | None = None
    notes: tuple[str, ...] = ()

    # Default language pair for lookups (a German -> English vocab builder).
    source_language: str = "de"
    target_language: str = "en"

    @classmethod
    def load(
        cls,
        *,
        path: Path | None = None,
        auth_path: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> "Config":
        """Resolve config across all layers: defaults < config.toml < auth.toml < env.

        config.toml (in ``_config_dir()`` unless `path` overrides) holds
        set-once preferences and refuses the api key, so it stays safe to share.
        auth.toml (its sibling in the same dir unless `auth_path` overrides)
        holds only the secret. Both are read with the same tomllib; env vars
        override both. CLI flags, the final layer, are applied by the caller.
        """
        config_path = _config_dir() / "config.toml" if path is None else path
        auth_path = _config_dir() / "auth.toml" if auth_path is None else auth_path
        # The two dicts are disjoint by construction (inverse allow-lists around
        # SECRET_KEYS), so this merge can never clash.
        overrides = {**_load_config_file(config_path), **_load_auth_file(auth_path)}
        return cls.from_env(environ, base=replace(cls(), **overrides))

    @classmethod
    def from_env(
        cls,
        environ: dict[str, str] | None = None,
        *,
        base: "Config | None" = None,
    ) -> "Config":
        """Build a Config from environment variables, falling back to `base`.

        Variables are read from the ANKERY_ namespace, e.g.
        ANKERY_LLM_MODEL, ANKERY_DECK, ANKERY_TAGS (comma-separated).
        `base` supplies the fallback values (the bare defaults when omitted); it
        is how `load` layers env on top of the config file.
        """
        env = os.environ if environ is None else environ
        base = cls() if base is None else base

        def _str(key: str, fallback: str) -> str:
            return env.get(ENV_PREFIX + key, fallback)

        def _opt_str(key: str, fallback: str | None) -> str | None:
            return env.get(ENV_PREFIX + key, fallback)

        def _opt_path(key: str, fallback: Path | None) -> Path | None:
            raw = env.get(ENV_PREFIX + key)
            return fallback if raw is None else Path(raw).expanduser()

        def _float(key: str, fallback: float) -> float:
            raw = env.get(ENV_PREFIX + key)
            return fallback if raw is None else float(raw)

        def _bool(key: str, fallback: bool) -> bool:
            raw = env.get(ENV_PREFIX + key)
            if raw is None:
                return fallback
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        def _csv(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
            raw = env.get(ENV_PREFIX + key)
            if raw is None:
                return fallback
            return tuple(t.strip() for t in raw.split(",") if t.strip())

        return cls(
            providers=_csv("PROVIDERS", base.providers),
            llm_base_url=_str("LLM_URL", base.llm_base_url),
            llm_model=_str("LLM_MODEL", base.llm_model),
            llm_timeout=_float("LLM_TIMEOUT", base.llm_timeout),
            llm_request_json_format=_bool("LLM_JSON_FORMAT", base.llm_request_json_format),
            llm_api_key=_opt_str("LLM_API_KEY", base.llm_api_key),
            verbformen_timeout=_float("VERBFORMEN_TIMEOUT", base.verbformen_timeout),
            anki_url=_str("ANKI_URL", base.anki_url),
            anki_timeout=_float("ANKI_TIMEOUT", base.anki_timeout),
            allow_duplicate=_bool("ALLOW_DUPLICATE", base.allow_duplicate),
            deck=_str("DECK", base.deck),
            note_type=_str("NOTE_TYPE", base.note_type),
            tags=_csv("TAGS", base.tags),
            notes_dir=_opt_path("NOTES_DIR", base.notes_dir),
            notes=_csv("NOTES", base.notes),
            source_language=_str("SOURCE_LANG", base.source_language),
            target_language=_str("TARGET_LANG", base.target_language),
        )


def _read_toml(path: Path) -> dict:
    """Read a TOML file to a raw dict; a missing file means no overrides."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc


def _load_config_file(path: Path) -> dict:
    """Read config.toml into a dict of Config field overrides, minus the secret.

    Keys must match Config field names; unknown keys are rejected so typos
    surface loudly rather than being silently ignored. The api key is refused
    here on purpose so config.toml stays safe to share — it belongs in auth.toml.
    """
    raw = _read_toml(path)
    allowed = {f.name for f in fields(Config)} - SECRET_KEYS
    unknown = set(raw) - allowed
    if unknown:
        if unknown & SECRET_KEYS:
            raise ConfigError(
                f"{path}: llm_api_key may not be set in config.toml; put it in "
                "auth.toml (or the ANKERY_LLM_API_KEY environment variable) instead."
            )
        raise ConfigError(f"{path}: unknown config keys: {', '.join(sorted(unknown))}")

    # TOML gives native types; only nudge the ones Config types differently.
    for key in ("tags", "providers", "notes"):
        if isinstance(raw.get(key), list):
            raw[key] = tuple(raw[key])
    for key in ("llm_timeout", "anki_timeout", "verbformen_timeout"):
        if key in raw:
            raw[key] = float(raw[key])
    if isinstance(raw.get("notes_dir"), str):
        raw["notes_dir"] = Path(raw["notes_dir"]).expanduser()
    return raw


def _load_auth_file(path: Path) -> dict:
    """Read auth.toml — the secret-only sibling of config.toml.

    The inverse of `_load_config_file`'s allow-list: only SECRET_KEYS are
    accepted. Any ordinary config key is rejected with a pointer back to
    config.toml, so the two files keep their split (shareable preferences vs.
    the secret) instead of drifting into two places that set the same things.
    """
    raw = _read_toml(path)
    unknown = set(raw) - SECRET_KEYS
    if unknown:
        raise ConfigError(
            f"{path}: only {', '.join(sorted(SECRET_KEYS))} belongs in auth.toml; "
            f"move {', '.join(sorted(unknown))} to config.toml."
        )
    return raw


# Provider name -> how to build it from config. Each entry is the seam the
# `providers` chain selects from; adding a provider is registering it here.
def _build_llm(config: "Config") -> WordProvider:
    return LLMProvider(
        base_url=config.llm_base_url,
        model=config.llm_model,
        timeout=config.llm_timeout,
        request_json_format=config.llm_request_json_format,
        api_key=config.llm_api_key,
    )


def _build_verbformen(config: "Config") -> WordProvider:
    return VerbformenProvider(timeout=config.verbformen_timeout)


PROVIDER_REGISTRY: dict[str, Callable[["Config"], WordProvider]] = {
    "llm": _build_llm,
    "verbformen": _build_verbformen,
}


def build_deck_builder(config: Config, *, map_fields: FieldMap | None = None) -> DeckBuilder:
    """Wire the provider chain and sink from config into a ready DeckBuilder.

    `config.providers` names the chain in fallback order; each name is looked up
    in `PROVIDER_REGISTRY`. An unknown name or an empty chain is a config error.
    """
    if not config.providers:
        raise ConfigError("no providers configured; set `providers` to at least one of: "
                          f"{', '.join(sorted(PROVIDER_REGISTRY))}.")
    providers = []
    for name in config.providers:
        try:
            build = PROVIDER_REGISTRY[name]
        except KeyError:
            raise ConfigError(
                f"unknown provider {name!r}; known providers: "
                f"{', '.join(sorted(PROVIDER_REGISTRY))}."
            ) from None
        providers.append(build(config))
    try:
        note_definitions = load_note_definitions(config.notes_dir, config.notes)
    except NoteDefinitionError as exc:
        raise ConfigError(str(exc)) from exc
    sink = AnkiConnectSink(
        base_url=config.anki_url,
        timeout=config.anki_timeout,
        allow_duplicate=config.allow_duplicate,
    )
    return DeckBuilder(
        providers,
        sink,
        deck=config.deck,
        note_type=config.note_type,
        map_fields=map_fields or default_field_map,
        note_definitions=note_definitions,
        tags=list(config.tags),
    )
