# Authoring a pack

A pack is a self-contained description of one subject ankery can build cards for:
its categories, the fields a card draws on, how to look an entry up, and how to lay
it out. The engine loads a pack by code and wires itself from what the pack
declares — adding support for something new means writing a pack, not changing the
program.

This guide builds one pack end to end. The worked example is a **chemistry** pack
(`chem`) that turns `Sodium` or `Water` into a card. Where it helps, we point out
what a different subject might declare in the same slot — the bundled German pack
(`de`) is the reference to read, and we mention German or a hypothetical Japanese
pack now and then as illustrations.

> **A pack is code.** A pack may ship a `filter.py` and provider modules written in
> Python. ankery imports and runs them in its own process, with full access to the
> local network and filesystem — there is no sandbox. Loading a pack executes its
> author's code. Installing a pack is as much a trust decision as installing any
> program: only author or install packs that are trusted.

## The one idea

Everything flows through a single shape, the **entry** — what ankery learns about
one term before it becomes a card:

```
term ──► look up ──► entry ──► clean up ──► route by category ──► Anki
```

An entry has a few fixed details (the term, its category, optional audio) and two
**open** bags a pack fills however it likes:

- **properties** — single values (`atomic_number`, `formula`).
- **collections** — lists of values (`common_uses`, `hazards`).

The keys in those bags mean nothing to the engine. A pack *declares* them, a
lookup source (usually an LLM) *fills* them, and the card layouts *read* them. That
is the whole model: declare keys, fill keys, render keys.

## Directory layout

Bundled packs live in `src/ankery/packs/<code>/`. Custom packs go in a `packs_dir`
that ankery is pointed at; a pack there overrides a bundled one of the same code.

```
chem/
  pack.toml      REQUIRED: the routing dimension, the declared keys, LLM guidance,
                 variables, and the lookup chain
  notes/         REQUIRED: card layouts (one *.toml per card type)
    style.css    OPTIONAL: card styling (absent => engine default)
  prompts/       OPTIONAL: system.j2 / user.j2 LLM prompt templates
  filter.py      OPTIONAL: a cleanup pass over each looked-up entry
  providers/     OPTIONAL: extra lookup sources written in Python
```

Only `pack.toml` and one note are strictly required; everything else has a neutral
engine default. We build `pack.toml` and one note first, then add the optional
pieces.

## Step 1 — `pack.toml`: the header

We start with the bare top-level keys. **These must come before any `[table]`
header**, because in TOML a table header captures every key written after it.

```toml
# chem/pack.toml
name = "Chemistry"

# Lookup sources, in fallback order. Names resolve against this pack's own
# providers/ first, then ankery's built-in sources (currently just "llm"). With
# no scraper of our own we rely on the LLM.
providers = ["llm"]
```

`name` is the human label used in prompts ("building Anki cards for Chemistry").
`providers` is the chain tried in order; the first to return an entry wins. A pack
with a source of its own lists it first — the German pack sets
`providers = ["netzverb", "llm"]` (more on providers in step 8).

## Step 2 — the routing dimension (`[category]`)

Every entry is routed to a card layout by one discriminator: its `category`. The
pack declares *which dimension* that is and *what values it can take*.

```toml
[category]
name = "kind"          # the table below that enumerates the values: [kind.*]
label = "kind"         # the human word the LLM prompt uses (default = name)
```

`name` points at whichever `[<name>.*]` tables enumerate the categories — here
`[kind.element]`, `[kind.compound]` — and `label` is the phrase the prompt shows
the model. The German pack uses `name = "pos"` with `label = "part of speech"`, so
its tables are `[pos.noun]`, `[pos.verb]`, and so on. The set of these tables is
also the closed list of values the LLM may classify into, so routing always lines
up with what the model can return.

## Step 3 — common keys (`[properties]`, `[collections]`)

Next we declare the open-bag keys shared by **every** category. Each entry maps a
key to its *meaning* — the description handed to the LLM. The meaning is itself
rendered as Jinja, so it can interpolate `name`, `label`, and `variables`.

```toml
# Single-value keys common to every kind of substance.
[properties]
appearance = "physical appearance at room temperature, e.g. silvery-white solid"

# List-valued keys common to every kind, the plural counterpart of [properties].
[collections]
common_uses = "a few typical real-world uses"
hazards = "notable safety hazards, if any"
```

