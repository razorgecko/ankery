
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jinja2

from ankery.models import WordInfo

FieldMap = Callable[[WordInfo], dict[str, str]]

# A note whose `applies_to` is this serves as the pack's catch-all fallback: it
# matches no specific category, but routing falls back to it (before the
# neutral catch-all) for any word no bespoke note claims.
DEFAULT_APPLIES_TO = "*"


class NoteDefinitionError(Exception):
    """Raised when a note definition can't be found or parsed."""


def _finalize(value: object) -> object:
    # Without this, present-but-None fields (category, audio_url, …) render as "None".
    return "" if value is None else value


# autoescape: field values flow from untrusted providers (LLM/scraper) into card
# HTML, so escape them by default. Card structure lives in the Anki templates, not
# here; a map that deliberately emits HTML can opt out per-value with `| safe`.
_env = jinja2.Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=jinja2.ChainableUndefined,  # missing feature keys render as ""
    finalize=_finalize,
    autoescape=True,
)


@dataclass(frozen=True)
class Card:
    """One Anki card template: a question side and an answer side."""

    name: str
    qfmt: str
    afmt: str


@dataclass(frozen=True)
class NoteDefinition:
    name: str
    field_map: dict[str, str]
    applies_to: str | None = None
    cards: tuple[Card, ...] = ()
    css: str = ""

    @property
    def fields(self) -> list[str]:
        return list(self.field_map)

    @property
    def is_default(self) -> bool:
        """Whether this note is the pack's catch-all fallback (`applies_to = "*"`)."""
        return self.applies_to == DEFAULT_APPLIES_TO

    def applies(self, info: WordInfo) -> bool:
        if self.applies_to is None or self.is_default:
            return False  # the default note is fallback-only, never a category match
        return (info.category or "").strip().lower() == self.applies_to

    def render(self, info: WordInfo) -> dict[str, str]:
        """Render each field's Jinja template against `info`."""
        data = info.model_dump()
        return {
            name: _env.from_string(template).render(**data)
            for name, template in self.field_map.items()
        }


def load_notes_from_dir(directory: Path) -> list[NoteDefinition]:
    """Load all *.toml note definitions from `directory`, sorted by stem (routing order)."""
    if not directory.is_dir():
        return []
    by_path: list[tuple[Path, NoteDefinition]] = []
    for path in sorted(directory.glob("*.toml")):
        try:
            by_path.append((path, _parse(tomllib.loads(path.read_text("utf-8")))))
        except (tomllib.TOMLDecodeError, KeyError, OSError) as exc:
            raise NoteDefinitionError(f"{path}: {exc}") from exc
    _reject_duplicate_category(by_path)
    return [definition for _, definition in by_path]


def _reject_duplicate_category(by_path: list[tuple[Path, NoteDefinition]]) -> None:
    """Raise if two definitions serve the same category."""
    seen: dict[str, Path] = {}
    for path, definition in by_path:
        category = definition.applies_to
        if category is None:
            continue
        if category in seen:
            what = (
                "the pack default note"
                if definition.is_default
                else f"category {category!r}"
            )
            raise NoteDefinitionError(
                f"{seen[category].name} and {path.name} both serve {what} in "
                f"{path.parent}; it may be served by at most one note definition "
                "per directory."
            )
        seen[category] = path


def merge_note_definitions(
    base: list[NoteDefinition], override: list[NoteDefinition]
) -> list[NoteDefinition]:
    """Layer `override` over `base` by category: matching category replaces in place, new appends."""
    override_by_category = {
        d.applies_to: d for d in override if d.applies_to is not None
    }
    merged = [override_by_category.get(d.applies_to, d) for d in base]
    placed = {d.applies_to for d in base if d.applies_to is not None}
    merged += [
        d for d in override if d.applies_to is None or d.applies_to not in placed
    ]
    return merged


def _parse(raw: dict) -> NoteDefinition:
    cards = tuple(
        Card(c["name"], c["qfmt"], c["afmt"]) for c in raw.get("cards", [])
    )
    return NoteDefinition(
        name=raw["name"],
        field_map=raw["map"],
        applies_to=raw.get("applies_to"),
        cards=cards,
        css=raw.get("css", ""),
    )
