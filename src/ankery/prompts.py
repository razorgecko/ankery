"""Render the system and user prompts.

The template owns the prompt's wording and layout; this module owns the logic
that must hold regardless of which template is used: it collapses the
classification set to a single value under a hint, and force-appends the
empty-object escape-hatch clause after the template renders so no override can
drop it.

The escape-hatch clause pairs with `llm.py`, which reads a term-less object under
a hint as a clean miss; change the two together.
"""

from dataclasses import replace

import jinja2

from ankery.defaults import default_system_template, default_user_template
from ankery.languages import FILTERS as LANGUAGE_FILTERS
from ankery.pack import CategorySpec, Pack

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


def _render_meaning(text: str, surface: dict) -> str:
    """Render one meaning/guidance string as a mini-template over `surface`."""
    return _env.from_string(text).render(**surface)


def _render_meanings(meanings: dict[str, str], surface: dict) -> dict[str, str]:
    return {key: _render_meaning(value, surface) for key, value in meanings.items()}


def _render_category(spec: CategorySpec, surface: dict) -> CategorySpec:
    """A copy of `spec` with its prose (citation, guidance, key meanings) rendered."""
    return replace(
        spec,
        citation=_render_meaning(spec.citation, surface) if spec.citation else spec.citation,
        guidance=tuple(_render_meaning(note, surface) for note in spec.guidance),
        properties=_render_meanings(spec.properties, surface),
        collections=_render_meanings(spec.collections, surface),
    )


def _system_context(
    pack: Pack, category_hint: str | None, variables: dict[str, str]
) -> tuple[dict, bool]:
    """Build the template variable surface and the hinted flag.

    A hint that names a declared category narrows the classification set to that
    one value and flips `hinted`. An unrecognised hint falls back to the full
    vocabulary, unhinted.

    Pack-declared meanings, citations, and guidance are themselves rendered as
    mini-templates over the same `name`/`label`/`variables` surface the prompt
    template sees, so a meaning line may interpolate e.g.
    `{{ variables.target_language | language_name }}`.
    """
    names = sorted(pack.categories)
    hinted = category_hint in pack.categories
    if hinted:
        names = [category_hint]
    # The surface a meaning/citation/guidance line may interpolate: the primitive
    # context that exists before any rendering. The body context is this plus the
    # structural keys derived FROM rendering the meanings (categories, the rendered
    # common_* dicts) — which a meaning could not reference without circularity.
    surface = {
        "name": pack.name,
        "label": pack.category_label,
        "variables": variables,
    }
    context = {
        **surface,
        "names": names,
        # CategorySpec objects expose .value/.citation/.guidance/.properties/.collections.
        "categories": [_render_category(pack.categories[value], surface) for value in names],
        "common_properties": _render_meanings(pack.common_properties, surface),
        "common_collections": _render_meanings(pack.common_collections, surface),
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
