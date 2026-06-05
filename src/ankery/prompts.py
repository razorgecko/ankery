from ankery.pack import LanguagePack


def render_system_prompt(pack: LanguagePack, category_hint: str | None = None) -> str:
    names = sorted(pack.categories)
    # The pack's human label for its routing dimension (e.g. "part of speech").
    # It is the JSON key the model fills; the provider maps it onto WordInfo's
    # generic `category` field. Naming the key for the domain, not for the
    # engine, keeps the classification task concrete for the model.
    label = pack.category_label
    # A hint that names a category the pack declares narrows the prompt to that
    # one class: the user has already told us what the word is, so the other
    # category sections and their feature keys are just noise (and tokens). The
    # closed classification set collapses to the single value to match. An
    # unrecognised hint falls back to the full vocabulary.
    hinted = category_hint in pack.categories
    if hinted:
        names = [category_hint]

    if hinted:
        # The user has asserted the class. Stating it as a flat fact would invite
        # the model to fabricate a reading for a word that is really some other
        # category, so the assertion is conditional and carries an escape hatch:
        # an empty object, which the provider reads as a clean miss. This is how
        # a mistaken hint misses instead of hallucinating.
        category_rule = (
            f"- `{label}`: the user states this word is a {category_hint}. "
            f"If that is correct, set it to {category_hint}. If the word is NOT "
            f"actually a {category_hint}, do not force a reading or relabel it — "
            "return an empty JSON object {} and nothing else."
        )
    elif len(names) == 1:
        # A pack with a single category: nothing to choose between, state it outright.
        category_rule = f"- `{label}`: {names[0]}."
    else:
        category_rule = f"- `{label}`: exactly one of: {', '.join(names)}."
    lines: list[str] = [
        f"You are a lexicographer building Anki vocabulary cards for {pack.name}. "
        "Given a single word in the source language, return ONLY a JSON object "
        "(no prose, no markdown fences) describing it.",
        "",
        "General rules:",
        f"- `word`: the citation/dictionary form for its {label} (see below). "
        "Give the bare lemma: no article, no surrounding parentheses.",
        category_rule,
        "- `definitions` and `examples`: written in the SOURCE language. "
        "`example_translations`: the TARGET-language gloss of each example, "
        "aligned by position.",
        "- `translations`: a JSON array of strings in the TARGET language, e.g. "
        '["house", "home"]. Always an array, never an object keyed by language.',
        f"- `features`: a JSON object of grammatical properties, using EXACTLY the "
        f"keys listed below for the word's {label} plus the common keys. "
        "Omit a key only if it genuinely does not apply. Give each value bare — "
        "forms with no leading article.",
        "- Leave anything you are unsure about empty rather than guessing.",
    ]

    if pack.common_features:
        lines += ["", f"Common feature keys (any {label}):"]
        lines += [f"  {key}: {meaning}" for key, meaning in pack.common_features.items()]

    for value in names:
        spec = pack.categories[value]
        lines += ["", f"{label.capitalize()}: {value}"]
        if spec.citation:
            lines.append(f"  citation form: {spec.citation}")
        for note in spec.guidance:
            lines.append(f"  - {note}")
        if spec.features:
            lines.append("  feature keys:")
            lines += [f"    {key}: {meaning}" for key, meaning in spec.features.items()]

    return "\n".join(lines)


def build_user_prompt(word: str, source_language: str, target_language: str) -> str:
    # A category_hint is handled wholly in the system prompt (render_system_prompt):
    # it trims the vocabulary to the asserted category and adds the miss escape hatch.
    # The user turn carries only the request itself.
    return "\n".join(
        [
            f"Source language: {source_language}",
            f"Target language: {target_language}",
            f"Word: {word}",
        ]
    )
