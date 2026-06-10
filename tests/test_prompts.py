"""The system-prompt renderer: pack in, prompt out, no hardcoded language."""

from pathlib import Path

from ankery.pack import load_pack
from ankery.prompts import render_system_prompt, render_user_prompt

FIXTURES = Path(__file__).parent / "fixtures"


def _golden(name: str) -> str:
    # Fixtures are stored without a trailing newline-only line; read verbatim.
    return FIXTURES.joinpath(name).read_text("utf-8")


def _render_de(category_hint: str | None = None) -> str:
    # The production path renders the de pack with its own (language-specific)
    # template; the engine default is domain-neutral, so de-specific assertions
    # must go through the pack template.
    pack = load_pack("de")
    return render_system_prompt(
        pack,
        category_hint,
        variables={"target_language": "en"},
        template=pack.system_template,
    )


def test_unhinted_prompt_matches_golden_byte_for_byte():
    # Pins the de pack's template + builder output byte-for-byte; regenerated
    # deliberately whenever the pack's declarations or template change.
    assert _render_de() == _golden("system_prompt_de_unhinted.txt")


def test_hinted_prompt_matches_golden_byte_for_byte():
    assert _render_de("noun") == _golden("system_prompt_de_noun.txt")


def test_escape_hatch_is_force_appended_by_the_builder_not_the_template():
    # The crux of the anti-hallucination contract: even a template that knows
    # nothing of the escape hatch still gets it under a hint, because the builder
    # appends it after rendering. A pack/operator template therefore cannot drop it.
    bare = "Cards for {{ name }}."
    out = render_system_prompt(
        load_pack("de"), "noun", variables={"target_language": "en"}, template=bare
    )

    assert out.startswith("Cards for German.")
    assert "return an empty JSON object {} and nothing else." in out


def test_no_escape_hatch_without_a_hint():
    out = render_system_prompt(
        load_pack("de"), variables={"target_language": "en"}, template="Cards for {{ name }}."
    )
    assert "empty JSON object" not in out


def test_renders_category_vocabulary_as_the_classification_set():
    prompt = _render_de()

    # The declared categories are offered as the closed classification vocabulary.
    assert (
        "exactly one of: adjective, adverb, article, conjunction, noun, "
        "particle, phrase, preposition, pronoun, verb" in prompt
    )


def test_renders_per_category_property_keys_and_meanings():
    prompt = _render_de()

    assert "Part of speech: noun" in prompt
    assert "gender: the definite article" in prompt
    assert "present_1sg: 1st person singular present" in prompt


def test_renders_common_properties_and_guidance():
    prompt = _render_de()

    assert "Common property keys" in prompt
    assert "ipa:" in prompt
    # Per-category guidance prose from pack.toml is included.
    assert "Fill all six present-tense forms" in prompt


def test_names_the_pack_language():
    assert "German" in _render_de()


def test_category_hint_trims_the_prompt_to_the_named_category():
    prompt = _render_de(category_hint="noun")

    # Only the hinted category section survives; the classification set collapses to it.
    assert "Part of speech: noun" in prompt
    assert "Part of speech: verb" not in prompt
    assert "exactly one of" not in prompt
    # The assertion is conditional, with an empty-object escape hatch so a
    # mistaken hint misses instead of fabricating a noun reading.
    assert "the user states this term is a noun" in prompt
    assert "return an empty JSON object {}" in prompt
    # Common property keys apply to any category, so trimming must keep them.
    assert "Common property keys" in prompt


def test_unknown_category_hint_falls_back_to_full_vocabulary():
    prompt = _render_de(category_hint="bogus")

    assert (
        "exactly one of: adjective, adverb, article, conjunction, noun, "
        "particle, phrase, preposition, pronoun, verb" in prompt
    )


def test_user_prompt_carries_only_the_term():
    # The language pair lives in the system prompt; the user turn is just the term.
    prompt = render_user_prompt("Buch")

    assert "Term: Buch" in prompt
    assert "language" not in prompt.lower()


def test_user_prompt_omits_category_line():
    # A category_hint is handled entirely in the system prompt; the user turn
    # never mentions the category.
    assert "Part of speech" not in render_user_prompt("Buch")


def test_system_prompt_names_the_target_language():
    # The target language is inlined into the system prompt as a display name.
    prompt = _render_de()

    assert "`definitions`, `examples`: written in German" in prompt
    assert "the English gloss of each example" in prompt
    assert "`translations`: strings in English" in prompt


def test_omitting_the_template_renders_the_domain_neutral_default():
    # No template= argument => the builder falls back to the engine default, which
    # is domain-neutral: it names no target language and makes no language-of-output
    # claim a non-language pack could not honour. The de pack supplies only the
    # category data here (not its template) to exercise the default's chrome.
    prompt = render_system_prompt(load_pack("de"), variables={"target_language": "en"})

    # The category vocabulary still threads through (it comes from injected
    # variables, not the template's prose).
    assert "exactly one of: adjective" in prompt
    # ...but the language-specific phrasing of the de template does not.
    assert "written in German" not in prompt
    assert "English" not in prompt
