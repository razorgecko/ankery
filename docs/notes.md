# Note types, routing, and the sink

`notedef.py`, routing in `manager.py`, and `sinks/ankiconnect.py`.

## 1. Note definitions

A note definition is the single source of truth for one note type: its Anki field
set and order, the Jinja `[map]` that fills each field from an `Entry`, the card
templates, and the category it serves (`applies_to`). The runtime reads the map;
the sink reads the same definition to create the model, so a field name is
written once.

File shape: see `packs/de/notes/noun_de.toml`.

- `[map]` values are Jinja, autoescaped (Anki fields are HTML); a map opts out
  per value with `| safe`.
- Key order in `[map]` is the Anki field order.
- Card `qfmt`/`afmt` are Anki's own mustache and are not run through Jinja.
- Omit `css` to fall back to the pack's `style.css`.
- `warn_if_shared` (optional) lists fields besides the first to check on add
  ([§3](#3-shared-field-warnings)). Each must be a `[map]` field, and the first
  field may not be listed (`NoteDefinitionError`).

`load_notes_from_dir` loads `*.toml` ordered by file stem. Within one directory
each category is served by at most one definition; two files with the same
`applies_to` raise. An `applies_to`-less note matches nothing and is exempt. The
`applies_to = "*"` default note is capped at one per directory by the same check.

`config.notes_dir`, when set, supplies extra layouts merged over the pack's by
category (`merge_note_definitions`): same category replaces, new category
appends, `None`-keyed appends. It is the one note channel not tied to a pack, for
sharing language-agnostic layouts.

## 2. Routing

An entry goes to the definition whose `applies_to` matches its `category`,
ignoring case and surrounding whitespace. `NoteDefinition` stores `applies_to`
lowercased, so the per-directory duplicate check and the `notes_dir` merge key
on the same spelling. No match falls back, in order, to:

1. **The pack's default note** — a definition with `applies_to = "*"`
   (`is_default`). Never a primary match; selected by `_route` only as fallback.
2. **The engine's catch-all note** (`defaults/notes/catchall.toml`, "Ankery
   Basic"). Front = term. Back = each non-empty `collections` entry as its own
   block (items joined by `<br>`, insertion order), then the `properties` block,
   blocks separated by `<hr>`. It pairs no collections by index, having no pack
   declarations at render time.

The catch-all renders via its own definition but is written into the model named
by `note_type`. That defaults to "Ankery Basic", derived from the asset's `name`,
and `--note-type` can repoint it at a foreign model. A pack with no default note
falls straight through to the catch-all.

`category` comes from the winning provider (`llm` classifies into the pack's
closed vocabulary; a scraper stamps the page type it parsed, see
[architecture.md](./architecture.md#3-providers-providers)); the engine does not
infer it. A `term:cat` CLI token overrides it. `split_category_hint` splits the
token at the last colon; `resolve_category_hint` (both in `__main__.py`) matches
the suffix against the pack's category names, case-insensitively, exact match
first, then a unique prefix. An empty, unknown or ambiguous hint is an error for
that term only; the remaining terms still run. The resolved name is passed to
providers as `category_hint` and stamped by `DeckBuilder.lookup` as `category`
before normalize.

## 3. Shared-field warnings

Anki's duplicate check compares only the first field, exactly. A note whose first
field differs but whose other fields match an existing note may be the same term
with other forms or another sense of it; only the user can tell.

`DeckBuilder.add_term`, after a successful `add_note`, queries the sink for each
`warn_if_shared` field with a non-empty rendered value (`find_notes`: same deck,
same note type). For each matching note whose first field differs, it emits one
`warnings.warn` naming the note, its first field and the shared fields. The
warning never blocks the add. An add Anki rejects, as a duplicate or otherwise,
raises before any query, so it is not warned about. A match with an identical
first field is skipped: it is the note just added, or a duplicate left to Anki's
check. `preview` (dry run) does not query.

The match is Anki's comparison: case-insensitive, over the whole field, so
`Jungen` does not match a stored `Jungen/Jungs`.

## 4. AnkiConnect (`sinks/ankiconnect.py`)

JSON-RPC to a running Anki via the AnkiConnect add-on (`http://localhost:8765`).
Responses are always HTTP 200; failures are in the body's `error` field.

**`add_note`** sets `duplicateScope = "deck"`: the same entry can live in another
deck, but a repeat within one deck is blocked unless `allow_duplicate`. Without
it AnkiConnect checks the whole collection for the note type.

**`find_notes`** is read-only: `findNotes` with the deck (subdecks excluded,
matching `checkChildren: False`), the note type and `field:value`, all escaped for
Anki search syntax (`\`, `"`, and the wildcards `*`, `_`), then `notesInfo` for
the matches' field values.

**`verify_note_types`** runs before adding, create-only and safe to re-run:

- A missing model is created from its definition (fields, cards, css).
- An existing model must have exactly the same field names in the same order, or
  it raises. Anki keys duplicate detection and its empty-note guard on the first
  field.
- Every existing model is checked before any is created.
- The catch-all model is created or checked only while `note_type` names it. A
  foreign `--note-type` (e.g. `Cloze`) is assumed to exist with the fields the
  catch-all map writes. If it doesn't, the error surfaces at `add_note` for each
  term that falls through to it, not up front.
- A created model with no css copies the catch-all model's live styling, falling
  back to the pack's `style.css` (or the engine default).

**`sync_note_types`** (`ankery sync-note-types`) is the explicit opt-in writer:

- Creates missing models (definition css, else pack `style.css`; no catch-all
  styling).
- For each existing model, overwrites card templates (`updateModelTemplates`)
  and styling (`updateModelStyling`) where they differ.
- Returns a `SyncResult`: names created, name → parts written.
- Never touches fields. A field or card-name mismatch raises, since adding or
  removing card types needs other actions and removal deletes cards.
- Checks all models before any write.
- Targets models by definition name only, so a foreign `--note-type` model is
  never written. `config.sync_note_types(config)` scopes it to the pack's own
  `pack.notes`; the engine catch-all and `notes_dir` layouts are never created or
  written by sync.