A pack may declare *no* common keys at all — both tables are optional. A language
pack tends to fill them heavily: the German pack's common `[collections]` are the
lexical lists `definitions`, `examples`, `example_translations`, and
`translations`, with a meaning that names the target language via
`{{ variables.target_language | language_name }}`. Keys that apply to only one
category are declared on that category instead (next step).

## Step 4 — per-category tables (`[kind.*]`)

One table per category value. Each gives a `citation` (what the canonical `term`
form is), optional `guidance` (extra prose for the LLM), and its own
`[<...>.properties]` / `[<...>.collections]` for keys specific to that value.

```toml
[kind.element]
citation = "the element's English name, capitalized, e.g. Sodium"
[kind.element.properties]
symbol = "the chemical symbol, e.g. Na"
atomic_number = "the atomic number (proton count)"
atomic_mass = "the standard atomic weight, e.g. 22.99"
group = "the periodic-table group number"
period = "the periodic-table period number"
standard_state = "state at room temperature: solid, liquid, or gas"

[kind.compound]
citation = "the IUPAC name, or the common name if more familiar, e.g. Water"
guidance = [
    "Give the molecular formula with counts written inline, e.g. H2O, CO2.",
    "If the substance is a single element, classify it as element, not compound.",
]
[kind.compound.properties]
formula = "the chemical formula, e.g. H2O"
molar_mass = "the molar mass in g/mol"
state_at_stp = "state at standard temperature and pressure"
[kind.compound.collections]
constituent_elements = "the elements that make up the compound"
```

Each category names only the keys its cards need, and the LLM is asked for the keys
that apply to the category it picked. A different subject fills these tables with
its own fields — the German pack's `[pos.noun]` declares `gender`, `genitive_sg`,
and `nominative_pl`; a Japanese pack's `[pos.verb]` might declare `verb_group`
(ichidan/godan/irregular) and a `te_form`.

That is a complete `pack.toml`: a routing dimension, common keys, and two
categories with their own keys.

## Step 5 — variables (optional)

A variable is an operator-supplied knob the pack consumes. The German pack declares
`target_language` so the operator can choose the language of translations and
example glosses. As an example, we will add an explanation level as a variable:

```toml
[variables.level]
meaning = "level of the audience: school or university"
default = "school"
```

The engine seeds each variable from its declared default, lets the operator
override it (`--var level=university`), rejects any name the pack never declared,
and hands the result to the pack's prompt and lookup sources. A meaning or template
reaches the variable as `{{ variables.level }}`. A pack that needs no such knob
declares no variables.

### Language filters

For the common case of a variable holding a language code, ankery exposes two Jinja
filters: `language_name` turns a code into its English name (`en` → "English") and
`language_code` does the reverse. They work wherever pack Jinja runs — the prompt
templates and the meaning lines themselves — so a meaning can name a language from a
variable instead of hardcoding it. The German pack uses this to phrase its lexical
meanings against the operator's chosen `target_language`:

```toml
[collections]
translations = "translations into {{ variables.target_language | language_name }}"
```

With `target_language = "en"`, the LLM reads "translations into English"; an operator
who passes `--var target_language=fr` shifts every such meaning to French without
touching the pack.

## Step 6 — a note (card layout)

A pack needs at least one note. A note is the single source of truth for one card
type: its Anki field set **and order**, the Jinja that fills each field from an
entry, the card templates, and the category it serves.

```toml
# chem/notes/element.toml
name = "Ankery Chem: Element"
applies_to = "element"      # routes here when category == "element"

# Field order is the order of these keys. Name leads because Anki keys duplicate
# detection on the first field. The map reads the keys pack.toml declares for
# elements and the common keys; an absent or undeclared key renders empty.
[map]
Name   = "{{ term }}"
Symbol = "{{ properties.symbol }}"
Number = "{{ properties.atomic_number }}"
Mass   = "{{ properties.atomic_mass }}"
Uses   = "{{ collections.common_uses | join(', ') }}"

[[cards]]
name = "E1 Recognition"
qfmt = "{{Symbol}} ({{Number}})"
afmt = "{{FrontSide}}<hr id=answer>{{Name}}<br><br>Mass {{Mass}}<br>{{Uses}}"

[[cards]]
name = "E2 Production"
qfmt = "{{Name}}"
afmt = "{{FrontSide}}<hr id=answer>{{Symbol}}, Z={{Number}}"
```

