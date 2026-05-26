"""Throwaway: import a deck so its note types (Noun, Verb) land in Anki.

Run once, import the .apkg, delete the sample cards if you like -- the note
types stay. Templates follow tmp_card_examples.txt.

    uv run python scripts/build_deck.py
"""

import genanki

# Stable random IDs so re-imports update instead of duplicating.
noun_model = genanki.Model(
    1986815750,
    "Noun (DE)",
    # Word first: Anki keys duplicate detection on the first field, so leading
    # with Article ("der"/"die"/"das") would flag every same-gender noun as a dup.
    fields=[{"name": n} for n in ("Word", "Article", "Plural", "GenitiveSg",
                                  "Translation", "Example")],
    templates=[
        {  # N1 Recognition (DE->EN)
            "name": "N1 Recognition",
            "qfmt": "{{Article}} {{Word}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Translation}}<br><br>"
                    "Pl. {{Plural}} &nbsp; Gen. {{GenitiveSg}}<br><br>{{Example}}",
        },
        {  # N2 Production (EN->DE): grades gender + plural only
            "name": "N2 Production",
            "qfmt": "{{Translation}} (noun)",
            "afmt": "{{FrontSide}}<hr id=answer>"
                    "{{Article}} {{Word}}, Pl. {{Plural}}, Gen. {{GenitiveSg}}",
        },
    ],
)

verb_model = genanki.Model(
    1890202981,
    "Verb (DE)",
    fields=[{"name": n} for n in ("Infinitive", "Translation", "Aux",
                                  "Present", "Preterite", "Perfect", "Example")],
    templates=[
        {  # V1 Recognition (DE->EN)
            "name": "V1 Recognition",
            "qfmt": "{{Infinitive}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Translation}}<br><br>"
                    "Präsens<br>{{Present}}<br>"
                    "Prät. {{Preterite}} &nbsp; Perf. {{Perfect}}<br><br>{{Example}}",
        },
        {  # V2 Production (EN->DE)
            "name": "V2 Production",
            "qfmt": "{{Translation}} (verb)",
            "afmt": "{{FrontSide}}<hr id=answer>{{Infinitive}} ({{Aux}})",
        },
        {  # V3 Forms recall: the one active recall
            "name": "V3 Forms recall",
            "qfmt": "{{Infinitive}} – {{Translation}}<br>conjugate: present (all six)",
            "afmt": "{{FrontSide}}<hr id=answer>{{Present}}<hr>"
                    "not graded: Prät. {{Preterite}} / Perf. {{Perfect}} ({{Aux}})",
        },
    ],
)

deck = genanki.Deck(1754859910, "German::ankery")

deck.add_note(genanki.Note(model=noun_model, fields=[
    "Haus", "das", "die Häuser", "des Hauses", "house, home", "Das Haus ist groß."]))

deck.add_note(genanki.Note(model=verb_model, fields=[
    "sehen", "to see", "haben",
    "ich sehe / du siehst / er sieht / wir sehen / ihr seht / sie sehen",
    "sah", "hat gesehen", "Ich sehe dich."]))

genanki.Package(deck).write_to_file("ankery_german.apkg")
print("wrote ankery_german.apkg")
