
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jinja2

from ankery.models import WordInfo

FieldMap = Callable[[WordInfo], dict[str, str]]


class NoteDefinitionError(Exception):
    """Raised when a note definition can't be found or parsed."""


def _finalize(value: object) -> object:
    # Without this, present-but-None fields (part_of_speech, audio_url, …) render as "None".
    return "" if value is None else value


_env = jinja2.Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=jinja2.ChainableUndefined,  # missing feature keys render as ""
    finalize=_finalize,
    autoescape=False,
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
    model_id: int | None = None
    cards: tuple[Card, ...] = ()
    css: str = ""

    @property
    def fields(self) -> list[str]:
        return list(self.field_map)

    def applies(self, info: WordInfo) -> bool:
        if self.applies_to is None:
            return False
        return (info.part_of_speech or "").strip().lower() == self.applies_to

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
    _reject_duplicate_pos(by_path)
    return [definition for _, definition in by_path]


def _reject_duplicate_pos(by_path: list[tuple[Path, NoteDefinition]]) -> None:
    """Raise if two definitions serve the same part of speech."""
    seen: dict[str, Path] = {}
    for path, definition in by_path:
        pos = definition.applies_to
        if pos is None:
            continue
        if pos in seen:
            raise NoteDefinitionError(
                f"{seen[pos].name} and {path.name} both serve part of speech "
                f"{pos!r} in {path.parent}; a part of speech may be served by at "
                "most one note definition per directory."
            )
        seen[pos] = path


def merge_note_definitions(
    base: list[NoteDefinition], override: list[NoteDefinition]
) -> list[NoteDefinition]:
    """Layer `override` over `base` by POS: matching POS replaces in place, new POS appends."""
    override_by_pos = {
        d.applies_to: d for d in override if d.applies_to is not None
    }
    merged = [override_by_pos.get(d.applies_to, d) for d in base]
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
        model_id=raw.get("id"),
        cards=cards,
        css=raw.get("css", ""),
    )


def default_field_map(info: WordInfo) -> dict[str, str]:
    """Catch-all field map for words matching no note definition: Front=word, Back=everything."""
    return {"Front": info.word, "Back": _back(info)}


def _back(info: WordInfo) -> str:
    sections: list[str] = []
    if info.translations:
        sections.append(", ".join(info.translations))
    if info.definitions:
        sections.append("<br>".join(info.definitions))
    if info.features:
        sections.append(
            "<br>".join(f"{key}: {value}" for key, value in info.features.items())
        )
    if info.examples:
        rendered = []
        for i, ex in enumerate(info.examples):
            gloss = info.example_translations[i] if i < len(info.example_translations) else ""
            rendered.append(f"<i>{ex}</i> — {gloss}" if gloss else f"<i>{ex}</i>")
        sections.append("<br>".join(rendered))
    return "<hr>".join(sections)
