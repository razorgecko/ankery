import json
import os
import stat
import tomllib
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ankery.defaults import catch_all_model_name
from ankery.manager import DeckBuilder
from ankery.notedef import (
    NoteDefinitionError,
    load_notes_from_dir,
    merge_note_definitions,
)
from ankery.pack import Pack, PackError, load_pack
from ankery.prompts import render_system_prompt, render_user_prompt
from ankery.providers.base import Provider
from ankery.providers.llm import (
    ChatCompletionsTransport,
    ChatGPTTransport,
    LLMProvider,
    TokenSource,
    Transport,
)
from ankery.signin import (
    SignInError,
    StoredTokens,
    TokenStore,
    is_signed_in,
    tokens_path,
)
from ankery.sinks.ankiconnect import AnkiConnectSink
from ankery.sinks.base import SyncResult

ENV_PREFIX = "ANKERY_"


def _config_dir() -> Path:
    """Return the ankery config dir, honoring XDG. Read at call time so tests can redirect it."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return base / "ankery"

# Skipped when attributing a warning, so it names the first caller outside ankery.
_INTERNAL_FILES = (str(Path(__file__).parent) + os.sep,)

SECRET_KEYS = {"llm_api_key"}
LLM_PARAMS_FILE = "llm_params.json"


class ConfigError(Exception):
    """Raised when a config file is unreadable or holds unknown/invalid keys."""


@dataclass(frozen=True)
class Config:
    """Infrastructure settings — endpoints, deck, pack selector, variables."""

    # Empty means use the pack's preferred chain.
    providers: tuple[str, ...] = ()

    llm_backend: str = ChatCompletionsTransport.name
    # chat-completions only. None: the transport's DEFAULT_BASE_URL.
    llm_base_url: str | None = None
    # None: the transport's DEFAULT_MODEL.
    llm_model: str | None = None
    llm_timeout: float = 30.0
    # Per-backend request parameter overrides, read from llm_params.json only.
    llm_params: dict[str, dict] = field(default_factory=dict)
    # Bearer token for hosted endpoints; None sends no Authorization header.
    llm_api_key: str | None = None

    anki_url: str = "http://localhost:8765"
    anki_timeout: float = 10.0
    anki_sync_timeout: float = 60.0
    allow_duplicate: bool = False

    # `note_type` is the catch-all model for terms that match no pack note
    # definition; defaults to the engine-owned "Ankery Basic". --note-type repoints
    # it at a foreign model (e.g. Anki's stock "Basic").
    deck: str = "Default"
    note_type: str = field(default_factory=catch_all_model_name)
    tags: tuple[str, ...] = ()

    # Extra note layouts merged over the pack's by category.
    notes_dir: Path | None = None

    # The pack selector — a pack code, taken literally (not a language code).
    # None until the operator chooses one; there is no default pack.
    pack: str | None = None
    # Opaque operator-supplied variables the pack consumes.
    variables: dict[str, str] = field(default_factory=dict)
    packs_dir: Path | None = None

    @classmethod
    def load(
        cls,
        *,
        path: Path | None = None,
        auth_path: Path | None = None,
        environ: dict[str, str] | None = None,
        with_auth: bool = True,
    ) -> "Config":
        """Resolve config: defaults < config.toml < auth.toml < env, with
        llm_params.json read alongside config.toml. Without `with_auth`, stops at
        config.toml: no auth file is read and no secret set."""
        env = os.environ if environ is None else environ
        if path is None:
            raw = env.get(ENV_PREFIX + "CONFIG")
            path = Path(raw).expanduser() if raw else None
        config_path = _config_dir() / "config.toml" if path is None else path
        base = replace(
            cls(),
            **_load_config_file(config_path),
            llm_params=_load_llm_params(_config_dir() / LLM_PARAMS_FILE),
        )
        if not with_auth:
            return base
        if auth_path is None:
            raw = env.get(ENV_PREFIX + "AUTH")
            auth_path = Path(raw).expanduser() if raw else None
        auth_path = _config_dir() / "auth.toml" if auth_path is None else auth_path
        return cls.from_env(environ, base=replace(base, **_load_auth_file(auth_path)))

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
    config_keys = {f.name for f in fields(Config)}
    allowed = config_keys - SECRET_KEYS - {"llm_params"}
    unknown = set(raw) - allowed
    if unknown:
        if unknown & SECRET_KEYS:
            raise ConfigError(
                f"{path}: llm_api_key may not be set in config.toml; put it in "
                "auth.toml (or the ANKERY_LLM_API_KEY environment variable) instead."
            )
        if "llm_params" in unknown:
            raise ConfigError(
                f"{path}: llm_params may not be set in config.toml; put it in "
                f"{LLM_PARAMS_FILE} in the config directory instead."
            )
        raise ConfigError(f"{path}: unknown config keys: {', '.join(sorted(unknown))}")

    backend = raw.get("llm_backend")
    if backend is not None and backend not in TRANSPORTS:
        raise ConfigError(f"{path}: {_unknown_backend(backend)}")

    for key in ("tags", "providers"):
        if isinstance(raw.get(key), list):
            raw[key] = tuple(raw[key])
    for key in ("llm_timeout", "anki_timeout", "anki_sync_timeout"):
        if key in raw:
            raw[key] = float(raw[key])
    for key in ("packs_dir", "notes_dir"):
        if isinstance(raw.get(key), str):
            raw[key] = Path(raw[key]).expanduser()
    # The [variables] table is an opaque key/value bag; coerce values to str so a
    # TOML number/bool is carried as text.
    if isinstance(raw.get("variables"), dict):
        # A TOML table header captures every key after it, so an engine key written
        # below `[variables]` silently lands inside the bag. Catch that here and
        # point at the cause: ordering.
        misplaced = set(raw["variables"]) & config_keys
        if misplaced:
            raise ConfigError(
                f"{path}: {', '.join(sorted(misplaced))} appear under [variables] "
                "but are engine config keys. A TOML table header captures every key "
                "after it, so the [variables] table must come after all top-level "
                "keys; move these above it."
            )
        raw["variables"] = {k: str(v) for k, v in raw["variables"].items()}
    return raw


def _load_llm_params(path: Path) -> dict[str, dict]:
    """Read llm_params.json, a backend -> request parameters map; a missing file
    means none. Every section is checked, not only the active backend's."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: the top level must be an object keyed by llm backend."
        )
    for backend, params in raw.items():
        transport = TRANSPORTS.get(backend)
        if transport is None:
            raise ConfigError(
                f"{path}: unknown llm backend {backend!r}; known: "
                f"{', '.join(sorted(TRANSPORTS))}."
            )
        if not isinstance(params, dict):
            raise ConfigError(f"{path}: section {backend!r} must be an object.")
        owned = params.keys() & transport.OWNED_KEYS
        if owned:
            raise ConfigError(
                f"{path}: section {backend!r} sets {', '.join(sorted(owned))}, "
                "which ankery builds itself; remove it."
            )
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
            skip_file_prefixes=_INTERNAL_FILES,
        )


