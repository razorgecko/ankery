from ankery.pack import LanguagePack


def render_system_prompt(pack: LanguagePack, pos_hint: str | None = None) -> str:
    pos_names = sorted(pack.grammar)
    # A hint that names a POS the pack declares narrows the prompt to that one
    # class: the user has already told us what the word is, so the other POS
    # sections and their feature keys are just noise (and tokens). The closed
    # classification set collapses to the single POS to match. An unrecognised
    # hint falls back to the full vocabulary.
    hinted = pos_hint in pack.grammar
    if hinted:
        pos_names = [pos_hint]

    if hinted:
        # The user has asserted the class. Stating it as a flat fact would invite
        # the model to fabricate a reading for a word that is really some other
        # POS, so the assertion is conditional and carries an escape hatch: an
        # empty object, which the provider reads as a clean miss. This is how a
        # mistaken hint misses instead of hallucinating.
        pos_rule = (
            f"- `part_of_speech`: the user states this word is a {pos_hint}. "
            f"If that is correct, set it to {pos_hint}. If the word is NOT "
            f"actually a {pos_hint}, do not force a reading or relabel it — "
            "return an empty JSON object {} and nothing else."
        )
    elif len(pos_names) == 1:
        # A pack with a single POS: nothing to choose between, so state it outright.
        pos_rule = f"- `part_of_speech`: {pos_names[0]}."
    else:
        pos_rule = f"- `part_of_speech`: exactly one of: {', '.join(pos_names)}."
    lines: list[str] = [
        f"You are a lexicographer building Anki vocabulary cards for {pack.name}. "
        "Given a single word in the source language, return ONLY a JSON object "
        "(no prose, no markdown fences) describing it.",
        "",
        "General rules:",
        "- `word`: the citation/dictionary form for its part of speech (see "
        "below). Give the bare lemma: no article, no surrounding parentheses.",
        pos_rule,
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
    # A pos_hint is handled wholly in the system prompt (render_system_prompt):
    # it trims the vocabulary to the asserted POS and adds the miss escape hatch.
    # The user turn carries only the request itself.
    return "\n".join(
        [
            f"Source language: {source_language}",
            f"Target language: {target_language}",
            f"Word: {word}",
        ]
    )
