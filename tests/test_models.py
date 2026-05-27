import pytest
from pydantic import ValidationError

from ankery.models import WordInfo


def test_minimal_valid():
    info = WordInfo(word="ephemeral", source="local_llm")
    assert info.word == "ephemeral"
    assert info.source == "local_llm"


def test_list_fields_default_empty():
    info = WordInfo(word="ephemeral", source="local_llm")
    assert info.definitions == []
    assert info.examples == []
    assert info.translations == []
    assert info.part_of_speech is None


def test_optional_fields_default():
    info = WordInfo(word="ephemeral", source="local_llm")
    assert info.source_language is None
    assert info.target_language is None
    assert info.features == {}
    assert info.audio_url is None


def test_features_carry_arbitrary_language_keys():
    # The contract names no language: gender, case forms, verb class, kana
    # reading, IPA all live in the open features dict under pack-declared keys.
    info = WordInfo(
        word="Buch",
        source_language="de",
        target_language="en",
        part_of_speech="noun",
        features={"gender": "das", "genitive_sg": "Buches", "nominative_pl": "Bücher"},
        source="local_llm",
    )
    assert info.features["gender"] == "das"
    assert info.features["nominative_pl"] == "Bücher"


def test_features_round_trip():
    info = WordInfo(
        word="mögen",
        features={"present_1sg": "mag", "present_3sg": "mag", "present_2sg": "magst"},
        source="local_llm",
    )
    restored = WordInfo.model_validate_json(info.model_dump_json())
    assert restored.features == info.features


def test_defaults_are_independent_instances():
    a = WordInfo(word="a", source="s")
    b = WordInfo(word="b", source="s")
    a.definitions.append("x")
    a.features["k"] = "v"
    assert b.definitions == []
    assert b.features == {}


def test_full_payload():
    info = WordInfo(
        word="run",
        definitions=["to move fast", "to operate"],
        examples=["I run daily."],
        translations=["correr"],
        part_of_speech="verb",
        features={"ipa": "/rʌn/"},
        source="local_llm",
    )
    assert info.definitions == ["to move fast", "to operate"]
    assert info.part_of_speech == "verb"
    assert info.features["ipa"] == "/rʌn/"


def test_whitespace_is_stripped():
    info = WordInfo(word="  run  ", source="  local_llm  ")
    assert info.word == "run"
    assert info.source == "local_llm"


def test_json_round_trip():
    info = WordInfo(word="run", definitions=["to move"], source="local_llm")
    restored = WordInfo.model_validate_json(info.model_dump_json())
    assert restored == info


def test_validate_from_llm_json_ignores_extra_keys():
    payload = '{"word": "run", "source": "local_llm", "confidence": 0.9}'
    info = WordInfo.model_validate_json(payload)
    assert info.word == "run"
    assert not hasattr(info, "confidence")


def test_translations_dict_coerced_to_list():
    # Some models key translations by language code instead of returning a
    # flat list; the boundary flattens the dict to its values.
    info = WordInfo.model_validate(
        {"word": "Haus", "source": "llm", "translations": {"en": "house"}}
    )
    assert info.translations == ["house"]


def test_translations_dict_with_list_values_flattened():
    info = WordInfo.model_validate(
        {"word": "run", "source": "llm", "translations": {"es": ["correr", "huir"]}}
    )
    assert info.translations == ["correr", "huir"]


def test_translations_list_passes_through():
    info = WordInfo.model_validate(
        {"word": "run", "source": "llm", "translations": ["correr"]}
    )
    assert info.translations == ["correr"]


@pytest.mark.parametrize("bad_word", ["", "   "])
def test_empty_word_rejected(bad_word):
    with pytest.raises(ValidationError):
        WordInfo(word=bad_word, source="local_llm")


def test_missing_source_rejected():
    with pytest.raises(ValidationError):
        WordInfo(word="run")
