"""Note definitions loaded from TOML — one file per note type.

A note definition is the single source of truth for a note type: its Anki field
set (and order), how each field is filled from a `WordInfo` (Jinja templates),
the card templates, and the part of speech it serves. The runtime reads the
field map and the routing predicate; ``AnkiConnectSink.verify_note_types`` reads
the *same* files to create the Anki note types (via ``createModel``). So a field
name is written once — in the ``[map]`` of one file — and can no longer drift
between the model and the filler.

File shape (see ``notes/noun_de.toml``)::

    name = "Noun (DE)"        # the Anki note type name
    id = 1986815750           # genanki model id; omit for a built-in note type
    applies_to = "noun"       # part_of_speech this serves; omit for the catch-all

    [map]                     # field name -> Jinja template over one WordInfo.
    Word = "{{ word }}"       # Key order IS the Anki field order.

    [[cards]]                 # card templates, passed verbatim to Anki (NOT Jinja)
    name = "N1 Recognition"
    qfmt = "..."
    afmt = "..."

    css = "..."               # optional; omitted creates the note type unstyled

The map values are Jinja; the card ``qfmt``/``afmt`` are Anki's own mustache and
are never run through Jinja here. Both happen to use ``{{ }}`` — only the map is
rendered.
"""

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jinja2

from ankery.models import WordInfo

_NOTES_DIR = Path(__file__).parent / "notes"

# A filler: WordInfo -> flat {field name: value}. Both NoteDefinition.render
# (the file-driven, declarative path) and default_field_map (the procedural
# catch-all) satisfy it, so DeckBuilder can treat either uniformly.
FieldMap = Callable[[WordInfo], dict[str, str]]


def _finalize(value: object) -> object:
    """Render `WordInfo`'s None-valued optionals as "" rather than "None".

    Absent inflection keys are already Undefined (-> "") via ChainableUndefined;
    this only has to catch the fields that are present-but-None (gender,
    pronunciation, ...), which Jinja would otherwise stringify as "None".
    """
    return "" if value is None else value


_env = jinja2.Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=jinja2.ChainableUndefined,  # inflections.missing_key -> "" not error
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


def load_note_definitions(directory: Path | None = None) -> list[NoteDefinition]:
    """Load every ``*.toml`` note definition from `directory` (default: bundled)."""
    directory = directory or _NOTES_DIR
    return [
        _parse(tomllib.loads(path.read_text("utf-8")))
        for path in sorted(directory.glob("*.toml"))
    ]


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

    The catch-all for words that match no note definition (adjectives, adverbs,
    function words, non-German). Unlike the per-POS note types, this collapses an
    arbitrary `WordInfo` into two slots, so it is procedural rather than a
    field-per-datum template — the one filler kept in Python. Fields are HTML, so
    lines are joined with <br>.
    """
    return {"Front": _front(info), "Back": _back(info)}


def _front(info: WordInfo) -> str:
    # Show nouns with their article so the gender is learned with the word.
    if info.gender:
        return f"{info.gender} {info.word}"
    return info.word


def _back(info: WordInfo) -> str:
    sections: list[str] = []
    if info.translations:
        sections.append(", ".join(info.translations))
    if info.definitions:
        sections.append("<br>".join(info.definitions))
    if info.inflections:
        sections.append(
            "<br>".join(f"{key}: {value}" for key, value in info.inflections.items())
        )
    if info.examples:
        rendered = []
        for i, ex in enumerate(info.examples):
            gloss = info.example_translations[i] if i < len(info.example_translations) else ""
            rendered.append(f"<i>{ex}</i> — {gloss}" if gloss else f"<i>{ex}</i>")
        sections.append("<br>".join(rendered))
    if info.pronunciation:
        sections.append(f"[{info.pronunciation}]")
    return "<hr>".join(sections)
