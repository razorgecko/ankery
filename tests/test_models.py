import pytest
from pydantic import ValidationError

from ankery.models import Entry


def test_minimal_valid():
    entry = Entry(term="ephemeral", source="local_llm")
    assert entry.term == "ephemeral"
    assert entry.source == "local_llm"


def test_open_bags_default_empty():
    entry = Entry(term="ephemeral", source="local_llm")
    assert entry.properties == {}
    assert entry.collections == {}
    assert entry.category is None


def test_optional_fields_default():
    entry = Entry(term="ephemeral", source="local_llm")
    assert entry.pack is None
    assert entry.variables == {}
    assert entry.audio_url is None


def test_features_carry_arbitrary_pack_keys():
    # The contract names no domain: gender, case forms, verb class, kana reading,
    # IPA all live in the open properties dict under pack-declared keys.
    entry = Entry(
        term="Buch",
        pack="de",
        variables={"target_language": "en"},
        category="noun",
        properties={"gender": "das", "genitive_sg": "Buches", "nominative_pl": "Bücher"},
        source="local_llm",
    )
    assert entry.properties["gender"] == "das"
    assert entry.properties["nominative_pl"] == "Bücher"


def test_sections_carry_arbitrary_pack_keys():
    # List-valued properties (definitions, examples, translations) are likewise
    # pack-declared keys of the open collections bag, not typed core fields.
    entry = Entry(
        term="run",
        category="verb",
        collections={"definitions": ["to move fast"], "examples": ["I run."]},
        source="local_llm",
    )
    assert entry.collections["definitions"] == ["to move fast"]
    assert entry.collections["examples"] == ["I run."]


def test_features_round_trip():
    entry = Entry(
        term="mögen",
        properties={"present_1sg": "mag", "present_3sg": "mag", "present_2sg": "magst"},
        source="local_llm",
    )
    restored = Entry.model_validate_json(entry.model_dump_json())
    assert restored.properties == entry.properties


def test_defaults_are_independent_instances():
    a = Entry(term="a", source="s")
    b = Entry(term="b", source="s")
    a.collections["definitions"] = ["x"]
    a.properties["k"] = "v"
    assert b.collections == {}
    assert b.properties == {}


def test_full_payload():
    entry = Entry(
        term="run",
        collections={
            "definitions": ["to move fast", "to operate"],
            "examples": ["I run daily."],
            "translations": ["correr"],
        },
        category="verb",
        properties={"ipa": "/rʌn/"},
        source="local_llm",
    )
    assert entry.collections["definitions"] == ["to move fast", "to operate"]
    assert entry.category == "verb"
    assert entry.properties["ipa"] == "/rʌn/"


def test_whitespace_is_stripped():
    entry = Entry(term="  run  ", source="  local_llm  ")
    assert entry.term == "run"
    assert entry.source == "local_llm"


def test_json_round_trip():
    entry = Entry(term="run", collections={"definitions": ["to move"]}, source="local_llm")
    restored = Entry.model_validate_json(entry.model_dump_json())
    assert restored == entry


def test_validate_from_llm_json_ignores_extra_keys():
    payload = '{"term": "run", "source": "local_llm", "confidence": 0.9}'
    entry = Entry.model_validate_json(payload)
    assert entry.term == "run"
    assert not hasattr(entry, "confidence")


def test_section_dict_value_coerced_to_flat_list():
    # A section value emitted as a dict (e.g. a model keying by language code)
    # flattens to its concatenated values.
    entry = Entry.model_validate(
        {"term": "Haus", "source": "llm", "collections": {"translations": {"en": "house"}}}
    )
    assert entry.collections["translations"] == ["house"]


def test_section_dict_with_list_values_flattened():
    entry = Entry.model_validate(
        {"term": "run", "source": "llm",
         "collections": {"translations": {"es": ["correr", "huir"]}}}
    )
    assert entry.collections["translations"] == ["correr", "huir"]


def test_section_string_value_wrapped_to_one_item_list():
    entry = Entry.model_validate(
        {"term": "run", "source": "llm", "collections": {"definitions": "to move"}}
    )
    assert entry.collections["definitions"] == ["to move"]


def test_section_list_value_passes_through():
    entry = Entry.model_validate(
        {"term": "run", "source": "llm", "collections": {"translations": ["correr"]}}
    )
    assert entry.collections["translations"] == ["correr"]


@pytest.mark.parametrize("bad_term", ["", "   "])
def test_empty_term_rejected(bad_term):
    with pytest.raises(ValidationError):
        Entry(term=bad_term, source="local_llm")


def test_missing_source_rejected():
    with pytest.raises(ValidationError):
        Entry(term="run")
