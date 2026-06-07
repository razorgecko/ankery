"""Language code <-> English-name resolution.

A deliberately small, hand-rolled table — not a standards-complete one. The engine
itself names no language and does no conversion: this module is consumed only by
pack code and by templates, never by engine wiring or pack selection. Each
*consumer* normalizes a language-typed variable to the form it needs.

Two directions, two consumers:

- `language_name(code)` is a Jinja filter (registered in `prompts.py`): a pack
  template renders `{{ variables.target_language | language_name }}` to read
  better in the prompt ("written in German" beats "written in de"). A miss falls
  back to `code.title()` so an unlisted code still renders *something* legible.
- `language_code(token)` normalizes a code-or-name to a code; it is also exposed
  as a Jinja filter and is used by the de pack's netzverb provider (which needs a
  code to negotiate Accept-Language). A miss passes through unchanged (lowercased)
  — so the table never gates which packs may load, and a language-typed variable
  value we don't list here still resolves.

The pack selector (`--pack`) is **not** routed through this table — pack codes are
their own namespace, taken literally, so a user pack named for a language is not
silently rerouted (e.g. `english` must not become `en`).
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
    """Normalize a code-or-English-name to a code; unknown tokens pass through lowercased.

    Pass-through is intentional: this must not restrict which packs can load, so a
    token we don't recognize is assumed to already be a code.
    """
    token = token.strip().lower()
    return _CODES.get(token, token)