def resolve_variables(raw: dict[str, str], pack: Pack) -> dict[str, str]:
    """Resolve the operator's variables against the pack's declarations.

    Seed from each declared variable's default, overlay the operator-supplied
    `raw`, and reject any key the pack did not declare (the typo protection). Keys
    with no default and no operator value are simply absent.
    """
    unknown = set(raw) - set(pack.variables)
    if unknown:
        known = ", ".join(sorted(pack.variables)) or "(none)"
        raise ConfigError(
            f"pack {pack.code!r} does not declare variable(s): "
            f"{', '.join(sorted(unknown))}; it knows: {known}."
        )
    resolved = {
        key: spec.default
        for key, spec in pack.variables.items()
        if spec.default is not None
    }
    resolved.update(raw)
    return resolved


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _token_source(config: "Config") -> TokenSource:
    """The stored ChatGPT sign-in; ConfigError if there is none."""
    store = TokenStore(tokens_path())
    try:
        record = store.load()
    except SignInError as exc:
        raise ConfigError(str(exc)) from exc
    if not is_signed_in(record):
        raise ConfigError(
            f"the {ChatGPTTransport.name} llm backend needs a ChatGPT sign-in; "
            "run `ankery login`."
        )
    return StoredTokens(store, timeout=config.llm_timeout)


# Each builder gets the resolved model and the backend's llm_params.json section.
TransportBuilder = Callable[["Config", str, dict[str, Any]], Transport]


