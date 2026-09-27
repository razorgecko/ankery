from collections.abc import Sequence


def parse_term(raw: str, category_names: Sequence[str]) -> tuple[str, str | None]:
    """Parse a `term[:cat]` token into (term, canonical category or None).

    Raises ValueError if the term is empty or the hint does not resolve.
    """
    term, hint = split_category_hint(raw)
    if not term:
        raise ValueError("empty term")
    if hint is None:
        return term, None
    return term, resolve_category_hint(hint, category_names)


def split_category_hint(raw: str) -> tuple[str, str | None]:
    """Split a `term:cat` token into (term, raw_hint); no colon -> (term, None).

    The category hint is everything after the last colon, e.g. `schnell:adj` or
    `Bank:noun`. A colon is glob-safe, so terms need no shell quoting.
    """
    term, sep, hint = raw.rpartition(":")
    if not sep:
        return raw.strip(), None
    return term.strip(), hint.strip()


def resolve_category_hint(hint: str, category_names: Sequence[str]) -> str:
    """Resolve a category hint to one canonical pack category by exact-then-prefix match.

    Raises ValueError if the hint is empty, matches nothing, or is an ambiguous
    prefix of more than one declared category.
    """
    if not hint:
        raise ValueError("empty category hint after colon")
    lowered = hint.lower()
    exact = [name for name in category_names if name.lower() == lowered]
    if exact:
        return exact[0]
    prefix = [name for name in category_names if name.lower().startswith(lowered)]
    if len(prefix) == 1:
        return prefix[0]
    known = ", ".join(sorted(category_names))
    if not prefix:
        raise ValueError(f"unknown category {hint!r}; this pack knows: {known}")
    raise ValueError(
        f"ambiguous category {hint!r}; matches: {', '.join(sorted(prefix))}"
    )
