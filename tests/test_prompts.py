"""The system-prompt renderer: pack in, prompt out, no hardcoded language."""

from ankery.pack import load_pack
from ankery.prompts import build_user_prompt, render_system_prompt


def test_renders_pos_vocabulary_as_the_classification_set():
    prompt = render_system_prompt(load_pack("de"))

    # The declared POS are offered as the closed classification vocabulary.
    assert "exactly one of: adjective, noun, verb" in prompt


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


def test_user_prompt_carries_word_and_language_pair():
    prompt = build_user_prompt("Buch", "de", "en")

    assert "Word: Buch" in prompt
    assert "Source language: de" in prompt
    assert "Target language: en" in prompt