Two things to keep straight:

- `[map]` values **are** Jinja and read the entry (`{{ term }}`,
  `{{ properties.symbol }}`, `{{ collections.common_uses }}`).
- Card `qfmt`/`afmt` are **Anki's own** mustache (`{{Symbol}}` is the *field* named
  Symbol) and are **not** run through Jinja. They reference the field names from the
  map, not entry keys.

Field order is contractual: Anki keys both duplicate detection and its empty-note
guard on the first field, and ankery refuses to mutate the field set of an existing
model. Enriching a card later means editing the Jinja, never adding a field.

### The catch-all fallback

A note is not required per category. Routing falls back, in order, to:

1. the pack's **default note** — a note with `applies_to = "*"`, a pack-styled card
   for every category that has no layout of its own (the German pack ships one for
   adjectives, adverbs, and prepositions), then
2. the engine's **neutral catch-all** ("Ankery Basic") — front is the term, back
   dumps whatever the entry carries. A pack that ships no default note falls
   straight through to this.

So the minimum pack is `pack.toml` plus one note — either a category-specific note,
or a single `applies_to = "*"` note that handles everything. For `chem` we might
add `compound.toml` (`applies_to = "compound"`) and skip a default.

## Step 7 — `filter.py` (optional)

If a source returns values that need cleanup, the pack can ship a `filter.py`. It
runs once on each looked-up entry, after the lookup and before routing, and returns
the cleaned-up entry. The German pack's strips stray articles ("des Hauses" →
"Hauses"). A pack with nothing to clean omits the file.

```python
# chem/filter.py — imports MUST be absolute; this file is loaded by path.
from ankery.models import Entry

def normalize(entry: Entry) -> Entry:
    # Example: collapse internal whitespace in every scalar property.
    entry.properties = {k: " ".join(v.split()) for k, v in entry.properties.items()}
    return entry
```

## Step 8 — `providers/` and `prompts/` (optional)

The cross-subject `llm` source comes for free; for many packs it is enough. A pack
adds its own lookup source only when it has a structured place worth scraping — the
German pack ships a `netzverb` scraper that pulls grammar tables from a dictionary
site. A custom source is a small Python module under `providers/`; it is handed a
term and returns either a filled entry or nothing (a clean miss, so the next source
in the chain is tried).

A `prompts/` directory with `system.j2` and/or `user.j2` overrides the bundled,
neutral prompt template per file. It is rarely needed: the default template already
loops over whatever keys a pack declares and lists them with their meanings.
Override it only to phrase something the generic template cannot — the German pack
ships one to state its source/target language split as a single rule line.

## Step 9 — try it

We point ankery at the pack and do a dry run — it looks up, routes, and renders a
card without writing anything or needing Anki running:

```bash
ankery --packs-dir ~/.config/ankery/packs --pack chem -n -v Sodium
ankery --pack chem -n -v Water
```

`-n` is the dry run, `-v` prints the rendered fields (empty fields stay visible —
that is the signal that a key did not fill). A quiet dry run (`-n -q`) doubles as a
validation pass: lookup misses and pack errors still set the exit code.

Once the rendered fields look right, we drop `-n` to write real cards (this needs
Anki running with the AnkiConnect add-on). Select the pack permanently with `pack =
"chem"` in `config.toml`.

## Checklist

- [ ] `pack.toml` with bare keys (`name`, `providers`) **before** any table.
- [ ] `[category]` naming the routing dimension and its `[<name>.*]` value tables.
- [ ] Declared keys: common `[properties]`/`[collections]`, plus per-category ones.
- [ ] At least one note in `notes/` whose `applies_to` matches a category (or `*`).
- [ ] `[map]` reads entry keys via Jinja; `qfmt`/`afmt` reference field names.
- [ ] First field chosen as the duplicate-detection key.
- [ ] Optional: `filter.py`, `providers/`, `prompts/`, `notes/style.css`.
- [ ] `ankery --pack <code> -n -v <term>` renders the expected fields.
