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
    assert info.pronunciation is None
    assert info.part_of_speech is None


def test_new_optional_fields_default():
    info = WordInfo(word="ephemeral", source="local_llm")
    assert info.source_language is None
    assert info.target_language is None
    assert info.gender is None
    assert info.separable is None
    assert info.inflections == {}
    assert info.audio_url is None


def test_german_noun_inflections():
    info = WordInfo(
        word="Buch",
        source_language="de",
        target_language="en",
        part_of_speech="noun",
        gender="das",
        inflections={"genitive_sg": "Buches", "nominative_pl": "Bücher"},
        source="local_llm",
    )
    assert info.gender == "das"
    assert info.inflections["nominative_pl"] == "Bücher"


def test_german_verb_present_paradigm():
    info = WordInfo(
        word="sehen",
        part_of_speech="verb",
        separable=False,
        inflections={
            "present_1sg": "sehe",
            "present_2sg": "siehst",
            "present_3sg": "sieht",
            "present_1pl": "sehen",
            "present_2pl": "seht",
            "present_3pl": "sehen",
            "preterite": "sah",
            "perfect": "hat gesehen",
            "auxiliary": "haben",
        },
        source="local_llm",
    )
    assert info.inflections["present_2sg"] == "siehst"
    assert info.separable is False


def test_separable_verb_keeps_clean_lemma():
    info = WordInfo(
        word="einkaufen",
        part_of_speech="verb",
        separable=True,
        inflections={"present_3sg": "kauft ein", "perfect": "hat eingekauft"},
        source="local_llm",
    )
    assert info.word == "einkaufen"
    assert info.separable is True


def test_inflections_round_trip():
    info = WordInfo(
        word="mögen",
        inflections={"present_1sg": "mag", "present_3sg": "mag", "present_2sg": "magst"},
        source="local_llm",
    )
    restored = WordInfo.model_validate_json(info.model_dump_json())
    assert restored.inflections == info.inflections


def test_defaults_are_independent_instances():
    a = WordInfo(word="a", source="s")
    b = WordInfo(word="b", source="s")
    a.definitions.append("x")
    assert b.definitions == []


def test_full_payload():
    info = WordInfo(
        word="run",
        definitions=["to move fast", "to operate"],
        examples=["I run daily."],
        translations=["correr"],
        pronunciation="/rʌn/",
        part_of_speech="verb",
        source="local_llm",
    )
    assert info.definitions == ["to move fast", "to operate"]
    assert info.part_of_speech == "verb"


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
