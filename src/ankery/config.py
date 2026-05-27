import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from pathlib import Path

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
    """Infrastructure settings only — the engine names no language.

    Everything language-specific (grammar, guidance, normalization, note
    layouts, the preferred provider chain) lives in the language pack, selected
    by `source_language` and loaded at wiring time (see pack.py). `Config` keeps
    only where things are and how to reach them: the Anki/LLM endpoints, the
    deck, the catch-all note type, the language pair, and the user pack
    override dir.
    """

    # Provider chain in fallback order: the first name with a result wins. Empty
    # (the default) means "use the pack's preferred chain"; set it to override.
    # Names resolve against the pack's own providers first, then the engine's
    # cross-language registry. --provider sets this.
    providers: tuple[str, ...] = ()

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
    # no note definition in the pack. Part-of-speech routing to specific models
    # is owned by the pack's note definitions (their `applies_to`), not config.
    deck: str = "Default"
    note_type: str = "Basic"
    tags: tuple[str, ...] = ()

    # `notes_dir`, when set, holds extra note layouts (*.toml, same shape as a
    # pack's notes/) merged over the active pack's notes by part of speech: a
    # file here whose `applies_to` matches a pack note replaces it, one serving a
    # new POS is added. This is the one note channel that is not pack-specific,
    # so language-agnostic layouts (word + translation, no features) can be
    # shared across packs. See notedef.merge_note_definitions.
    notes_dir: Path | None = None

    # Language selection. `source_language` is the pack code to load (e.g. "de");
    # `target_language` is where translations go. `langs_dir`, when set, is the
    # user pack directory: a pack at `<langs_dir>/<code>/` overrides the bundled
    # one of the same code.
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
        """Resolve config across all layers: defaults < config.toml < auth.toml < env.

        config.toml holds set-once preferences and refuses the api key, so it
        stays safe to share. auth.toml is its sibling holding only the secret.
        Which file each reads is resolved in three steps: an explicit `path` /
        `auth_path` (the CLI-flag channel) wins; failing that the
        `ANKERY_CONFIG` / `ANKERY_AUTH` env vars; failing both, the default name
        in `_config_dir()`. CLI flags, the final layer, are applied by the caller
        (`__main__`) after this returns.
        """
        env = os.environ if environ is None else environ
        if path is None:
            raw = env.get(ENV_PREFIX + "CONFIG")
            path = Path(raw).expanduser() if raw else None
        if auth_path is None:
            raw = env.get(ENV_PREFIX + "AUTH")
            auth_path = Path(raw).expanduser() if raw else None
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
        """Overlay the only env-supplied field — the secret — onto `base`.

        Env carries just the API-key override (`ANKERY_LLM_API_KEY`, a secret
        that must not sit in a file) and, in `load`, the config/auth file
        *paths*. Everything else is set in config.toml or via CLI flags, so other
        `ANKERY_*` variables are deliberately ignored here. `base` supplies the
        fallback; this is how `load` layers env on the file.
        """
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


# Engine-level provider registry: only cross-language providers live here. Each
# builder takes (config, pack); language-specific providers come from the pack's
# own provider.py and are merged over this at wiring time. The LLM builder
# renders its system prompt from the pack, which is what makes it work for any
# language.
def _build_llm(config: "Config", pack: LanguagePack) -> WordProvider:
    return LLMProvider(
        base_url=config.llm_base_url,
        model=config.llm_model,
        system_prompt=render_system_prompt(pack),
        source_language=pack.code,
        target_language=config.target_language,
        timeout=config.llm_timeout,
        request_json_format=config.llm_request_json_format,
        api_key=config.llm_api_key,
    )


ProviderBuilder = Callable[["Config", LanguagePack], WordProvider]

PROVIDER_REGISTRY: dict[str, ProviderBuilder] = {
    "llm": _build_llm,
}


def build_deck_builder(config: Config) -> DeckBuilder:
    """Resolve the language pack and wire the chain, sink, and DeckBuilder from it.

    `config.source_language` selects the pack. The chain is `config.providers`
    when set, else the pack's preferred chain; each name resolves against the
    pack's own providers first, then `PROVIDER_REGISTRY`. An unknown name or an
    empty chain is a config error.
    """
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
    )
