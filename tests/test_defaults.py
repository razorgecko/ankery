"""The engine-shipped defaults home: the guaranteed terminus for neutral slots.

A packaging regression that drops one of these assets must fail loudly here
rather than fall through to nothing at run time, so each slot has a completeness
check.
"""

from ankery import defaults
from ankery.models import Entry


def test_default_style_is_shipped_and_nonempty():
    css = defaults.default_style()
    assert ".card" in css


def test_default_style_path_exists():
    # The terminus must actually be on disk in the installed package.
    assert defaults.STYLE_PATH.is_file()


def test_exactly_one_catch_all_note_is_shipped():
    # The completeness guard: a packaging regression that drops or duplicates the
    # catch-all fails loudly here, not at run time.
    assert defaults.default_catch_all().name == "Ankery Basic"


def test_catch_all_is_an_owned_model_with_one_forward_card():
    # The catch-all is an ankery-provisioned model: Front/Back fields and a single
    # forward card (term -> info dump). No reverse card — reversing a structured
    # dump makes a poor flashcard.
    note = defaults.default_catch_all()
    assert note.fields == ["Front", "Back"]
    assert len(note.cards) == 1
    card = note.cards[0]
    assert "{{Front}}" in card.qfmt
    assert "{{Back}}" in card.afmt


def test_catch_all_carries_no_css_so_it_inherits_the_fallback():
    # No css of its own: the sink styles it with the pack's style.css (else the
    # engine default), the same styling bespoke cards inherit.
    assert defaults.default_catch_all().css == ""


def test_catch_all_model_name_matches_the_asset():
    assert defaults.catch_all_model_name() == defaults.default_catch_all().name


def test_prompt_templates_are_shipped():
    # The prompt terminus must exist on disk and be loadable.
    assert defaults.SYSTEM_TEMPLATE_PATH.is_file()
    assert defaults.USER_TEMPLATE_PATH.is_file()
    assert "{{ name }}" in defaults.default_system_template()
    assert "{{ term }}" in defaults.default_user_template()


# ---------------------------------------------------------------------------
# The neutral catch-all note's rendering
# ---------------------------------------------------------------------------


def test_catch_all_is_neutral():
    # Carries no domain knowledge: front is the bare term, and each non-empty
    # section (items joined by <br>, in insertion order) plus every declared
    # feature shows in the back, blocks separated by <hr>.
    entry = Entry(
        term="Buch",
        source="test",
        collections={"translations": ["book", "tome"], "definitions": ["gebundene Seiten"]},
        properties={"gender": "das", "nominative_pl": "Bücher"},
    )
    fields = defaults.default_catch_all().render(entry)

    assert fields["Front"] == "Buch"
    assert fields["Back"] == (
        "book<br>tome<hr>gebundene Seiten<hr>gender: das<br>nominative_pl: Bücher"
    )


def test_catch_all_dumps_each_section_independently_without_pairing():
    # No leading/trailing/doubled <hr>: blocks separated only between two present
    # collections. The neutral catch-all pairs no collections by index (that is a pack-
    # note convention) — examples and their glosses are separate blocks.
    entry = Entry(
        term="x", source="test",
        collections={
            "examples": ["Ein Satz.", "Noch einer."],
            "example_translations": ["A sentence."],
        },
    )
    back = defaults.default_catch_all().render(entry)["Back"]
    assert back == "Ein Satz.<br>Noch einer.<hr>A sentence."
    assert not back.startswith("<hr>") and not back.endswith("<hr>")


def test_catch_all_escapes_provider_html_but_keeps_structure():
    entry = Entry(
        term="x",
        source="test",
        collections={
            "translations": ["<script>alert(1)</script>"],
            "examples": ["a <b>bold</b> sentence"],
        },
    )
    back = defaults.default_catch_all().render(entry)["Back"]

    # Provider values are escaped...
    assert "<script>" not in back
    assert "&lt;script&gt;" in back
    assert "<b>bold</b>" not in back
    # ...but our own structural tags survive.
    assert "<hr>" in back


def test_catch_all_escapes_the_front_term():
    # The templated catch-all escapes Front like every other note — autoescape
    # applies uniformly.
    fields = defaults.default_catch_all().render(
        Entry(term="<b>x</b>", source="test")
    )
    assert fields["Front"] == "&lt;b&gt;x&lt;/b&gt;"
