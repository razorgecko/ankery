"""The engine-shipped defaults home: the guaranteed terminus for neutral slots.

A packaging regression that drops one of these assets must fail loudly here
rather than fall through to nothing at run time, so each slot has a completeness
check.
"""

from ankery import defaults
from ankery.models import WordInfo


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
    # forward card (word -> info dump). No reverse card — reversing a structured
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
    assert "{{ source_language }}" in defaults.default_system_template()
    assert "{{ word }}" in defaults.default_user_template()


# ---------------------------------------------------------------------------
# The neutral catch-all note's rendering
# ---------------------------------------------------------------------------


def test_catch_all_is_neutral():
    # Carries no domain knowledge: front is the bare word (no article), and every
    # declared feature shows in the back as key: value.
    info = WordInfo(
        word="Buch",
        source="test",
        translations=["book", "tome"],
        definitions=["gebundene Seiten"],
        features={"gender": "das", "nominative_pl": "Bücher"},
    )
    fields = defaults.default_catch_all().render(info)

    assert fields["Front"] == "Buch"
    assert fields["Back"] == (
        "book, tome<hr>gebundene Seiten<hr>gender: das<br>nominative_pl: Bücher"
    )


def test_catch_all_sections_are_separated_only_when_present():
    # No leading/trailing/doubled <hr>: a single present section yields no rule.
    only_examples = WordInfo(
        word="x", source="test",
        examples=["Ein Satz.", "Noch einer."],
        example_translations=["A sentence."],
    )
    back = defaults.default_catch_all().render(only_examples)["Back"]
    # Index-aligned glosses; a missing gloss (shorter list) leaves the example bare.
    assert back == "<i>Ein Satz.</i> — A sentence.<br><i>Noch einer.</i>"
    assert not back.startswith("<hr>") and not back.endswith("<hr>")


def test_catch_all_escapes_provider_html_but_keeps_structure():
    info = WordInfo(
        word="x",
        source="test",
        translations=["<script>alert(1)</script>"],
        examples=["a <b>bold</b> sentence"],
        example_translations=["a gloss"],
    )
    back = defaults.default_catch_all().render(info)["Back"]

    # Provider values are escaped...
    assert "<script>" not in back
    assert "&lt;script&gt;" in back
    assert "<b>bold</b>" not in back
    # ...but our own structural tags survive.
    assert "<i>" in back and "<hr>" in back


def test_catch_all_escapes_the_front_word():
    # Unlike the old imperative map (which emitted the bare word), the templated
    # catch-all escapes Front like every other note — autoescape applies uniformly.
    fields = defaults.default_catch_all().render(
        WordInfo(word="<b>x</b>", source="test")
    )
    assert fields["Front"] == "&lt;b&gt;x&lt;/b&gt;"
