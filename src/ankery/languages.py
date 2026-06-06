"""Language code <-> English-name resolution.

A deliberately small, hand-rolled table — not a standards-complete one. The engine
needs a name only to read better in the LLM prompt ("Source language: German"
beats "de"); the canonical value flowing through the system is always the code.

Two directions, two callers:

- `language_name(code)` is the prompt's display path. A miss falls back to
  `code.title()` so an unlisted code still renders *something* legible rather than
  blanking the line.
- `language_code(token)` is the CLI's normalize path: it lets a flag take either a
  code or an English name (`--target-lang english`). A miss is passed through
  unchanged (lowercased) — crucially so this table never gates which packs may
  load. A pack for a language we don't list here must still resolve by its code.

The system prompt names the source language from the pack's own `name`
(authoritative) rather than this table. The user turn resolves both languages
through `language_name` for a uniform display; for any pack whose `name` matches
its code's entry here the two agree.
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
