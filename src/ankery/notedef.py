"""Note definitions loaded from a pack's notes/ directory — one file per type.

A note definition is the single source of truth for a note type: its Anki field
set (and order), how each field is filled from a `WordInfo` (Jinja templates),
the card templates, and the part of speech it serves. The runtime reads the
field map and the routing predicate; ``AnkiConnectSink.verify_note_types`` reads
the *same* definitions to create the Anki note types (via ``createModel``). So a
field name is written once — in the ``[map]`` of one file — and can no longer
drift between the model and the filler.

The files live in the active language pack (``langs/<code>/notes/``); the pack
loader calls ``load_notes_from_dir``. The maps read the feature keys the pack
declares, e.g. ``{{ features.gender }}`` / ``{{ features.genitive_sg }}``; an
undeclared/absent key renders empty via ``ChainableUndefined``.

File shape (see ``langs/de/notes/noun_de.toml``)::

    name = "Noun (DE)"        # the Anki note type name
    id = 1986815750           # genanki model id; omit for a built-in note type
    applies_to = "noun"       # part_of_speech this serves; omit for the catch-all

    [map]                     # field name -> Jinja template over one WordInfo.
    Word = "{{ word }}"       # Key order IS the Anki field order.

    [[cards]]                 # card templates, passed verbatim to Anki (NOT Jinja)
    name = "N1 Recognition"
    qfmt = "..."
    afmt = "..."

    css = "..."               # optional; omitted falls back to the pack's style.css

The map values are Jinja; the card ``qfmt``/``afmt`` are Anki's own mustache and
are never run through Jinja here.
"""

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jinja2

from ankery.models import WordInfo

# A filler: WordInfo -> flat {field name: value}. Both NoteDefinition.render
# (the file-driven, declarative path) and default_field_map (the procedural
# catch-all) satisfy it, so DeckBuilder can treat either uniformly.
FieldMap = Callable[[WordInfo], dict[str, str]]


class NoteDefinitionError(Exception):
    """Raised when a note definition can't be found or parsed."""


def _finalize(value: object) -> object:
    """Render `WordInfo`'s None-valued optionals as "" rather than "None".

    Absent feature keys are already Undefined (-> "") via ChainableUndefined;
    this catches the core fields that are present-but-None (part_of_speech,
    audio_url, ...), which Jinja would otherwise stringify as "None".
    """
    return "" if value is None else value


_env = jinja2.Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=jinja2.ChainableUndefined,  # features.missing_key -> "" not error
    finalize=_finalize,
    autoescape=False,  # field values are HTML we assemble deliberately
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
        """Field names in Anki order — the order they appear in ``[map]``."""
        return list(self.field_map)

    def applies(self, info: WordInfo) -> bool:
        """Whether this note type serves the word, by part of speech."""
        if self.applies_to is None:
            return False
        return (info.part_of_speech or "").strip().lower() == self.applies_to

    def render(self, info: WordInfo) -> dict[str, str]:
        """Fill every field by rendering its Jinja template against `info`."""
        data = info.model_dump()
        return {
            name: _env.from_string(template).render(**data)
            for name, template in self.field_map.items()
        }


def load_notes_from_dir(directory: Path) -> list[NoteDefinition]:
    """Load every ``*.toml`` note definition in `directory`, ordered by stem.

    Stem order is the routing order (first whose ``applies`` matches wins). A
    missing directory yields no definitions; the pack just routes everything to
    the catch-all. A malformed file raises ``NoteDefinitionError``.
    """
    if not directory.is_dir():
        return []
    definitions: list[NoteDefinition] = []
    for path in sorted(directory.glob("*.toml")):
        try:
            definitions.append(_parse(tomllib.loads(path.read_text("utf-8"))))
        except (tomllib.TOMLDecodeError, KeyError, OSError) as exc:
            raise NoteDefinitionError(f"{path}: {exc}") from exc
    return definitions


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
    """Fill Anki's built-in "Basic" note from any `WordInfo`: Front + Back.

    The language-neutral catch-all for words that match no note definition
    (adjectives, function words, anything a pack ships no layout for). It names
    no language: the front is the bare word, the back collapses translations,
    definitions, every declared `features` entry, and the examples. Fields are
    HTML, so lines are joined with <br>.
    """
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
