"""The system-prompt renderer: pack in, prompt out, no hardcoded language."""

from ankery.pack import load_pack
from ankery.prompts import build_user_prompt, render_system_prompt


def test_renders_pos_vocabulary_as_the_classification_set():
    prompt = render_system_prompt(load_pack("de"))

    # The declared POS are offered as the closed classification vocabulary.
    assert (
        "exactly one of: adjective, adverb, article, conjunction, noun, "
        "particle, preposition, pronoun, verb" in prompt
    )


def test_renders_per_pos_feature_keys_and_meanings():
    prompt = render_system_prompt(load_pack("de"))

    assert "Part of speech: noun" in prompt
    assert "gender: the definite article" in prompt
    assert "present_1sg: 1st person singular present" in prompt


def test_renders_common_features_and_guidance():
    prompt = render_system_prompt(load_pack("de"))

    assert "Common feature keys" in prompt
    assert "ipa:" in prompt
    # Per-POS guidance prose from lang.toml is included.
    assert "Fill all six present-tense forms" in prompt


def test_names_the_pack_language():
    assert "German" in render_system_prompt(load_pack("de"))


def test_pos_hint_trims_the_prompt_to_the_named_pos():
    prompt = render_system_prompt(load_pack("de"), pos_hint="noun")

    # Only the hinted POS section survives; the classification set collapses to it.
    assert "Part of speech: noun" in prompt
    assert "Part of speech: verb" not in prompt
    assert "exactly one of" not in prompt
    # The assertion is conditional, with an empty-object escape hatch so a
    # mistaken hint misses instead of fabricating a noun reading.
    assert "the user states this word is a noun" in prompt
    assert "return an empty JSON object {}" in prompt
    # Common feature keys apply to any POS, so trimming must keep them.
    assert "Common feature keys" in prompt


def test_unknown_pos_hint_falls_back_to_full_vocabulary():
    prompt = render_system_prompt(load_pack("de"), pos_hint="bogus")

    assert (
        "exactly one of: adjective, adverb, article, conjunction, noun, "
        "particle, preposition, pronoun, verb" in prompt
    )


def test_user_prompt_carries_word_and_language_pair():
    prompt = build_user_prompt("Buch", "de", "en")

    assert "Word: Buch" in prompt
    assert "Source language: de" in prompt
    assert "Target language: en" in prompt


def test_user_prompt_omits_pos_line():
    # A pos_hint is handled entirely in the system prompt; the user turn never
    # mentions part of speech.
    assert "Part of speech" not in build_user_prompt("Buch", "de", "en")
