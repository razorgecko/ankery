# Configuration and CLI

`config.py`, `__main__.py` and `signin.py`: how settings are layered, what the
CLI does with them, and the ChatGPT sign-in.

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
- `tokens.json` is not a layer: it is state written by ankery, outside the
  config dir, and sets no `Config` field ([§6](#6-sign-in-with-chatgpt)).
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

## 6. Sign in with ChatGPT

`signin.py` implements OpenAI's Sign in with ChatGPT for the `chatgpt` transport:
the OAuth authorization code flow with PKCE and no client secret. Endpoints are
fixed under `https://auth.openai.com`; the token resource is
`https://api.openai.com/v1`.

### Commands

`ankery login`, `ankery logout` and `ankery status` each have their own parser
(`--config` only). As with `sync-note-types` ([§5](#5-sync-note-types)), only
the first argv token selects them, and they load config with `with_auth=False`.
Their requests use `llm_timeout`. A config error exits 2.

**`login`** (`begin`, `complete`):

- Each run makes a fresh PKCE verifier, `state` and `nonce`.
- Registration: with no stored `client_id`, it authorizes as
  `dynamic_agent_client` with `agent_name_hint=ankery` and a new
  `ext_agent_host_id` (`urn:uuid:…`). The redirect returns the issued
  `client_id` (`oaiapp_…`), which is used from then on.
  `dynamic_agent_client` is never stored. Later runs send the issued
  `client_id` and the stored `ext_agent_host_id`.
- While an ID token is held, the account is hinted. The printed URL carries
  `login_hint` (the stored email) only; the URL that option 2 opens adds the ID
  token as `id_token_hint`. The SIWC docs ask for URLs holding that hint to be
  kept out of logs, so it is never printed.
- It prints the authorize URL and asks:

  ```
  1. Paste the redirect URL
  2. Open the link in a browser
  3. Cancel
  ```

  1. Reads the URL the browser was redirected to
     (`http://127.0.0.1:1455/auth/callback?…`). Needs no listener, so it works
     when the browser runs on another machine: the user copies the URL from the
     address bar of the page that failed to load. The code in it is useless
     without the verifier, which only this process holds.
  2. Calls `webbrowser.open`, then `CallbackServer` serves `127.0.0.1:1455`
     until the redirect with this run's `state` arrives (5 minutes). A request
     with another or no `state` (a stale tab, another web page) gets a 400 and
     the wait goes on. This option is offered only when a
     graphical browser can be expected and the port binds. `can_open_browser`
     expects one on macOS and Windows, and elsewhere only with `DISPLAY` or
     `WAYLAND_DISPLAY` set; without them, `webbrowser` falls back to a console
     browser that takes over the terminal. A busy port prints an error naming
     it.
  3. Cancels, as do EOF and Ctrl-C: exit 1, nothing written.

  An unavailable option is left out, not renumbered.
- `parse_callback` checks both paths in this order: path `/auth/callback`,
  `state`, `error`, `code`. `state` and `nonce` are compared as bytes, so a
  non-ASCII value is a mismatch, not a crash. A repeat sign-in's redirect may
  omit `client_id`; the one sent to authorize stays valid.
- The code is exchanged at the token endpoint. The ID token's claims are
  checked: `iss`, `aud` contains the issued `client_id`, `nonce`, `exp`
  (60 s skew), `sub`. There is no JWKS signature check, because the token comes
  straight from the token endpoint over TLS.
- It saves the record under the store lock, then prints the account and the
  model slugs. A failure exits 1 and keeps the previous record.

**`logout`**:

- Asks `Sign out <account>? [y/N]`. Anything but `y`/`yes` (any case), EOF or
  Ctrl-C exits 1 with nothing changed.
- Revokes the refresh token at the revocation endpoint. A failed revocation
  warns, and logout continues.
- Deletes the tokens and keeps the registration and the account (`signed_out`:
  `email`, `issuer`, `subject`, `client_id`, `ext_agent_host_id`). The next
  login reuses the registration, without `id_token_hint`.
- Exit 0, also when already signed out (no prompt).

**`status`** prints the account, the access token's expiry and the model slugs
(`GET /v1/models`, entries with `visibility == "list"`). It refreshes the token
first if due and never prints a token. Exit 1 when signed out or when the model
list fails.

### Token store

- Path (`tokens_path`): `$XDG_STATE_HOME/ankery/tokens.json` if
  `XDG_STATE_HOME` is absolute, else `~/.local/state/ankery/tokens.json`. It is
  never hand-edited.
- One record: `email`, `issuer`, `subject`, `client_id`, `ext_agent_host_id`,
  `id_token`, `access_token`, `refresh_token`, `expires_in`, `scopes`,
  `saved_at` (ISO 8601 UTC, taken before the token request).
- `TokenStore.save` writes atomically: temp file in the same directory,
  `chmod 0600`, `os.replace`. It creates the directory with mode `0700`.
- `TokenStore.lock` takes an exclusive lock on `tokens.json.lock` beside it
  (`fcntl.flock`; `msvcrt.locking` on Windows), seen by other processes. Every
  write holds it: refresh, `login` and `logout`. Reads need none, since the
  write is atomic.
- `auth.toml` keeps only `llm_api_key`; the OAuth tokens never go there.

### Refresh

`StoredTokens` is the `chatgpt` transport's `TokenSource`. When no sign-in is
stored, `config._token_source` raises `ConfigError`, so the run fails at wiring,
dry run included.

- `access_token()` holds the store lock while it reads the record and, if due,
  refreshes and saves. The SIWC docs require refreshes to be serialized: the
  server rejects a rotated-out refresh token as `refresh_token_reused`. A
  process that waited on the lock re-reads the record, so it uses the set the
  holder saved and makes no request.
- Within 300 s of expiry it refreshes and saves the new set before returning,
  because the refresh token rotates. The refresh form is `grant_type`,
  `client_id`, `refresh_token` and `resource`, with no `scope`. A response
  without a refresh token keeps the current one.
- An error code that the SIWC docs call terminal (`invalid_grant`,
  `invalid_refresh_token`, `token_expired`, `refresh_token_expired`,
  `refresh_token_invalidated`, `refresh_token_reused`, read from the OAuth
  `error` or the API's `error.code`) clears the tokens, keeping the
  registration as `logout` does, and is a `ProviderError` telling the user to
  run `ankery login`. Any other failure, a 429 included, keeps the tokens and is
  a `ProviderError` without that advice.

### Logging

Never log or echo a token, the token request form or response, the callback
query, the pasted URL or request headers.

- `signin` logs the endpoint and grant type only.
- `parse_callback` errors never quote the URL.
- `CallbackServer` silences the request log, which would print the code to
  stderr.

## 7. Tooling

```bash
uv sync                       # venv from pyproject + lockfile
uv run pytest                 # tests
uv run python -m ankery <term>
```

`uv build` produces the wheel/sdist (packs included).
`uv tool install --editable .` installs a global `ankery` launcher linked to
`src/`; `pyproject.toml` changes need `--reinstall`.
