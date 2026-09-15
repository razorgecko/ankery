# Packs

Reference for the pack format and how `pack.py` loads it. For a walkthrough of
writing one, see [authoring-packs.md](./authoring-packs.md).

## 1. Resolution

A pack is a directory keyed by code. Bundled: `src/ankery/packs/<code>/`. A user
pack at `<packs_dir>/<code>/` overrides the bundled one of the same code
(whole-directory override). `load_pack(code, packs_dir)` resolves and loads one;
an unknown code or malformed pack raises `PackError`.

```
packs/de/
  pack.toml      routing dimension ([category]), common + per-category property
                 and collection keys + meanings, LLM guidance, declared [variables],
                 preferred provider chain, provider options
  notes/         card layouts (one *.toml per note type) + optional style.css
                 (absent => engine default src/ankery/defaults/style.css)
  prompts/       OPTIONAL: system.j2 / user.j2, resolved per file pack > defaults
  filter.py      OPTIONAL: normalize(Entry) -> Entry; absent => identity
  providers/     OPTIONAL: each *.py exposes PROVIDERS: {name: (config, pack)
                 -> Provider}; dicts merged, duplicate name => PackError
```

## 2. `pack.toml`

**Routing dimension.** A `[category]` table: `name` is the table that enumerates
the category values (the German pack uses `name = "pos"`, so `[pos.*]`); `label`
(default = `name`) is the phrase the LLM prompt uses and the JSON key the model
fills, mapped onto `Entry.category` by the `llm` provider. The category-value set
is also the closed vocabulary the LLM classifies into, so routing lines up with
what was requested.

**Keys.** Each is `key -> meaning`:

- `[properties]` — scalar keys common to every category.
- `[collections]` — list-valued keys common to every category. The German pack
  declares `definitions`, `examples`, `example_translations`, `translations`
  here.
- `[<name>.<value>]` — one table per category value, with a `citation` form,
  optional `guidance`, and `[<name>.<value>.properties]` /
  `[<name>.<value>.collections]` for keys specific to that value.

TOML ordering: `[category]`, `[properties]`, `[collections]` and the category
tables must follow the bare top-level keys (`name`, `providers`), because a table
header captures every key after it.

Notes read keys via Jinja (`{{ properties.gender }}`,
`{{ collections.translations }}`); absent or undeclared keys render empty
(`ChainableUndefined`).

**Variables.** An optional `[variables]` table declares the operator-supplied
variables the pack consumes: each `[variables.<key>]` with an optional `meaning`
and `default`. The engine names none of them. It seeds from declared defaults,
overlays the operator's values (see [configuration.md](./configuration.md#2-layers)
for where they come from), rejects any undeclared key, and
hands the resolved bag to the pack's prompt template and providers. The German
pack declares `target_language` (default `en`), read by its prompt and the
netzverb scraper to pick the language of translations and glosses.

## 3. Pack code

`filter.py` and `providers/*.py` are pack-author code loaded by file path:

- Imports must be absolute.
- They run in ankery's interpreter with its dependencies (`httpx`, `bs4`)
  available.
- Loggers must be named under `ankery.` explicitly (see netzverb's
  `ankery.pack.de.netzverb`), since `__name__` of a path-loaded module is not in
  the `ankery` tree and would miss the `-vv` trace.
- A failure in `normalize` is wrapped as a `ProviderError`.

Loading a pack executes its author's code unsandboxed, with full network and
filesystem access. This is documented for users in the README's "Adding a pack"
section.
