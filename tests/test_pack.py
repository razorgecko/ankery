import pytest

from ankery.models import WordInfo
from ankery.pack import PackError, load_pack


# ---------------------------------------------------------------------------
# The bundled German pack
# ---------------------------------------------------------------------------


def test_bundled_de_pack_loads():
    pack = load_pack("de")

    assert pack.code == "de"
    assert pack.name == "German"
    assert pack.providers == ("netzverb", "llm")
    assert pack.category_label == "part of speech"
    assert set(pack.categories) == {
        "noun", "verb", "adjective", "adverb", "preposition",
        "pronoun", "article", "conjunction", "particle",
    }
    # Stem order: default_de, noun_de, verb_de. "Ankery DE: Word" is the pack's
    # catch-all default note (applies_to "*"); adjective/adverb/preposition have
    # no bespoke note and route to it.
    assert pack.notes and [n.name for n in pack.notes] == [
        "Ankery DE: Word", "Ankery DE: Noun", "Ankery DE: Verb",
    ]
    assert ".card" in pack.style_css


def test_de_categories_declare_feature_keys():
    pack = load_pack("de")

    assert "gender" in pack.categories["noun"].features
    assert "present_1sg" in pack.categories["verb"].features
    assert "ipa" in pack.common_features  # common to every category


def test_de_filter_hook_is_loaded_and_active():
    normalize = load_pack("de").normalize
    info = WordInfo(word="Haus", source="t", features={"genitive_sg": "des Hauses"})
    assert normalize(info).features["genitive_sg"] == "Hauses"


def test_de_provider_builder_is_registered():
    pack = load_pack("de")
    assert "netzverb" in pack.provider_builders


def test_de_provider_options_carry_netzverb_timeout():
    # Provider tunables live in the pack (lang.toml [provider_options]), not the
    # engine's Config.
    pack = load_pack("de")
    assert pack.provider_options["netzverb"]["timeout"] == 15.0


def test_unknown_code_raises():
    with pytest.raises(PackError, match="no language pack for 'zz'"):
        load_pack("zz")


# ---------------------------------------------------------------------------
# User-directory packs (override-by-code) and authoring contracts
# ---------------------------------------------------------------------------


def _write_pack(root, code, lang_toml: str) -> None:
    pack_dir = root / code
    (pack_dir / "notes").mkdir(parents=True)
    (pack_dir / "lang.toml").write_text(lang_toml, "utf-8")


def test_user_pack_overrides_bundled_by_code(tmp_path):
    _write_pack(
        tmp_path,
        "de",
        'name = "German (custom)"\nproviders = ["llm"]\n[category]\nname = "pos"\n[pos.noun]\n[pos.noun.features]\ngender = "the article"\n',
    )
    pack = load_pack("de", langs_dir=tmp_path)

    assert pack.name == "German (custom)"  # the user pack wins over the bundled one
    assert pack.providers == ("llm",)


def test_brand_new_user_pack_loads_with_no_engine_change(tmp_path):
    _write_pack(
        tmp_path,
        "xx",
        'name = "Examplish"\nproviders = ["llm"]\n[category]\nname = "pos"\n[pos.noun]\ncitation = "the lemma"\n[pos.noun.features]\nplural = "plural form"\n',
    )
    pack = load_pack("xx", langs_dir=tmp_path)

    assert pack.name == "Examplish"
    assert pack.categories["noun"].features == {"plural": "plural form"}
    # No filter.py / providers/ => identity normalize and no pack-local providers.
    info = WordInfo(word="x", source="t", features={"plural": "xs"})
    assert pack.normalize(info) is info
    assert pack.provider_builders == {}


def test_lang_toml_without_category_declaration_raises(tmp_path):
    _write_pack(tmp_path, "yy", 'name = "Y"\nproviders = ["llm"]\n')
    with pytest.raises(PackError, match="no \\[category\\] name"):
        load_pack("yy", langs_dir=tmp_path)


def test_category_naming_an_absent_table_raises(tmp_path):
    _write_pack(tmp_path, "yy", 'name = "Y"\nproviders = ["llm"]\n[category]\nname = "pos"\n')
    with pytest.raises(PackError, match="no \\[pos.\\*\\] category sections"):
        load_pack("yy", langs_dir=tmp_path)


def test_pack_chooses_its_own_category_name(tmp_path):
    # The routing dimension is pack-declared: a non-language pack can name it
    # "kind" (with a [kind.*] table) instead of "pos" — no engine change.
    _write_pack(
        tmp_path,
        "chem",
        'name = "Chemistry"\nproviders = ["llm"]\n[category]\nname = "kind"\n'
        'label = "kind of entity"\n[kind.element]\ncitation = "the element symbol"\n',
    )
    pack = load_pack("chem", langs_dir=tmp_path)

    assert pack.category_label == "kind of entity"
    assert set(pack.categories) == {"element"}


def test_provider_name_collision_across_modules_raises(tmp_path):
    _write_pack(tmp_path, "zz", 'name = "Z"\n[category]\nname = "pos"\n[pos.noun]\n')
    providers = tmp_path / "zz" / "providers"
    providers.mkdir()
    builder = "def _b(config, pack):\n    return None\nPROVIDERS = {'dup': _b}\n"
    (providers / "a.py").write_text(builder, "utf-8")
    (providers / "b.py").write_text(builder, "utf-8")

    with pytest.raises(PackError, match="registered by more than one"):
        load_pack("zz", langs_dir=tmp_path)
