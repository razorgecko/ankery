"""Language packs — the one boundary that holds all language knowledge.

A pack is a directory keyed by language code. Bundled packs live under
``src/ankery/langs/<code>/``; a user pack at ``<langs_dir>/<code>/`` overrides
the bundled one of the same code (whole-directory override). The engine resolves
a pack from ``source_language`` at wiring time and builds everything from it —
so a new language is a directory you author and drop in, with no engine change.

A pack directory contains:

  lang.toml      grammar vocabulary (per-POS feature keys + meanings), LLM
                 guidance, the preferred provider chain, and provider options.
  notes/         card layouts (one *.toml per note type) + style.css fallback.
  filter.py      OPTIONAL. Exposes ``normalize(WordInfo) -> WordInfo``, applied
                 to every provider's output before routing. Absent => identity.
  providers/     OPTIONAL. Each ``*.py`` exposes ``PROVIDERS: dict[name,
                 (config, pack) -> WordProvider]`` — language-specific providers
                 (e.g. scrapers). All files' dicts are merged and the result is
                 layered over the engine's cross-language registry; a name
                 registered by two files is an error.

``filter.py`` and the ``providers/`` modules are loaded by file path, so their
imports must be absolute. They are pack-author code: a pack you drop in runs in
ankery's interpreter, with ankery's dependencies (httpx, bs4) available.
"""

import importlib.util
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from ankery.models import WordInfo
from ankery.notedef import NoteDefinition, load_notes_from_dir

_BUNDLED_LANGS = Path(__file__).parent / "langs"

# A pack's normalize hook and provider builders. ProviderBuilder is typed
# loosely (config, pack) -> provider to avoid an import cycle with config.py.
Normalize = Callable[[WordInfo], WordInfo]
ProviderBuilder = Callable[..., object]


class PackError(Exception):
    """Raised when a pack cannot be found or is malformed."""


@dataclass(frozen=True)
class POSGrammar:
    """The grammar a pack declares for one part of speech."""

    pos: str
    citation: str | None
    guidance: tuple[str, ...]
    # feature key -> meaning handed to the LLM and read back by the notes.
    features: dict[str, str]


@dataclass(frozen=True)
class LanguagePack:
    code: str
    name: str
    # Feature keys common to every POS (e.g. ipa/reading): key -> meaning.
    common_features: dict[str, str]
    grammar: dict[str, POSGrammar]  # POS name -> its grammar
    providers: tuple[str, ...]  # preferred chain, fallback order
    provider_options: dict[str, dict]  # provider name -> its options table
    provider_builders: dict[str, ProviderBuilder]  # pack-local providers
    notes: list[NoteDefinition]  # card layouts, in routing order
    style_css: str  # fallback card stylesheet
    normalize: Normalize  # post-fetch output filter (identity if no filter.py)


def _identity(info: WordInfo) -> WordInfo:
    return info


def load_pack(code: str, langs_dir: Path | None = None) -> LanguagePack:
    """Resolve and load the pack for `code`.

    A user pack at ``<langs_dir>/<code>/`` wins over the bundled one; otherwise
    the bundled ``langs/<code>/`` is used. Raises ``PackError`` if neither
    exists or the pack is malformed.
    """
    directory = _resolve_dir(code, langs_dir)
    raw = _read_lang_toml(directory / "lang.toml")

    grammar = _parse_grammar(raw, directory)
    notes_dir = directory / "notes"
    try:
        notes = load_notes_from_dir(notes_dir)
    except Exception as exc:  # malformed note TOML
        raise PackError(f"pack {code!r}: could not load notes: {exc}") from exc

    style_path = notes_dir / "style.css"
    style_css = style_path.read_text("utf-8") if style_path.exists() else ""

    normalize = _load_normalize(directory / "filter.py", code)
    provider_builders = _load_providers(directory / "providers", code)

    return LanguagePack(
        code=code,
        name=raw.get("name", code),
        common_features=dict(raw.get("features", {})),
        grammar=grammar,
        providers=tuple(raw.get("providers", ("llm",))),
        provider_options={k: dict(v) for k, v in raw.get("provider_options", {}).items()},
        provider_builders=provider_builders,
        notes=notes,
        style_css=style_css,
        normalize=normalize,
    )


def _resolve_dir(code: str, langs_dir: Path | None) -> Path:
    if langs_dir is not None:
        candidate = langs_dir / code
        if (candidate / "lang.toml").exists():
            return candidate
    bundled = _BUNDLED_LANGS / code
    if (bundled / "lang.toml").exists():
        return bundled
    searched = [str(_BUNDLED_LANGS / code)]
    if langs_dir is not None:
        searched.insert(0, str(langs_dir / code))
    raise PackError(
        f"no language pack for {code!r}; looked in: {', '.join(searched)}."
    )


def _read_lang_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PackError(f"could not read {path}: {exc}") from exc


def _parse_grammar(raw: dict, directory: Path) -> dict[str, POSGrammar]:
    pos_table = raw.get("pos", {})
    if not pos_table:
        raise PackError(f"pack at {directory}: lang.toml declares no [pos.*] sections.")
    grammar: dict[str, POSGrammar] = {}
    for pos, spec in pos_table.items():
        grammar[pos] = POSGrammar(
            pos=pos,
            citation=spec.get("citation"),
            guidance=tuple(spec.get("guidance", ())),
            features=dict(spec.get("features", {})),
        )
    return grammar


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PackError(f"could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_normalize(path: Path, code: str) -> Normalize:
    if not path.exists():
        return _identity
    try:
        module = _load_module(path, f"ankery_pack_{code}_filter")
    except Exception as exc:
        raise PackError(f"pack {code!r}: filter.py failed to import: {exc}") from exc
    normalize = getattr(module, "normalize", None)
    if not callable(normalize):
        raise PackError(f"pack {code!r}: filter.py must define normalize(info).")
    return normalize


def _load_providers(directory: Path, code: str) -> dict[str, ProviderBuilder]:
    """Merge the PROVIDERS dicts of every module in the pack's providers/ dir.

    Each providers/*.py is imported by path and must define ``PROVIDERS``, a
    ``{name: (config, pack) -> WordProvider}`` dict. The dicts are unioned so a
    pack can split its language-specific providers across files; a name
    registered by two files is a ``PackError``. A missing directory yields no
    pack-local providers.
    """
    if not directory.is_dir():
        return {}
    builders: dict[str, ProviderBuilder] = {}
    for path in sorted(directory.glob("*.py")):
        try:
            module = _load_module(path, f"ankery_pack_{code}_provider_{path.stem}")
        except Exception as exc:
            raise PackError(
                f"pack {code!r}: providers/{path.name} failed to import: {exc}"
            ) from exc
        providers = getattr(module, "PROVIDERS", None)
        if not isinstance(providers, dict):
            raise PackError(
                f"pack {code!r}: providers/{path.name} must define PROVIDERS "
                "(a name -> builder dict)."
            )
        for name, builder in providers.items():
            if name in builders:
                raise PackError(
                    f"pack {code!r}: provider {name!r} is registered by more than "
                    "one module in providers/."
                )
            builders[name] = builder
    return builders
