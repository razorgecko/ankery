"""Field maps and per-part-of-speech note routing.

A note type and the function that fills its fields are one decision, not two:
the "Noun (DE)" model has fields a noun filler knows how to fill, the
"Verb (DE)" model has a different field set needing a different filler. A
`NoteRecipe` bundles the two (plus the predicate that says which words it is
for) so they cannot drift apart.

The maps target the exact field names of the models built in
``scripts/build_deck.py``. Those names are typed once, here, in the only place
that can decide which piece of `WordInfo` belongs in which field — the maps do
the rendering Anki cannot (prefixing the article, joining the six present
forms into one line, comma-joining translations).
"""

from collections.abc import Callable
from dataclasses import dataclass

from anki_deckbuilder.models import WordInfo

FieldMap = Callable[[WordInfo], dict[str, str]]


@dataclass(frozen=True)
class NoteRecipe:
    """A note type paired with its field filler and a word selector.

    `applies_to` is checked against a `WordInfo` (it inspects `part_of_speech`);
    the first recipe whose predicate is true wins, and that recipe both names
    the note type and supplies the map that fills it.
    """

    note_type: str
    map_fields: FieldMap
    applies_to: Callable[[WordInfo], bool]


# Pronouns paired with the canonical present-tense inflection keys (prompts.py),
# in citation order, so the six forms render as one line:
# "ich sehe / du siehst / er sieht / wir sehen / ihr seht / sie sehen".
_PRESENT_FORMS = (
    ("ich", "present_1sg"),
    ("du", "present_2sg"),
    ("er", "present_3sg"),
    ("wir", "present_1pl"),
    ("ihr", "present_2pl"),
    ("sie", "present_3pl"),
)


def map_noun_fields(info: WordInfo) -> dict[str, str]:
    """Fill the "Noun (DE)" model: Word, Article, Plural, GenitiveSg, ..."""
    return {
        "Word": info.word,
        "Article": info.gender or "",
        "Plural": info.inflections.get("plural", ""),
        "GenitiveSg": info.inflections.get("genitive_sg", ""),
        "Translation": ", ".join(info.translations),
        "Example": _first_example(info),
    }


def map_verb_fields(info: WordInfo) -> dict[str, str]:
    """Fill the "Verb (DE)" model: Infinitive, Aux, Present, Preterite, ..."""
    return {
        "Infinitive": info.word,
        "Translation": ", ".join(info.translations),
        "Aux": info.inflections.get("auxiliary", ""),
        "Present": _present_paradigm(info),
        "Preterite": info.inflections.get("preterite", ""),
        "Perfect": info.inflections.get("perfect", ""),
        "Example": _first_example(info),
    }


def default_field_map(info: WordInfo) -> dict[str, str]:
    """Render a `WordInfo` into Front/Back fields for a Basic-style note.

    The catch-all for words that route to no specific model (adjectives,
    adverbs, function words). Fields are HTML, so lines are joined with <br>.
    """
    return {"Front": _front(info), "Back": _back(info)}


def build_recipes(
    *,
    noun_note_type: str,
    verb_note_type: str,
) -> list[NoteRecipe]:
    """Build the POS-routing chain from the configured model names.

    An empty name disables routing for that part of speech (the word then falls
    through to the DeckBuilder's catch-all note type). Order does not matter
    here since noun and verb predicates are mutually exclusive.
    """
    recipes: list[NoteRecipe] = []
    if noun_note_type:
        recipes.append(NoteRecipe(noun_note_type, map_noun_fields, _pos_is("noun")))
    if verb_note_type:
        recipes.append(NoteRecipe(verb_note_type, map_verb_fields, _pos_is("verb")))
    return recipes


def _pos_is(pos: str) -> Callable[[WordInfo], bool]:
    return lambda info: (info.part_of_speech or "").strip().lower() == pos


def _present_paradigm(info: WordInfo) -> str:
    parts = [
        f"{pronoun} {info.inflections[key]}"
        for pronoun, key in _PRESENT_FORMS
        if info.inflections.get(key)
    ]
    return " / ".join(parts)


def _first_example(info: WordInfo) -> str:
    if not info.examples:
        return ""
    german = info.examples[0]
    gloss = info.example_translations[0] if info.example_translations else ""
    return f"{german} — {gloss}" if gloss else german


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
