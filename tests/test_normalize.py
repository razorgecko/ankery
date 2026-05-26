from ankery.providers.normalize import strip_leading_article


def test_strips_a_german_definite_article():
    assert strip_leading_article("des Hauses", "de") == "Hauses"
    assert strip_leading_article("die Häuser", "de") == "Häuser"


def test_leaves_articleless_forms_untouched():
    assert strip_leading_article("Hauses", "de") == "Hauses"
    # A multi-word form whose first token is not an article is unchanged.
    assert strip_leading_article("hat gesehen", "de") == "hat gesehen"


def test_unknown_language_is_a_no_op():
    # No article table for the language -> trust the prompt, change nothing
    # (even if the leading token happens to be a German article).
    assert strip_leading_article("des Hauses", "fr") == "des Hauses"
    assert strip_leading_article("des Hauses", None) == "des Hauses"


def test_never_strips_the_form_down_to_nothing():
    # A bare article with no following form is left as-is rather than emptied.
    assert strip_leading_article("die", "de") == "die"
