
import importlib.util
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from ankery.defaults import default_style
from ankery.models import WordInfo
from ankery.notedef import NoteDefinition, load_notes_from_dir

_BUNDLED_PACKS = Path(__file__).parent / "packs"

# Typed loosely to avoid an import cycle with config.py.
Normalize = Callable[[WordInfo], WordInfo]
ProviderBuilder = Callable[..., object]


class PackError(Exception):
    """Raised when a pack cannot be found or is malformed."""


@dataclass(frozen=True)
class CategorySpec:
    value: str  # a routing category value, e.g. "noun"
    citation: str | None
    guidance: tuple[str, ...]
    features: dict[str, str]  # key -> meaning, handed to the LLM and read by notes


@dataclass(frozen=True)
class Pack:
    code: str
    name: str
    common_features: dict[str, str]
    # The human label for the routing dimension (e.g. "part of speech"), used in
    # the LLM prompt; the per-value vocabulary lives in `categories`.
    category_label: str
    categories: dict[str, CategorySpec]
    providers: tuple[str, ...]
    provider_options: dict[str, dict]
    provider_builders: dict[str, ProviderBuilder]
    notes: list[NoteDefinition]
    style_css: str
    normalize: Normalize


def _identity(info: WordInfo) -> WordInfo:
    return info


def load_pack(code: str, packs_dir: Path | None = None) -> Pack:
    """Load the pack for `code`; user pack at `packs_dir/<code>/` overrides the bundled one."""
    directory = _resolve_dir(code, packs_dir)
    raw = _read_pack_toml(directory / "pack.toml")

    category_label, categories = _parse_categories(raw, directory)
    notes_dir = directory / "notes"
    try:
        notes = load_notes_from_dir(notes_dir)
    except Exception as exc:  # malformed note TOML
        raise PackError(f"pack {code!r}: could not load notes: {exc}") from exc

    style_path = notes_dir / "style.css"
    style_css = (
        style_path.read_text("utf-8") if style_path.exists() else default_style()
    )

    normalize = _load_normalize(directory / "filter.py", code)
    provider_builders = _load_providers(directory / "providers", code)

    return Pack(
        code=code,
        name=raw.get("name", code),
        common_features=dict(raw.get("features", {})),
        category_label=category_label,
        categories=categories,
        providers=tuple(raw.get("providers", ("llm",))),
        provider_options={k: dict(v) for k, v in raw.get("provider_options", {}).items()},
        provider_builders=provider_builders,
        notes=notes,
        style_css=style_css,
        normalize=normalize,
    )


def _resolve_dir(code: str, packs_dir: Path | None) -> Path:
    if packs_dir is not None:
        candidate = packs_dir / code
        if (candidate / "pack.toml").exists():
            return candidate
    bundled = _BUNDLED_PACKS / code
    if (bundled / "pack.toml").exists():
        return bundled
    searched = [str(_BUNDLED_PACKS / code)]
    if packs_dir is not None:
        searched.insert(0, str(packs_dir / code))
    raise PackError(
        f"no pack for {code!r}; looked in: {', '.join(searched)}."
    )


def _read_pack_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PackError(f"could not read {path}: {exc}") from exc


def _parse_categories(raw: dict, directory: Path) -> tuple[str, dict[str, CategorySpec]]:
    """Resolve the pack's routing dimension: read [category] for the name of the
    table that enumerates the category values, then parse that table."""
    declaration = raw.get("category")
    if not declaration or "name" not in declaration:
        raise PackError(
            f"pack at {directory}: pack.toml declares no [category] name."
        )
    name = declaration["name"]
    label = declaration.get("label", name)
    table = raw.get(name, {})
    if not table:
        raise PackError(
            f"pack at {directory}: pack.toml has no [{name}.*] category sections."
        )
    categories: dict[str, CategorySpec] = {}
    for value, spec in table.items():
        categories[value] = CategorySpec(
            value=value,
            citation=spec.get("citation"),
            guidance=tuple(spec.get("guidance", ())),
            features=dict(spec.get("features", {})),
        )
    return label, categories


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PackError(f"could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so pack code that imports itself, pickles, or inspects
    # its own module resolves correctly instead of building a second copy.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
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
    """Merge PROVIDERS dicts from every *.py in the pack's providers/ directory."""
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
