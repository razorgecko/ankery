# ankery

A small command-line tool to add vocabulary cards to Anki. Given a word, it
looks the word up, gets structured information about it, and writes a note to a
chosen deck.

```bash
$ ankery <word>
```

## Features

- **Looks words up automatically** through one or more configured providers (an
  LLM or a web scraper) and turns the result into a card.
- **Writes straight to a running Anki** via the
  [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on — no manual
  import step.
- **Picks the right card layout** for the word, routing by part of speech to a
  dedicated layout and falling back to a plain front/back card for anything
  without one.
- **Adds several words at once**, each to the configured deck.
- **Works in any chosen language**, selected with `--source-lang`. New
  languages can be added as self-contained packs (see
  [Adding a language](#adding-a-language)).

## Providers

A provider is a source of word information. The configured providers are tried
in order; the first to return an answer wins. Override the list for a single run
with `--provider`.

- **llm** — asks an OpenAI-compatible language model for definitions, examples,
  and translations. Works for any language.
- **netzverb** — scrapes the Netzverb dictionary sites
  ([verbformen.com](https://www.verbformen.com),
  [verben.de](https://www.verben.de)) for grammar details like gender,
  declension, and conjugation. German only.

## Requirements

- Python 3.13+
- A running Anki with the AnkiConnect add-on (listening on
  `http://localhost:8765` by default)
- An OpenAI-compatible LLM endpoint for the LLM lookup (e.g. a local
  `llama-server` on `http://localhost:8080/v1`)

## Install

Install the `ankery` command with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install --editable .
```

This drops a launcher at `~/.local/bin/ankery`, available from any directory.

## Usage

```bash
ankery <word>                    # add a single word
ankery <word1> <word2> <word3>   # add several at once
ankery --deck MyDeck <word>      # choose the destination deck
ankery --source-lang <lang_code> <word>   # look the word up in this language
```

The part of speech comes from whichever provider answers: a scraper may guess it
from the word's form (netzverb reads German capitalisation), the LLM infers it.
When a word is ambiguous, a `:pos` suffix pins it, which both picks the
right source to look up and routes to the matching card layout:

```bash
ankery Bank:noun      # not the verb sense
ankery schnell:adj    # adjective, not adverb
ankery laufen:v       # short forms work: n, v, adj, adv, prep, …
```

The hint is the part-of-speech name the pack declares (`noun`, `verb`,
`adjective`, …); any unambiguous prefix is accepted.

Run `ankery --help` to see all options.

## Configuration

ankery is configured in three pieces:

- **`config.toml`** — every ordinary setting (deck, languages, the LLM
  endpoint, and so on). Any setting can also be given as a command-line flag,
  which takes precedence over the file.
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
source_language = "de"
target_language = "en"
tags = ["ankery"]
```

The LLM API key is the one setting that is **not** allowed here (see
[Authorization](#authorization)).

#### Available settings

| `config.toml` key | CLI flag | Default | Meaning |
|---|---|---|---|
| `deck` | `--deck` | `"Default"` | Destination deck. |
| `providers` | `--provider` | per language | Lookup sources, tried in fallback order. Empty uses the default chain for the chosen language. The flag takes a comma-separated list. |
| `source_language` | `--source-lang` | `"de"` | Language of the words being looked up. |
| `target_language` | `--target-lang` | `"en"` | Language to translate into. |
| `packs_dir` | `--packs-dir` | — | Directory of custom packs; one here overrides a built-in of the same code. |
| `note_type` | `--note-type` | `"Ankery Basic"` | Catch-all model for words with no dedicated layout. Defaults to ankery's own provisioned model; point it at a foreign model (e.g. Anki's stock `Basic`) to write there instead. |
| `tags` | — | `[]` | Tags added to every created note. |
| `allow_duplicate` | `--allow-duplicate` | `false` | Add a note even if Anki considers it a duplicate. |
| `llm_base_url` | `--llm-url` | `"http://localhost:8080/v1"` | OpenAI-compatible LLM endpoint. |
| `llm_model` | `--llm-model` | `"local-model"` | Model name sent to the LLM. |
| `llm_timeout` | — | `30.0` | LLM request timeout, in seconds. |
| `llm_request_json_format` | — | `true` | Ask the LLM for a JSON-formatted response. |
| `anki_url` | `--anki-url` | `"http://localhost:8765"` | AnkiConnect endpoint. |
| `anki_timeout` | — | `10.0` | AnkiConnect request timeout, in seconds. |
| `notes_dir` | `--notes-dir` | — | Directory of custom card-layout definitions, merged over the built-in ones. |

### Authorization

The LLM API key, if the endpoint needs one, can be supplied in two ways:

- an `auth.toml` file, which may hold only this one key:

  ```toml
  llm_api_key = "sk-..."
  ```

- the `ANKERY_LLM_API_KEY` environment variable, which overrides `auth.toml` if
  both are set.

If the endpoint is configured without an API key (as a local `llama-server`
can be), both can be omitted entirely.

### Custom card layouts

The card layouts ship as one TOML file per note type. Additional layouts can be
added, or a built-in one replaced, by pointing `notes_dir` at a directory of
such files; it is merged *over* the built-in set, so a layout for a part of
speech that already has one replaces it, and a layout for a new one is added.

```toml
# config.toml
notes_dir = "~/.config/ankery/notes"
```

A layout file gives the Anki note type a name, the part of speech it serves
(`applies_to`), how each field is filled from the looked-up word (`[map]`, whose
values are [Jinja](https://jinja.palletsprojects.com) over the word's data and
whose key order is the Anki field order), and one or more card templates:

```toml
# ~/.config/ankery/notes/adjective.toml
name = "Adjective"
applies_to = "adjective"

[map]
Word        = "{{ word }}"
Translation = "{{ translations | join(', ') }}"
Example     = "{{ examples[0] }}"

[[cards]]
name = "Recognition"
qfmt = "{{Word}}"
afmt = "{{FrontSide}}<hr id=answer>{{Translation}}<br><br>{{Example}}"
```

A word whose part of speech matches no layout falls back to the pack's default
layout if it defines one (`applies_to = "*"`), and otherwise to the catch-all
`note_type` — by default `Ankery Basic`, a model ankery ships and provisions
itself (a neutral word → info-dump card). Point `--note-type` at a foreign model
(e.g. Anki's stock `Basic`) to write there instead. The card `qfmt`/`afmt` are
Anki's own template syntax, not Jinja — only the `[map]` values are rendered here.

### Adding a language

A language is a **pack** — a directory named by its language code that holds the language's grammar, layouts, and lookup sources.
ankery ships with packs of its own, and more can be added without touching the program:
point `packs_dir` at a directory and drop a pack inside it, named by code (e.g. `fr/` for French).

```toml
# config.toml
packs_dir = "~/.config/ankery/packs"
```

```
~/.config/ankery/packs/
  fr/
    pack.toml      grammar terms, lookup guidance, and the provider chain
    notes/         card layouts for this language (one *.toml per type)
      style.css    OPTIONAL: card styling for this language
    filter.py      OPTIONAL: clean up looked-up results
    providers/     OPTIONAL: language-specific lookup sources
```

Select a pack at run time with `--source-lang fr` (or set `source_language`
in `config.toml`). A pack here whose code matches a built-in one **replaces**
it, so a bundled language can be overridden entirely.

A `notes/style.css` is optional: if a pack omits it, cards fall back to
ankery's built-in default styling. Provide one to style this language's cards.

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
