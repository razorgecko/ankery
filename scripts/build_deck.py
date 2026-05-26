"""Throwaway: import a deck so its note types (Noun, Verb) land in Anki.

The note types are now defined in ``src/ankery/notes/*.toml``. This script turns
each definition that carries a model id + card templates into a genanki model,
fills a sample note through the same ``[map]`` the runtime uses, and writes a
deck so the types land in Anki on import. Field names live in the .toml only.

    uv run python scripts/build_deck.py
"""

import genanki

from ankery.models import WordInfo
from ankery.notedef import NoteDefinition, load_note_definitions


def to_model(note_def: NoteDefinition) -> genanki.Model:
    """Build a genanki model from a note definition's fields + card templates."""
    kwargs = {"css": note_def.css} if note_def.css else {}
    return genanki.Model(
        note_def.model_id,
        note_def.name,
        fields=[{"name": name} for name in note_def.fields],
        templates=[
            {"name": c.name, "qfmt": c.qfmt, "afmt": c.afmt} for c in note_def.cards
        ],
        **kwargs,
    )


# Sample words, one per routed note type. Rendered through the note definition's
# own map, so the seeded note proves the field map and the model agree.
SAMPLES = [
    WordInfo(
        word="Haus", part_of_speech="noun", gender="das",
        translations=["house", "home"],
        inflections={"nominative_pl": "Häuser", "genitive_sg": "Hauses"},
        examples=["Das Haus ist groß."], source="sample",
    ),
    WordInfo(
        word="sehen", part_of_speech="verb", translations=["to see"],
        inflections={
            "auxiliary": "haben",
            "present_1sg": "sehe", "present_2sg": "siehst", "present_3sg": "sieht",
            "present_1pl": "sehen", "present_2pl": "seht", "present_3pl": "sehen",
            "preterite": "sah", "perfect": "hat gesehen",
        },
        examples=["Ich sehe dich."], source="sample",
    ),
]

definitions = [d for d in load_note_definitions() if d.model_id is not None]
models = {d.name: to_model(d) for d in definitions}

deck = genanki.Deck(1754859910, "German::ankery")
for info in SAMPLES:
    note_def = next(d for d in definitions if d.applies(info))
    fields = note_def.render(info)
    deck.add_note(
        genanki.Note(
            model=models[note_def.name],
            fields=[fields[name] for name in note_def.fields],
        )
    )

genanki.Package(deck).write_to_file("ankery_german.apkg")
print("wrote ankery_german.apkg")
