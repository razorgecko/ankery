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
    assert set(pack.grammar) == {"noun", "verb", "adjective"}
    assert pack.notes and [n.name for n in pack.notes] == ["Noun (DE)", "Verb (DE)"]
    assert ".card" in pack.style_css


def test_de_grammar_declares_feature_keys():
    pack = load_pack("de")

    assert "gender" in pack.grammar["noun"].features
    assert "present_1sg" in pack.grammar["verb"].features
    assert "ipa" in pack.common_features  # common to every POS


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
        'name = "German (custom)"\nproviders = ["llm"]\n[pos.noun]\n[pos.noun.features]\ngender = "the article"\n',
    )
    pack = load_pack("de", langs_dir=tmp_path)

    assert pack.name == "German (custom)"  # the user pack wins over the bundled one
    assert pack.providers == ("llm",)


def test_brand_new_user_pack_loads_with_no_engine_change(tmp_path):
    _write_pack(
        tmp_path,
        "xx",
        'name = "Examplish"\nproviders = ["llm"]\n[pos.noun]\ncitation = "the lemma"\n[pos.noun.features]\nplural = "plural form"\n',
    )
    pack = load_pack("xx", langs_dir=tmp_path)

    assert pack.name == "Examplish"
    assert pack.grammar["noun"].features == {"plural": "plural form"}
    # No filter.py / providers/ => identity normalize and no pack-local providers.
    info = WordInfo(word="x", source="t", features={"plural": "xs"})
    assert pack.normalize(info) is info
    assert pack.provider_builders == {}


def test_lang_toml_without_pos_raises(tmp_path):
    _write_pack(tmp_path, "yy", 'name = "Y"\nproviders = ["llm"]\n')
    with pytest.raises(PackError, match="no \\[pos"):
        load_pack("yy", langs_dir=tmp_path)


def test_provider_name_collision_across_modules_raises(tmp_path):
    _write_pack(tmp_path, "zz", 'name = "Z"\n[pos.noun]\n')
    providers = tmp_path / "zz" / "providers"
    providers.mkdir()
    builder = "def _b(config, pack):\n    return None\nPROVIDERS = {'dup': _b}\n"
    (providers / "a.py").write_text(builder, "utf-8")
    (providers / "b.py").write_text(builder, "utf-8")

    with pytest.raises(PackError, match="registered by more than one"):
        load_pack("zz", langs_dir=tmp_path)
