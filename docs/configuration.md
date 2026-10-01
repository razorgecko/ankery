# Configuration and CLI

`config.py` and `__main__.py`: how settings are layered, and what the CLI does
with them.

## 1. `Config`

A frozen dataclass of infrastructure: endpoints, deck, catch-all note type, tags,
the `pack` selector, the opaque `variables` bag, user pack dir. Domain behaviour
lives in the pack.

Key fields: `providers` (empty = pack's chain), `pack` (required, no default),
`variables` (default `{}`), `packs_dir`, `notes_dir`, `deck`, `note_type`,
`tags`, the `llm_*`/`anki_*` settings, `allow_duplicate`.

`llm_backend` names the LLM transport
([architecture.md](./architecture.md#3-providers-providers)); `config.toml`
rejects an unknown name at load. `llm_base_url` and `llm_model` default to
`None`, meaning the transport's own default; a transport with no default model
and no `llm_model` is a `ConfigError`. `llm_base_url` and `llm_api_key` apply to
`chat-completions` only; `chatgpt` ignores both with a warning.

## 2. Layers

Each layer overrides the last:

```
dataclass defaults  <  config.toml  <  auth.toml  <  env (secret only)  <  CLI flags
```

- Config dir: `$XDG_CONFIG_HOME/ankery/` (if absolute), else `~/.config/ankery/`.
- `config.toml` — every field except the secret; unknown keys raise.
- `auth.toml` — only `llm_api_key`; any other key raises. The split keeps
  `config.toml` shareable.
- `llm_params.json` — request parameter overrides for the LLM transport
  ([§3](#3-llm-request-parameters)). Read from the config dir only, alongside
  `config.toml` (also under `with_auth=False`); not settable in `config.toml`.
- Env — only the secret (`ANKERY_LLM_API_KEY`) and the file paths
  (`ANKERY_CONFIG`, `ANKERY_AUTH`).
- `variables` — a `[variables]` table in `config.toml`, keyed by labels the pack
  declares. Being a table header it must follow every bare top-level key;
  `config.py` catches an engine key that lands under it and points at the
  ordering. Validated against the pack's declarations only in
  `build_deck_builder`, once the pack is loaded.
  Any `--var` replaces the whole table; flags and file are not merged per key.
  The operator's values come from one source, the flags if given, else
  `config.toml`, so a run's variables follow from the command line and the
  pack's defaults without recalling the file. Pack defaults still fill every
  key the operator leaves unset (`resolve_variables`).

## 3. LLM request parameters

Each LLM transport ([architecture.md](./architecture.md#3-providers-providers))
has a default parameter set (`DEFAULT_PARAMS`) and a set of fields it builds
itself (`OWNED_KEYS`).

| transport | defaults | owned |
|---|---|---|
| `chat-completions` | `{"temperature": 0, "response_format": {"type": "json_object"}}` | `model`, `messages`, `stream` |
| `chatgpt` | `{}` | `model`, `input`, `instructions`, `stream`, `store` |

The `chatgpt` endpoint accepts `reasoning.effort`, `none` to `max`.

`llm_params.json` maps a transport name to that transport's overrides:

```json
{"chat-completions": {"temperature": 0.2, "response_format": null}}
```

- **Merge** (`merge_params`): a section key replaces the default's key, `null`
  removes it, a new key is added. Shallow: a nested value replaces the default
  whole. The request body is the merged parameters plus the owned fields.
- A section is per transport, not per server: every `chat-completions` server
  shares it. A missing section or file means the defaults.
- Values pass through unchecked. An endpoint rejection is a `ProviderError`
  carrying the endpoint's message (`error.message` or `detail`).
- **Load-time checks** cover every section, `ConfigError` naming the file:
  invalid JSON, a non-object top level or section, an unknown transport name,
  an owned key.
- JSON, not TOML, because `null` is how a default is removed.

## 4. Add command flags

`--provider` (comma list, whole chain), `--llm` (the `llm`-only chain; mutually
exclusive with `--provider`), `--pack` (taken literally, never normalized to a
language code), `--var KEY=VALUE` (repeatable), `--deck`, `--note-type`,
`--allow-duplicate`, `--llm-backend`, `--llm-url`, `--llm-model`, `--anki-url`,
`--packs-dir`, `--notes-dir`, `--config`, `--auth`.

**`-n`/`--dry-run`** looks up, routes and renders without writing.
`DeckBuilder.preview` is `add_term` minus the sink (`note_id` is None);
note-type provisioning and the shared-field query
([notes.md](./notes.md#3-shared-field-warnings)) are skipped, so no running
Anki is needed. The `-v` preview prints as `would add` (an explicit `-q` still
silences it). Lookup misses and failures keep their stderr and exit codes, so a
quiet dry run works as a validation pass.

**`--sync`** asks Anki to sync its collection with AnkiWeb once, after the term
loop, whatever the terms' outcomes; it is skipped when note type provisioning
fails. It prints `sync requested` (not at `-q`); a failure prints `sync failed:
…` and exits 1. Argparse rejects it with `--dry-run`. Semantics:
[notes.md](./notes.md#4-ankiconnect-sinksankiconnectpy).

**Verbosity** (`-q` / default / `-v` / `-vv`; `-q` and `-v` mutually exclusive):

| level | stdout | stderr |
|---|---|---|
| `-q` | nothing | errors; exit codes unchanged |
| default | `term: added`, note types created | errors |
| `-v` | + note id, note type, rendered fields (empty fields shown) | errors |
| `-vv` | as `-v` | + engine logs as `ankery: trace: …` |

Levels 0–2 are CLI formatting. The trace is `logging` under the `"ankery"`
logger tree, scoped so httpx stays quiet. Flow events log at INFO, payloads
(prompts, LLM response) at DEBUG.

Warnings (`warnings.warn`) print to stderr as `ankery: warning: …` at every
level.

## 5. `sync-note-types`

`ankery sync-note-types` has its own parser (`build_sync_parser`).

- `main` dispatches on the first argv token only: `ankery -- sync-note-types`
  adds that literal term, and command options must follow the command name.
- Shares `--config`, `--packs-dir`, `--anki-url` with the add parser
  (`_shared_parser`); adds a required `--pack`; argparse rejects anything else
  (exit 2).
- Loads config with `with_auth=False`: no `auth.toml`, no env secret.
- Calls `config.sync_note_types(config)` (no `DeckBuilder`, no verify), prints
  `created note type: <name>` and `updated note type: <name> (<parts written>)`,
  and adds no notes.

Sync semantics: [notes.md](./notes.md#4-ankiconnect-sinksankiconnectpy).

## 6. Tooling

```bash
uv sync                       # venv from pyproject + lockfile
uv run pytest                 # tests
uv run python -m ankery <term>
```

`uv build` produces the wheel/sdist (packs included).
`uv tool install --editable .` installs a global `ankery` launcher linked to
`src/`; `pyproject.toml` changes need `--reinstall`.
