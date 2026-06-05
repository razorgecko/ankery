import os
import stat
import tomllib
import warnings
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from ankery.manager import DeckBuilder
from ankery.notedef import (
    NoteDefinitionError,
    load_notes_from_dir,
    merge_note_definitions,
)
from ankery.pack import LanguagePack, PackError, load_pack
from ankery.prompts import render_system_prompt
from ankery.providers.base import WordProvider
from ankery.providers.llm import LLMProvider
from ankery.sinks.ankiconnect import AnkiConnectSink

ENV_PREFIX = "ANKERY_"


def _config_dir() -> Path:
    """Return the ankery config dir, honoring XDG. Read at call time so tests can redirect it."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return base / "ankery"

SECRET_KEYS = {"llm_api_key"}


class ConfigError(Exception):
    """Raised when a config file is unreadable or holds unknown/invalid keys."""


@dataclass(frozen=True)
class Config:
    """Infrastructure settings — endpoints, deck, language pair. Language behavior lives in the pack."""

    # Empty means use the pack's preferred chain.
    providers: tuple[str, ...] = ()

    llm_base_url: str = "http://localhost:8080/v1"
    llm_model: str = "local-model"
    llm_timeout: float = 30.0
    llm_request_json_format: bool = True
    # Bearer token for hosted endpoints; None sends no Authorization header.
    llm_api_key: str | None = None

    anki_url: str = "http://localhost:8765"
    anki_timeout: float = 10.0
    allow_duplicate: bool = False

    # `note_type` is the catch-all for words that match no pack note definition.
    deck: str = "Default"
    note_type: str = "Basic"
    tags: tuple[str, ...] = ()

    # Extra note layouts merged over the pack's by category; language-agnostic layouts live here.
    notes_dir: Path | None = None

    source_language: str = "de"
    target_language: str = "en"
    langs_dir: Path | None = None

    @classmethod
    def load(
        cls,
        *,
        path: Path | None = None,
        auth_path: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> "Config":
        """Resolve config: defaults < config.toml < auth.toml < env. CLI flags applied by caller."""
        env = os.environ if environ is None else environ
        if path is None:
            raw = env.get(ENV_PREFIX + "CONFIG")
            path = Path(raw).expanduser() if raw else None
        if auth_path is None:
            raw = env.get(ENV_PREFIX + "AUTH")
            auth_path = Path(raw).expanduser() if raw else None
        config_path = _config_dir() / "config.toml" if path is None else path
        auth_path = _config_dir() / "auth.toml" if auth_path is None else auth_path
        overrides = {**_load_config_file(config_path), **_load_auth_file(auth_path)}
        return cls.from_env(environ, base=replace(cls(), **overrides))

    @classmethod
    def from_env(
        cls,
        environ: dict[str, str] | None = None,
        *,
        base: "Config | None" = None,
    ) -> "Config":
        """Overlay `ANKERY_LLM_API_KEY` from env onto `base`."""
        env = os.environ if environ is None else environ
        base = cls() if base is None else base
        return replace(
            base,
            llm_api_key=env.get(ENV_PREFIX + "LLM_API_KEY", base.llm_api_key),
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
    """Read config.toml; rejects unknown keys and refuses the secret (belongs in auth.toml)."""
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

    for key in ("tags", "providers"):
        if isinstance(raw.get(key), list):
            raw[key] = tuple(raw[key])
    for key in ("llm_timeout", "anki_timeout"):
        if key in raw:
            raw[key] = float(raw[key])
    for key in ("langs_dir", "notes_dir"):
        if isinstance(raw.get(key), str):
            raw[key] = Path(raw[key]).expanduser()
    return raw


def _load_auth_file(path: Path) -> dict:
    """Read auth.toml; accepts only SECRET_KEYS and rejects anything else."""
    raw = _read_toml(path)
    unknown = set(raw) - SECRET_KEYS
    if unknown:
        raise ConfigError(
            f"{path}: only {', '.join(sorted(SECRET_KEYS))} belongs in auth.toml; "
            f"move {', '.join(sorted(unknown))} to config.toml."
        )
    if raw:
        _warn_if_world_readable(path)
    return raw


def _warn_if_world_readable(path: Path) -> None:
    """Warn if a secret-bearing file is readable by group or others (POSIX only)."""
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        warnings.warn(
            f"{path} holds a secret but is accessible to group/others; "
            f"restrict it with `chmod 600 {path}`.",
            stacklevel=2,
        )


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _build_llm(config: "Config", pack: LanguagePack) -> WordProvider:
    if config.llm_api_key:
        parts = urlsplit(config.llm_base_url)
        if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
            warnings.warn(
                f"sending the LLM API key over plaintext http to {parts.hostname}; "
                "the token is exposed in transit — use https for remote endpoints.",
                stacklevel=2,
            )
    return LLMProvider(
        base_url=config.llm_base_url,
        model=config.llm_model,
        system_prompt_for=partial(render_system_prompt, pack),
        source_language=pack.code,
        target_language=config.target_language,
        category_key=pack.category_label,
        timeout=config.llm_timeout,
        request_json_format=config.llm_request_json_format,
        api_key=config.llm_api_key,
    )


ProviderBuilder = Callable[["Config", LanguagePack], WordProvider]

PROVIDER_REGISTRY: dict[str, ProviderBuilder] = {
    "llm": _build_llm,
}


def build_deck_builder(config: Config) -> DeckBuilder:
    """Resolve the pack from `source_language` and wire providers, notes, sink, and builder."""
    try:
        pack = load_pack(config.source_language, config.langs_dir)
    except PackError as exc:
        raise ConfigError(str(exc)) from exc

    registry: dict[str, ProviderBuilder] = {**PROVIDER_REGISTRY, **pack.provider_builders}
    chain = config.providers or pack.providers
    if not chain:
        raise ConfigError(
            "no providers configured; set `providers` or give the pack a default chain."
        )
    providers = []
    for name in chain:
        try:
            build = registry[name]
        except KeyError:
            raise ConfigError(
                f"unknown provider {name!r} for pack {pack.code!r}; known: "
                f"{', '.join(sorted(registry))}."
            ) from None
        providers.append(build(config, pack))

    notes = pack.notes
    if config.notes_dir is not None:
        try:
            extra = load_notes_from_dir(config.notes_dir)
        except NoteDefinitionError as exc:
            raise ConfigError(str(exc)) from exc
        notes = merge_note_definitions(pack.notes, extra)

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
        style_css=pack.style_css,
        normalize=pack.normalize,
        note_definitions=notes,
        tags=list(config.tags),
        category_names=sorted(pack.categories),
    )
