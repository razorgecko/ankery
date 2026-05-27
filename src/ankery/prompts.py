"""Render the LLM system prompt from a language pack.

The `features` dict on `WordInfo` is intentionally untyped, so consistency comes
from telling the LLM exactly which keys to fill. That key vocabulary — and the
grammar guidance around it — is no longer hardcoded here; it lives in the active
pack (lang.toml, see pack.py). This module is a pure renderer: pack in, prompt
out. Adding a language is authoring a pack, never editing this file.

The set of parts of speech the pack declares doubles as the closed vocabulary
the model classifies `part_of_speech` into, so routing (which keys on a note)
always lines up with what was requested.
"""

from ankery.pack import LanguagePack


def render_system_prompt(pack: LanguagePack) -> str:
    """Build the system prompt for `pack`: general rules + per-POS feature keys."""
    pos_names = sorted(pack.grammar)
    lines: list[str] = [
        f"You are a lexicographer building Anki vocabulary cards for {pack.name}. "
        "Given a single word in the source language, return ONLY a JSON object "
        "(no prose, no markdown fences) describing it.",
        "",
        "General rules:",
        "- `word`: the citation/dictionary form for its part of speech (see "
        "below). Give the bare lemma: no article, no surrounding parentheses.",
        f"- `part_of_speech`: exactly one of: {', '.join(pos_names)}.",
        "- `definitions` and `examples`: written in the SOURCE language. "
        "`example_translations`: the TARGET-language gloss of each example, "
        "aligned by position.",
        "- `translations`: a JSON array of strings in the TARGET language, e.g. "
        '["house", "home"]. Always an array, never an object keyed by language.',
        "- `features`: a JSON object of grammatical properties, using EXACTLY the "
        "keys listed below for the word's part of speech plus the common keys. "
        "Omit a key only if it genuinely does not apply. Give each value bare — "
        "forms with no leading article.",
        "- Leave anything you are unsure about empty rather than guessing.",
    ]

    if pack.common_features:
        lines += ["", "Common feature keys (any part of speech):"]
        lines += [f"  {key}: {meaning}" for key, meaning in pack.common_features.items()]

    for pos in pos_names:
        grammar = pack.grammar[pos]
        lines += ["", f"Part of speech: {pos}"]
        if grammar.citation:
            lines.append(f"  citation form: {grammar.citation}")
        for note in grammar.guidance:
            lines.append(f"  - {note}")
        if grammar.features:
            lines.append("  feature keys:")
            lines += [f"    {key}: {meaning}" for key, meaning in grammar.features.items()]

    return "\n".join(lines)


def build_user_prompt(word: str, source_language: str, target_language: str) -> str:
    """Render the per-word instruction for the LLM provider."""
    return (
        f"Source language: {source_language}\n"
        f"Target language: {target_language}\n"
        f"Word: {word}"
    )
