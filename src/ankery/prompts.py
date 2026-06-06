"""The prompt builder: engine-owned logic wrapped around an overridable template.

The split is deliberate. The **template** (`defaults/prompts/system.j2`, or a pack/
operator override) owns the *chrome*: the role line, the phrasing of the general
rules, and the layout of the per-category guidance and feature listings. It is
rendered with a rich variable surface so a competent prompter has real power
(few-shot scaffolding, emphasis, reordering) without touching engine code.

The **builder** (this module) owns the *logic* that must hold for any template:

1. it computes the classification set and collapses it to the single value under a
   hint, so the vocabulary always lines up with what was requested; and
2. it **force-appends the empty-object escape-hatch clause whenever a hint is
   present**, *after* the template renders. This is the anti-hallucination
   contract: the provider-side detector (`llm.py`) reads a word-less object under a
   hint as a clean miss, so a mistaken hint misses instead of fabricating a card.
   If that clause lived in the overridable template a careless pack or operator
   could drop it and silently break the contract; appending it here makes the
   guarantee survive *any* template, trusted or not.
"""

import jinja2

from ankery.defaults import default_system_template, default_user_template
from ankery.pack import Pack

# Plain text, not HTML: no autoescape. ChainableUndefined mirrors notedef — a
# template that reaches for an absent variable renders empty rather than crashing
# a run. trim/lstrip_blocks keep the line-based template readable; the builder
# strips the single trailing newline a line-based template inevitably leaves.
_env = jinja2.Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
    undefined=jinja2.ChainableUndefined,
)


def _system_context(
    pack: Pack, category_hint: str | None, target_language: str
) -> tuple[dict, bool]:
    """Build the template variable surface and the hinted flag.

    A hint that names a declared category narrows the classification set to that
    one value (the user has told us what the word is, so the other category
    sections are noise) and flips `hinted`. An unrecognised hint falls back to the
    full vocabulary, unhinted.

    Both languages are exposed as display names so a template author spells the
    pair the same way: `source_language` is the pack's own authoritative `name`,
    `target_language` is threaded in already resolved. `name` stays meaning just
    "pack name" (it happens to equal the source language for a language pack).
    """
    names = sorted(pack.categories)
    hinted = category_hint in pack.categories
    if hinted:
        names = [category_hint]
    context = {
        "name": pack.name,
        "label": pack.category_label,
        "names": names,
        # CategorySpec objects expose .value/.citation/.guidance/.features.
        "categories": [pack.categories[value] for value in names],
        "common_features": pack.common_features,
        "source_language": pack.name,
        "target_language": target_language,
        "hinted": hinted,
        "hint": category_hint if hinted else None,
    }
    return context, hinted


def _escape_hatch(hint: str) -> str:
    """The clause that lets a mistaken hint miss instead of fabricating a reading."""
    return (
        f"If the word is NOT actually a {hint}, do not force a reading or relabel "
        "it — return an empty JSON object {} and nothing else."
    )


def render_system_prompt(
    pack: Pack,
    category_hint: str | None = None,
    *,
    target_language: str,
    template: str | None = None,
) -> str:
    """Render the system prompt for `pack`, optionally narrowed to `category_hint`.

    `target_language` is a display name; the source language comes from the pack's
    own `name` (exposed to the template as `source_language` for symmetry).
    `template` overrides the bundled chrome (the pack/operator layers supply it);
    the builder logic — set collapse and the forced escape hatch — is invariant
    across whichever layer supplied it.
    """
    context, hinted = _system_context(pack, category_hint, target_language)
    text = template if template is not None else default_system_template()
    # Strip the trailing newline a line-based template leaves so output matches a
    # hand-joined prompt; the escape hatch (under a hint) is appended afterwards so
    # no template can omit it.
    rendered = _env.from_string(text).render(**context).rstrip("\n")
    if hinted:
        rendered = f"{rendered}\n\n{_escape_hatch(category_hint)}"
    return rendered


def build_user_prompt(word: str, *, template: str | None = None) -> str:
    """Render the user turn: just the word. The language pair and any category hint
    live in the system prompt, so the user turn carries only the word itself."""
    text = template if template is not None else default_user_template()
    return _env.from_string(text).render(word=word).rstrip("\n")
