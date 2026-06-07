"""The system-prompt renderer: pack in, prompt out, no hardcoded language."""

from pathlib import Path

from ankery.pack import load_pack
from ankery.prompts import build_user_prompt, render_system_prompt

FIXTURES = Path(__file__).parent / "fixtures"


def _golden(name: str) -> str:
    # Fixtures are stored without a trailing newline-only line; read verbatim.
    return FIXTURES.joinpath(name).read_text("utf-8")


def test_unhinted_prompt_matches_golden_byte_for_byte():
    # Pins the extracted template + builder to the exact prompt the imperative
    # renderer produced before extraction, so the refactor changed nothing.
    assert render_system_prompt(load_pack("de"), variables={"target_language": "en"}) == _golden("system_prompt_de_unhinted.txt")


def test_hinted_prompt_matches_golden_byte_for_byte():
    assert (
        render_system_prompt(load_pack("de"), "noun", variables={"target_language": "en"})
        == _golden("system_prompt_de_noun.txt")
    )


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
    prompt = render_system_prompt(load_pack("de"), variables={"target_language": "en"})

    # The declared categories are offered as the closed classification vocabulary.
    assert (
        "exactly one of: adjective, adverb, article, conjunction, noun, "
        "particle, preposition, pronoun, verb" in prompt
    )


def test_renders_per_category_feature_keys_and_meanings():
    prompt = render_system_prompt(load_pack("de"), variables={"target_language": "en"})

    assert "Part of speech: noun" in prompt
    assert "gender: the definite article" in prompt
    assert "present_1sg: 1st person singular present" in prompt


def test_renders_common_features_and_guidance():
    prompt = render_system_prompt(load_pack("de"), variables={"target_language": "en"})

    assert "Common feature keys" in prompt
    assert "ipa:" in prompt
    # Per-category guidance prose from pack.toml is included.
    assert "Fill all six present-tense forms" in prompt


def test_names_the_pack_language():
    assert "German" in render_system_prompt(load_pack("de"), variables={"target_language": "en"})


def test_category_hint_trims_the_prompt_to_the_named_category():
    prompt = render_system_prompt(
        load_pack("de"), category_hint="noun", variables={"target_language": "en"}
    )

    # Only the hinted category section survives; the classification set collapses to it.
    assert "Part of speech: noun" in prompt
    assert "Part of speech: verb" not in prompt
    assert "exactly one of" not in prompt
    # The assertion is conditional, with an empty-object escape hatch so a
    # mistaken hint misses instead of fabricating a noun reading.
    assert "the user states this word is a noun" in prompt
    assert "return an empty JSON object {}" in prompt
    # Common feature keys apply to any category, so trimming must keep them.
    assert "Common feature keys" in prompt


def test_unknown_category_hint_falls_back_to_full_vocabulary():
    prompt = render_system_prompt(
        load_pack("de"), category_hint="bogus", variables={"target_language": "en"}
    )

    assert (
        "exactly one of: adjective, adverb, article, conjunction, noun, "
        "particle, preposition, pronoun, verb" in prompt
    )


def test_user_prompt_carries_only_the_word():
    # The language pair lives in the system prompt; the user turn is just the word.
    prompt = build_user_prompt("Buch")

    assert "Word: Buch" in prompt
    assert "language" not in prompt.lower()


def test_user_prompt_omits_category_line():
    # A category_hint is handled entirely in the system prompt; the user turn
    # never mentions the category.
    assert "Part of speech" not in build_user_prompt("Buch")


def test_system_prompt_names_the_target_language():
    # The target language is inlined into the system prompt as a display name.
    prompt = render_system_prompt(load_pack("de"), variables={"target_language": "en"})

    assert "written in German" in prompt
    assert "the English gloss" in prompt
    assert "strings in English" in prompt
