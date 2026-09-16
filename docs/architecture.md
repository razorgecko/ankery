# Architecture

The pipeline, the `Entry` contract, the provider chain, and the module map. Pack
format: [packs.md](./packs.md). Prompt rendering: [prompts.md](./prompts.md).
Note types, routing, the sink: [notes.md](./notes.md). Config and CLI:
[configuration.md](./configuration.md).

## 1. Pipeline

One data contract (`Entry`) connects every layer:

```
term ──► Provider(s) ──► Entry ──► normalize ──► Manager ──► Sink ──► Anki
              ▲                       ▲             │
        (fallback chain)      (pack filter)  (route by category, map to fields)
                                                    │
      Pack ───────────────────────────────────────┘
   (categories, guidance, providers, filter, notes)
```

1. **Provider** (`term -> Entry`): a source of entry info.
2. **normalize** (`Entry -> Entry`): the pack's optional output filter.
3. **Manager** (`DeckBuilder`): runs the chain, applies normalize, routes by
   category, maps to note fields, hands the note to the sink.
4. **Sink** (`Entry -> Anki note`): writes the note.

`build_deck_builder(config)` resolves the pack from the `pack` selector, resolves
the operator's `variables` against the pack's declarations, and wires the chain,
normalize hook, note definitions, and sink from it.

## 2. The contract: `Entry` (`models.py`)

A Pydantic model. Also the schema handed to the LLM and the validation boundary
for its output. Names no domain.

- **Typed core:** `term`, `category?`, `audio_url?`, `source` (the producing
  provider). `category` is the routing discriminator: one value drawn from the
  pack's declared vocabulary (the German pack's values are parts of speech; a
  chemistry pack's might be `element`/`compound`).
- **Open vocabulary, two bags:** `properties: dict[str, str]` holds scalar values
  (`gender`, `genitive_sg`, `reading`, `ipa`, …); `collections: dict[str,
  list[str]]` holds list values (`definitions`, `examples`,
  `example_translations`, `translations`, …). Both are keyed by labels the active
  pack declares, common or per category value. The core is type-checked; the bags
  are open.
- **Write-only provenance:** `pack?` (the producing pack code) and `variables`
  (the resolved variable bag), stamped by the producing provider (`llm` and
  `netzverb`). Nothing reads them; they record the configuration a note was built
  under. The `llm` provider overwrites any `pack`/`variables`/`source` the model
  echoes with the engine-resolved values.

Boundary coercion: each `collections` value becomes a list of strings. A dict
flattens to its concatenated values (e.g. a model keying by language code), a bare
string wraps to a one-item list, a list passes through. The engine reads no
meaning into the keys. Index alignment between two collections (e.g.
`example_translations` to `examples`) is a pack convention enforced by the pack's
notes. Property values are bare forms (no article), enforced by the pack's
normalize hook.

## 3. Providers (`providers/`)

A provider is built for one pack, so its language is fixed at construction.
`fetch(term, category_hint=None) -> Entry | None`.

- Tried in chain order; the first non-`None` wins.
- `None` is a clean miss: try the next.
- `ProviderError` is a hard failure: try the next, re-raise if the chain ends with
  no result.
- `category_hint` is a canonical category value resolved at the CLI boundary from
  a `term:cat` token ([notes.md](./notes.md#2-routing)). A provider may use it to
  disambiguate or ignore it.

Name resolution: the pack's own `providers/` builders first, then the engine
registry `PROVIDER_REGISTRY` (`config.py`). Chain = `config.providers` if set,
else the pack's `providers`.

- **`llm`** — engine-level, cross-language. OpenAI-compatible
  `/v1/chat/completions` (default `http://localhost:8080/v1`, a local
  llama-server). The system prompt is rendered per fetch
  ([prompts.md](./prompts.md)), so the provider holds a `(category_hint -> str)`
  renderer, not a fixed string. The model fills a JSON key named for the pack's
  category `label` (e.g. `part of speech`), mapped onto `Entry.category`, and
  returns `properties` and `collections` as nested objects taken verbatim by
  `Entry`; the provider does no folding and holds no key list: the declared keys
  reach the model only through the rendered system prompt. Under a hint, a
  term-less object is a clean miss (`None`). Output is validated against `Entry`,
  which drops unknown top-level keys but does not check keys inside
  `properties`/`collections` against the pack's declarations; the provider stamps
  provenance. A 429 is retried (`providers/retry.py`).
  `llm_api_key` adds a Bearer header.
- **`netzverb`** (German pack) — scrapes verbformen.com and verben.de with
  BeautifulSoup. 404 is a clean miss; a 429 is retried. A `category_hint` picks
  the page directly and misses cleanly for any category it cannot scrape; with no
  hint, capitalisation selects the noun or verb URL. Reads the pack's
  `target_language` variable (via `languages.language_code`) for
  Accept-Language.

## 4. Modules

```
models.py         Entry (the contract)
config.py         Config, layered resolution, resolve_variables, wiring, sync_note_types, PROVIDER_REGISTRY
pack.py           Pack + load_pack (resolve, parse categories/derived keys/variables, load filter/providers)
prompts.py        render_system_prompt(pack, category_hint?, *, variables, template?), render_user_prompt
languages.py      language_name/language_code: code<->English-name, exposed as Jinja filters
notedef.py        NoteDefinition, load/merge from dir
manager.py        DeckBuilder: chain -> normalize -> route by category -> sink
__main__.py       CLI parsers, term:cat splitting, output verbosity
defaults/         engine-shipped neutral assets: catch-all note, prompt templates, fallback style.css
providers/base    Provider Protocol + ProviderError
providers/llm     LLMProvider (OpenAI-compatible endpoint)
providers/retry   request_with_retry: HTTP 429 backoff, shared by providers
sinks/base        AnkiSink Protocol + SinkError
sinks/ankiconnect AnkiConnectSink (JSON-RPC)
packs/de/         bundled German pack (pack.toml, filter.py, providers/, prompts/, notes/)
```

`src/` layout; `import ankery` resolves through the installed (editable) package.
Each module has a matching `tests/test_*.py`; HTTP is mocked via `pytest-httpx`.

## 5. Dependencies

`pydantic` (validates LLM output, doubles as its schema), `httpx` (all HTTP),
`beautifulsoup4` (pack scrapers), `jinja2` (note `[map]` and prompt rendering).
Dev: `pytest`, `pytest-httpx`.
