from ankery.languages import language_code, language_name


def test_language_name_known_code():
    assert language_name("de") == "German"
    assert language_name("en") == "English"


def test_language_name_is_case_insensitive():
    assert language_name("DE") == "German"


def test_language_name_unknown_falls_back_to_title():
    # No table entry: render *something* legible rather than blanking the prompt line.
    assert language_name("xx") == "Xx"


def test_language_code_from_name():
    assert language_code("German") == "de"
    assert language_code("english") == "en"


def test_language_code_from_code_is_identity():
    assert language_code("de") == "de"


def test_language_code_strips_and_lowercases():
    assert language_code("  English  ") == "en"


def test_language_code_unknown_passes_through_lowercased():
    # Pass-through is deliberate: this must not restrict which packs can load.
    assert language_code("xx") == "xx"
    assert language_code("Klingon") == "klingon"


def test_round_trip():
    for code in ("de", "en", "fr", "ja", "zh"):
        assert language_code(language_name(code)) == code
