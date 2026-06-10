"""Render the system and user prompts.

The template owns the prompt's wording and layout; this module owns the logic
that must hold regardless of which template is used: it collapses the
classification set to a single value under a hint, and force-appends the
empty-object escape-hatch clause after the template renders so no override can
drop it.

The escape-hatch clause pairs with `llm.py`, which reads a term-less object under
a hint as a clean miss; change the two together.
"""

import jinja2

from ankery.defaults import default_system_template, default_user_template
from ankery.languages import FILTERS as LANGUAGE_FILTERS
from ankery.pack import Pack

# Plain text, not HTML: no autoescape. ChainableUndefined renders an absent
# variable as empty rather than crashing. trim/lstrip_blocks keep the line-based
# template readable.
_env = jinja2.Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
    undefined=jinja2.ChainableUndefined,
)
# Language-naming helpers, offered to any template as Jinja filters.
_env.filters.update(LANGUAGE_FILTERS)


def _system_context(
    pack: Pack, category_hint: str | None, variables: dict[str, str]
) -> tuple[dict, bool]:
    """Build the template variable surface and the hinted flag.

    A hint that names a declared category narrows the classification set to that
    one value and flips `hinted`. An unrecognised hint falls back to the full
    vocabulary, unhinted.
    """
    names = sorted(pack.categories)
    hinted = category_hint in pack.categories
    if hinted:
        names = [category_hint]
    context = {
        "name": pack.name,
        "label": pack.category_label,
        "names": names,
        # CategorySpec objects expose .value/.citation/.guidance/.properties/.collections.
        "categories": [pack.categories[value] for value in names],
        "common_properties": pack.common_properties,
        "common_collections": pack.common_collections,
        "variables": variables,
        "hinted": hinted,
        "hint": category_hint if hinted else None,
    }
    return context, hinted


def _escape_hatch(hint: str) -> str:
    """The clause that lets a mistaken hint miss instead of fabricating a reading."""
    return (
        f"If the entry is NOT actually a {hint}, do not force a reading or relabel "
        "it — return an empty JSON object {} and nothing else."
    )


def render_system_prompt(
    pack: Pack,
    category_hint: str | None = None,
    *,
    variables: dict[str, str],
    template: str | None = None,
) -> str:
    """Render the system prompt for `pack`, optionally narrowed to `category_hint`.

    `variables` is the resolved operator bag, exposed to the template verbatim.
    `template` overrides the bundled default. The escape-hatch clause is appended
    under a hint after rendering, so no template can omit it.
    """
    context, hinted = _system_context(pack, category_hint, variables)
    text = template if template is not None else default_system_template()
    # Strip the trailing newline the line-based template leaves; the escape hatch
    # (under a hint) is appended afterwards so no template can omit it.
    rendered = _env.from_string(text).render(**context).rstrip("\n")
    if hinted:
        rendered = f"{rendered}\n\n{_escape_hatch(category_hint)}"
    return rendered


def render_user_prompt(term: str, *, template: str | None = None) -> str:
    """Render the user turn: just the term."""
    text = template if template is not None else default_user_template()
    return _env.from_string(text).render(term=term).rstrip("\n")
