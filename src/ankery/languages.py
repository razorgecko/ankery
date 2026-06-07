"""Language code <-> English-name resolution.

A deliberately small, hand-rolled table, not a standards-complete one. Both
directions fall back rather than fail: an unlisted token renders/normalizes as the
bare token, so the table never gates anything.
"""

# Curated: the languages someone is plausibly translating to/from. Extend freely;
# nothing breaks for an absent code, it just renders/normalizes as the bare token.
_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sv": "Swedish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "zh": "Chinese",
}

# Reverse index for name -> code, built once. Lowercased keys for case-insensitive
# lookup; codes are already lowercase ISO 639-1.
_CODES: dict[str, str] = {name.lower(): code for code, name in _NAMES.items()}


def language_name(code: str) -> str:
    """English name for a language `code`; unlisted codes fall back to `code.title()`."""
    return _NAMES.get(code.lower(), code.title())


def language_code(token: str) -> str:
    """Normalize a code-or-English-name to a code; an unknown token passes through
    lowercased, assumed to already be a code."""
    token = token.strip().lower()
    return _CODES.get(token, token)
