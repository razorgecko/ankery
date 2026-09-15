# Prompts and engine defaults

`prompts.py` and `defaults/`: how the `llm` provider's prompts are rendered, and
the neutral assets every pack falls back to.

## 1. Template and builder

The system prompt is split in two:

- **The Jinja template** owns the chrome: role line, rule phrasing,
  per-category guidance and key layout. It is rendered with `name`, `label`,
  `names`, `categories`, `common_properties`, `common_collections`, `variables`,
  `hinted`, `hint`.
- **The builder** (`render_system_prompt`) owns the invariant logic. Under a
  `category_hint` it trims the prompt to that value's section and collapses the
  classification set to the one value (an unknown hint keeps the full
  vocabulary). After the template renders it appends the empty-object clause:
  if the entry is not actually that category, return `{}`.

`render_user_prompt` renders only the term; the language pair and hint live in
the system prompt.

## 2. Template resolution

Per file: a pack's `prompts/system.j2` / `prompts/user.j2` override the bundled
`defaults/prompts/*.j2`. Resolved at pack-load time in `pack.py` (as with
`style.css`); `config.py` passes `pack.system_template`/`user_template` into the
builder.

The engine default is domain-neutral: it names no language, reads no variable,
and lists each declared property/collection key with its meaning, common and
per-category alike. A non-language pack renders on it unchanged; a pack that
declares no collections shows none. The German pack ships its own
`prompts/system.j2`, which states the source/target language split once as a
rule line and otherwise loops the same declared keys.

Templates may name languages with the `language_name`/`language_code` filters
(`{{ variables.target_language | language_name }}` → "English").

## 3. Rendering pack meanings

Pack-declared meanings, citations and guidance are rendered as Jinja before they
reach the template, over a sub-surface: `name`, `label`, `variables` (same
filters). The sub-surface omits `categories`, `common_properties` and
`common_collections`, because those are the outputs of rendering the meanings.
`_system_context` builds the template context as the sub-surface plus those
derived keys.

## 4. `defaults/`

Pack-shaped but not a pack: no `pack.toml`, no `[category]`, never selected. The
engine's guaranteed fallbacks, shipped in-package and complete:

- `notes/catchall.toml` — the neutral catch-all note, "Ankery Basic"
  ([notes.md](./notes.md#2-routing)).
- `prompts/system.j2`, `prompts/user.j2` — the neutral templates.
- `style.css` — the fallback styling.

Loaded by subset loaders that skip category parsing, so every neutral slot
resolves to pack-or-default.
