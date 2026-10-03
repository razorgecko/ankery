# ankery

A small command-line tool that adds cards to Anki. Given a term, it looks the
term up, gathers structured information about it, and writes a note to a chosen
deck.

```bash
$ ankery <term>
```

What ankery knows about a term comes entirely from a **pack** — a self-contained
description of one subject, selected at run time with `--pack`. The bundled pack
covers German vocabulary; a pack for any other subject is dropped in the same way
(see [Adding a pack](#adding-a-pack)).

## Features

- **Looks terms up automatically** through one or more configured providers (an
  LLM or a web scraper) and turns the result into a card.
- **Writes straight to a running Anki** via the
  [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on — no manual
  import step.
- **Picks the right card layout** for a term, routing by category to a dedicated
  layout and falling back to a plain front/back card for anything without one.
- **Adds several terms at once**, each to the configured deck.
- **Adapts to the active pack**: a pack supplies the categories, layouts, and
  lookup sources, so a new subject is added as a self-contained pack without
  touching the program (see [Adding a pack](#adding-a-pack)).

## Providers

A provider is a source of information about a term. The configured providers are
tried in order; the first to return an answer wins. Override the list for a single
run with `--provider`, or use `--llm` as a shorthand for `--provider llm` when you
want the model alone and no fallback.

- **llm** — asks an OpenAI-compatible language model to fill in the structured
  fields the pack declares. Works with any pack.
- **netzverb** — a scraper bundled with the German pack; it reads the Netzverb
  dictionary sites ([verbformen.com](https://www.verbformen.com),
  [verben.de](https://www.verben.de)) for grammatical detail such as gender,
  declension, and conjugation. Being pack-specific, it serves only that pack.

## Requirements

- Python 3.13+
- A running Anki with the AnkiConnect add-on (listening on
  `http://localhost:8765` by default)
- For the LLM lookup, either an OpenAI-compatible LLM endpoint (e.g. a local
  `llama-server` on `http://localhost:8080/v1`) or a ChatGPT plan (see
  [Using a ChatGPT plan](#using-a-chatgpt-plan))

## Install

Install the `ankery` command with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install --editable .
```

This drops a launcher at `~/.local/bin/ankery`, available from any directory.

## Usage

```bash
ankery <term>                    # add a single term
ankery <term1> <term2> <term3>   # add several at once
ankery --deck MyDeck <term>      # choose the destination deck
ankery --pack <code> <term>      # load a pack by code (e.g. the bundled `de`)
ankery --var KEY=VALUE <term>    # set a pack variable
ankery -n <term>                 # dry run: show the card, write nothing
ankery --sync <term>             # then ask Anki to sync with AnkiWeb
ankery -q <term>                 # quiet: no output, errors still on stderr
ankery -v <term>                 # also print each note's saved content
ankery -vv <term>                # additionally trace the lookup on stderr
```

The category comes from whichever provider answers: a scraper may infer it from
the term's form (the bundled scraper reads German capitalisation), the LLM
classifies it. When a term is ambiguous, a `:category` suffix pins it, which both
picks the right source to look up and routes to the matching card layout:

```bash
ankery Bank:noun      # not the verb sense
ankery schnell:adj    # adjective, not adverb
ankery laufen:v       # short forms work: any unambiguous prefix
```

The suffix is one of the category names the active pack declares (the bundled
pack offers `noun`, `verb`, `adjective`, `adverb`, `preposition`, …); any
unambiguous prefix is accepted.

Multi-token terms — idioms, fixed phrases, proverbs — work too, when the active
pack declares a category for them (the bundled pack declares `phrase`). Quote the
term so the shell passes it as one argument:

```bash
ankery "jemandem die Daumen drücken"
ankery "die Kirche im Dorf lassen:ph"   # :ph pins it (p alone is ambiguous)
```

Run `ankery --help` to see all options.

## Configuration

ankery is configured in three pieces:

- **`config.toml`** — every ordinary setting (deck, pack, the LLM endpoint, and
  so on). Any setting can also be given as a command-line flag, which takes
  precedence over the file.
- **Custom note types** — additional card layouts, dropped in a directory and
  pointed to from `config.toml`, replacing or extending the built-in ones.
- **Authorization** — the LLM API key, kept out of `config.toml`. Supply it in
  a sibling `auth.toml` or via the `ANKERY_LLM_API_KEY` environment variable.

File locations default to the config directory but can be overridden with
environment variables (see [Environment variables](#environment-variables)).
The config directory follows the XDG spec: if `XDG_CONFIG_HOME` is set to an
absolute path, files are read from `$XDG_CONFIG_HOME/ankery/`, otherwise from
`~/.config/ankery/`.

### config.toml

Settings live in `~/.config/ankery/config.toml`. Example:

```toml
deck = "Vocabulary"
pack = "de"
tags = ["ankery"]

[variables]
target_language = "en"
```

Keep the `[variables]` table **after** all top-level keys: a TOML table header
captures every key written below it, so a setting like `deck` placed under
`[variables]` would be read as a pack variable. ankery rejects that with an
error naming the misplaced key.

The LLM API key is the one setting that is **not** allowed here (see
[Authorization](#authorization)).

#### Available settings

| `config.toml` key | CLI flag | Default | Meaning |
|---|---|---|---|
| `deck` | `--deck` | `"Default"` | Destination deck. |
| `providers` | `--provider` | per pack | Lookup sources, tried in fallback order. Empty uses the default chain for the chosen pack. The flag takes a comma-separated list. |
| — | `--llm` | — | Shorthand for `--provider llm`: ask the language model only, with no fallback. Cannot be combined with `--provider`. |
| `pack` | `--pack` | — (required) | Pack to load, keyed by code. Required: there is no default pack. Taken literally — never normalized — so the code is used exactly as written. |
| `[variables]` table | `--var KEY=VALUE` | per pack | Opaque values the pack consumes (e.g. `target_language`). Each pack declares the keys it accepts and their defaults; an undeclared key is an error. The flag is repeatable. |
| `packs_dir` | `--packs-dir` | — | Directory of custom packs; one here overrides a built-in of the same code. |
| `note_type` | `--note-type` | `"Ankery Basic"` | Catch-all model for terms with no dedicated layout. Defaults to ankery's own provisioned model; point it at a foreign model (e.g. Anki's stock `Basic`) to write there instead. |
| `tags` | — | `[]` | Tags added to every created note. |
| `allow_duplicate` | `--allow-duplicate` | `false` | Add a note even if Anki considers it a duplicate. |
| `llm_backend` | `--llm-backend` | `"chat-completions"` | How ankery talks to the LLM: `chat-completions` for an OpenAI-compatible server, `chatgpt` for a ChatGPT plan. |
| `llm_base_url` | `--llm-url` | `"http://localhost:8080/v1"` | OpenAI-compatible LLM endpoint for `chat-completions`. Ignored by `chatgpt`, whose endpoint is fixed. |
| `llm_model` | `--llm-model` | per backend | Model name sent to the LLM. `chat-completions`: `"local-model"`; `chatgpt`: none, it must be set. |
| `llm_timeout` | — | `30.0` | LLM request timeout, in seconds. |
| `anki_url` | `--anki-url` | `"http://localhost:8765"` | AnkiConnect endpoint. |
| `anki_timeout` | — | `10.0` | AnkiConnect request timeout, in seconds. |
| `anki_sync_timeout` | — | `60.0` | Timeout for the `--sync` request, in seconds. |
| `notes_dir` | `--notes-dir` | — | Directory of custom card-layout definitions, merged over the built-in ones. |

### LLM request parameters

With the `chat-completions` backend, ankery sends the LLM a small set of default
request parameters: `temperature: 0` and
`response_format: {"type": "json_object"}`. The `chatgpt` backend sends none. To
change them, create `~/.config/ankery/llm_params.json`, with one section per
backend:

```json
{
  "chat-completions": {"temperature": 0.2, "response_format": null},
  "chatgpt": {"reasoning": {"effort": "low"}}
}
```

- Only the section of the backend in use is sent, so switching backends needs no
  edit.
- A key replaces the default of the same name; nested values are replaced whole.
- `null` removes a default (here, JSON mode for a server that rejects it).
- Any other key is passed to the endpoint as given. If the endpoint rejects it,
  the error shows the endpoint's message.
- Reserved for ankery and not settable: `model`, `messages` and `stream` under
  `chat-completions`; `model`, `input`, `instructions`, `stream` and `store`
  under `chatgpt`.

### Authorization

The LLM API key, if the endpoint needs one, can be supplied in two ways:

- an `auth.toml` file, which may hold only this one key:

  ```toml
  llm_api_key = "sk-..."
  ```

- the `ANKERY_LLM_API_KEY` environment variable, which overrides `auth.toml` if
  both are set.

If the endpoint is configured without an API key (as a local `llama-server`
can be), both can be omitted entirely. The key is used by `chat-completions`
only; `chatgpt` ignores it.

### Using a ChatGPT plan

The LLM lookup can run on a ChatGPT plan instead of an API key, through OpenAI's
Sign in with ChatGPT. Usage counts against the plan. The feature is an OpenAI
preview and may change.

1. Sign in:

   ```bash
   ankery login
   ```

   ankery prints a sign-in link and offers two ways to finish:
   - **Paste the redirect URL.** Open the link in any browser, on any machine,
     and sign in. The browser is then sent to a `http://127.0.0.1:1455/…`
     address; copy it from the address bar, even if the page fails to load,
     and paste it at the prompt.
   - **Enter `o`** (desktop only). ankery opens the link in a browser and
     waits for it to come back.

   Enter `q` to cancel.

   Once signed in, ankery lists the models the plan offers.

2. Select the backend and one of those models in `config.toml`:

   ```toml
   llm_backend = "chatgpt"
   llm_model = "gpt-5.5"
   ```

`ankery status` shows the signed-in account and the available models.
`ankery logout` signs out, after asking for confirmation, and forgets the
account; to switch accounts, log out and sign in again. The sign-in is kept in
`~/.local/state/ankery/tokens.json` (in `$XDG_STATE_HOME/ankery/` if
`XDG_STATE_HOME` is set to an absolute path), readable only by its owner,
renewed automatically, and separate from `auth.toml`. A term spelled like one
of these commands needs `--`: `ankery -- status`.

### Custom card layouts

The card layouts ship as one TOML file per note type. Additional layouts can be
added, or a built-in one replaced, by pointing `notes_dir` at a directory of
such files; it is merged *over* the built-in set, so a layout for a category that
already has one replaces it, and a layout for a new category is added.

```toml
# config.toml
notes_dir = "~/.config/ankery/notes"
```

A layout file gives the Anki note type a name, the category it serves
(`applies_to`), how each field is filled from the looked-up entry (`[map]`, whose
values are [Jinja](https://jinja.palletsprojects.com) over the entry's data and
whose key order is the Anki field order), and one or more card templates. The
entry carries `term`, a scalar `properties` bag, and a list-valued `collections`
bag (both keyed by the names the pack declares):

```toml
# ~/.config/ankery/notes/element.toml
name = "Element"
applies_to = "element"

[map]
Term   = "{{ term }}"
Number = "{{ properties.atomic_number }}"
Uses   = "{{ collections.common_uses | join(', ') }}"

[[cards]]
name = "Recognition"
qfmt = "{{Term}}"
afmt = "{{FrontSide}}<hr id=answer>{{Number}}<br><br>{{Uses}}"
```

A term whose category matches no layout falls back to the pack's default layout if
it defines one (`applies_to = "*"`), and otherwise to the catch-all `note_type` —
by default `Ankery Basic`, a model ankery ships and provisions itself (a plain
term → info-dump card). Point `--note-type` at a foreign model (e.g. Anki's stock
`Basic`) to write there instead. The card `qfmt`/`afmt` are Anki's own template
syntax, not Jinja — only the `[map]` values are rendered here.

A note type is created in Anki on first use and never modified afterwards.
Later edits to a pack's card templates or `style.css` are pushed explicitly:

```bash
ankery sync-note-types --pack chem
```

The sync creates missing note types and overwrites the templates and styling of
existing ones, **discarding edits made in Anki**. It covers only the pack's own
layouts, not `notes_dir` layouts or `Ankery Basic`. Fields and card types are
never changed; on a mismatch the sync aborts with Anki untouched. A term spelled
like the command needs `--`: `ankery -- sync-note-types`.

### Adding a pack

A pack is a self-contained description of one subject — a directory named by its
code that holds the categories ankery routes on, the card layouts, and the lookup
sources. ankery ships with a pack of its own, and more can be added without
touching the program: point `packs_dir` at a directory and drop a pack inside it,
named by code (e.g. `chem/`).

```toml
# config.toml
packs_dir = "~/.config/ankery/packs"
```

```
~/.config/ankery/packs/
  chem/
    pack.toml      category vocabulary, lookup guidance, and the provider chain
    notes/         card layouts for this pack (one *.toml per type)
      style.css    OPTIONAL: card styling for this pack
    prompts/       OPTIONAL: system.j2 / user.j2 LLM prompt templates
    filter.py      OPTIONAL: clean up looked-up results
    providers/     OPTIONAL: pack-specific lookup sources
```

Select a pack at run time with `--pack chem` (or set `pack` in `config.toml`).
A pack here whose code matches a built-in one **replaces** it, so a bundled pack
can be overridden entirely.

A `notes/style.css` is optional: if a pack omits it, cards fall back to
ankery's built-in default styling. Provide one to style this pack's cards.

A `prompts/` directory is optional too: a `system.j2` and/or `user.j2` tailors
the LLM prompt for the pack, resolved per file over a built-in default.

For a step-by-step walkthrough of building a pack from scratch see [Authoring a pack](docs/authoring-packs.md).

> **A pack is code.** `filter.py` and `providers/*.py` are Python that ankery
> imports and runs in its own process, with full access to the local network and
> filesystem — there is no sandbox. Treat installing a pack exactly like installing
> any program: only packs written or otherwise trusted should be installed.

### Environment variables

ankery reads only a few environment variables:

| Variable | Effect |
|---|---|
| `ANKERY_LLM_API_KEY` | The LLM API key. Overrides `auth.toml` if both are set. |
| `ANKERY_CONFIG` | Path to the `config.toml` to load (overridden by `--config`). |
| `ANKERY_AUTH` | Path to the `auth.toml` to load (overridden by `--auth`). |
| `XDG_CONFIG_HOME` | Base config directory, as described above. |
| `XDG_STATE_HOME` | Base directory of the ChatGPT sign-in (`tokens.json`), as described in [Using a ChatGPT plan](#using-a-chatgpt-plan). |