def _build_chat_completions(config: "Config", model: str, params: dict[str, Any]) -> Transport:
    base_url = config.llm_base_url or ChatCompletionsTransport.DEFAULT_BASE_URL
    if config.llm_api_key:
        parts = urlsplit(base_url)
        if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
            warnings.warn(
                f"sending the LLM API key over plaintext http to {parts.hostname}; "
                "the token is exposed in transit — use https for remote endpoints.",
                skip_file_prefixes=_INTERNAL_FILES,
            )
    return ChatCompletionsTransport(
        base_url=base_url,
        model=model,
        params=params,
        timeout=config.llm_timeout,
        api_key=config.llm_api_key,
    )


def _build_chatgpt(config: "Config", model: str, params: dict[str, Any]) -> Transport:
    ignored = [key for key in ("llm_base_url", "llm_api_key") if getattr(config, key)]
    if ignored:
        warnings.warn(
            f"llm_backend {ChatGPTTransport.name!r} ignores {', '.join(ignored)}: "
            "its endpoint is fixed and it uses the ChatGPT sign-in.",
            skip_file_prefixes=_INTERNAL_FILES,
        )
    return ChatGPTTransport(
        model,
        token_source=_token_source(config),
        params=params,
        timeout=config.llm_timeout,
    )


_TRANSPORT_BUILDERS: dict[type[Transport], TransportBuilder] = {
    ChatCompletionsTransport: _build_chat_completions,
    ChatGPTTransport: _build_chatgpt,
}

TRANSPORTS: dict[str, type[Transport]] = {
    transport.name: transport for transport in _TRANSPORT_BUILDERS
}


def _unknown_backend(backend: str) -> str:
    return f"unknown llm_backend {backend!r}; known: {', '.join(sorted(TRANSPORTS))}."


def _build_transport(config: "Config") -> Transport:
    backend = TRANSPORTS.get(config.llm_backend)
    if backend is None:
        raise ConfigError(_unknown_backend(config.llm_backend))
    model = config.llm_model or backend.DEFAULT_MODEL
    if model is None:
        raise ConfigError(
            f"llm_backend {backend.name!r} has no default model; set llm_model "
            "(or --llm-model) to a model slug; `ankery status` lists them."
        )
    params = config.llm_params.get(backend.name, {})
    return _TRANSPORT_BUILDERS[backend](config, model, params)


def _build_llm(config: "Config", pack: Pack) -> Provider:
    transport = _build_transport(config)
    return LLMProvider(
        transport,
        system_prompt_for=partial(
            render_system_prompt,
            pack,
            variables=config.variables,
            template=pack.system_template,
        ),
        user_prompt_for=partial(render_user_prompt, template=pack.user_template),
        pack=pack.code,
        variables=config.variables,
        category_key=pack.category_label,
    )


ProviderBuilder = Callable[["Config", Pack], Provider]

PROVIDER_REGISTRY: dict[str, ProviderBuilder] = {
    "llm": _build_llm,
}


def _load_pack(config: Config) -> Pack:
    if config.pack is None:
        raise ConfigError("no pack chosen; pass --pack <code> or set `pack` in config.toml")
    try:
        return load_pack(config.pack, config.packs_dir)
    except PackError as exc:
        raise ConfigError(str(exc)) from exc


def build_sink(config: Config) -> AnkiConnectSink:
    return AnkiConnectSink(
        base_url=config.anki_url,
        timeout=config.anki_timeout,
        sync_timeout=config.anki_sync_timeout,
        allow_duplicate=config.allow_duplicate,
    )


def sync_note_types(config: Config) -> SyncResult:
    """Sync the selected pack's own note definitions, styled with its style.css."""
    pack = _load_pack(config)
    return build_sink(config).sync_note_types(pack.notes, default_css=pack.style_css)


def sync_collection(config: Config) -> None:
    """Ask the Anki at `anki_url` to sync its collection with AnkiWeb."""
    build_sink(config).sync_collection()


def build_deck_builder(config: Config) -> DeckBuilder:
    """Resolve the pack from `pack` and wire providers, notes, sink, and builder."""
    pack = _load_pack(config)

    # Resolve variables against the pack now that it is loaded — validation needs
    # its declarations.
    config = replace(config, variables=resolve_variables(config.variables, pack))

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

    return DeckBuilder(
        providers,
        build_sink(config),
        deck=config.deck,
        note_type=config.note_type,
        style_css=pack.style_css,
        normalize=pack.normalize,
        note_definitions=notes,
        tags=list(config.tags),
        category_names=sorted(pack.categories),
    )
