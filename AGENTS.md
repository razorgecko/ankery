# ankery — invariants

A Python library/CLI that turns a term into an Anki note: look the term up, get
structured info, write a card to a chosen deck. All domain knowledge lives in a
**language pack**, a directory selected by code at run time.

This file lists the rules that may not change without changing the design. The
mechanics are in [docs/](./docs/).

## Invariants

1. **The engine names no domain.** No language, part of speech or word concept in
   engine code or `defaults/`. Adding a pack is authoring a directory, never an
   engine change, and there is no default pack
   (`test_brand_new_user_pack_loads_with_no_engine_change`,
   `test_build_deck_builder_requires_a_pack`,
   `test_catch_all_is_neutral`, `test_omitting_the_template_renders_the_domain_neutral_default`).
2. **`Entry` is the only contract between layers.** Its typed core is `term`,
   `category`, `audio_url`, `source`; everything domain-shaped goes in the open
   `properties`/`collections` bags, keyed by what the pack declares. Adding a
   typed field for one domain breaks rule 1.
3. **`category` is never inferred by the engine.** It comes from the winning
   provider, or from a `term:cat` hint, which wins over any provider
   (`test_category_hint_overrides_the_providers_classification`).
4. **A pack is a trust boundary.** Loading one runs its author's code
   unsandboxed. The engine does not try to sandbox it; the README says so.
5. **The model cannot forge provenance or fabricate under a hint.** `llm`
   overwrites echoed `pack`/`variables`/`source`
   (`test_fetch_sets_provenance_and_variables`); the builder, not the template,
   appends the empty-object clause, so no pack template can drop it
   (`test_escape_hatch_is_force_appended_by_the_builder_not_the_template`); a
   term-less object under a hint is a clean miss (`test_hinted_fetch_misses_on_empty_object`).
6. **ankery never mutates an Anki model implicitly.** `verify_note_types` only
   creates. `sync_note_types` is the explicit opt-in, writes only templates and
   styling, never fields, and never a model it does not define (a foreign
   `--note-type`, the catch-all, `notes_dir` layouts).
7. **Check everything, then write.** Verify checks every existing model before
   creating any (`test_verify_checks_every_model_before_creating_any`); sync
   checks every model before any write (`test_sync_rejects_mismatch_before_writing_anything`).
   A mismatch leaves Anki unchanged. "Every model" means every model ankery
   defines: a foreign `--note-type` has no definition to check against, so it is
   neither created nor checked, and a term routed to it fails at `add_note`
   (`test_verify_skips_owned_catch_all_when_note_type_points_at_a_foreign_model`).
8. **A note definition is written once.** The runtime map and the sink's model
   creation read the same `NoteDefinition`; field names and order are not
   restated anywhere else.
9. **The secret stays out of shareable and logged surfaces.** Only
   `llm_api_key` lives in `auth.toml`; env carries only that secret and the two
   file paths (`test_from_env_ignores_non_secret_vars`,
   `test_load_auth_file_rejects_non_secret_keys`); request headers are never
   logged.

## Rules that are easy to break by accident

- **Field order is contractual.** `[map]` key order is the Anki field order, and
  Anki keys duplicate detection and its empty-note guard on the first field.
  An existing model with the same fields in a different order is rejected
  (`test_verify_rejects_field_order_difference`).
- **One definition per category per directory**, the `"*"` default note
  included (`test_two_notes_serving_one_category_in_a_directory_raise`,
  `test_two_default_notes_in_a_directory_raise`). Routing order must never
  decide which note an entry gets.
- **The pack default note is fallback only**, never a primary match; the engine
  catch-all comes after it
  (`test_unmatched_category_routes_to_the_pack_default_note_over_the_catch_all`).
- **The catch-all pairs no collections by index.** Index alignment is a
  pack-note convention (`test_catch_all_dumps_each_section_independently_without_pairing`).
- **Note `[map]` Jinja is autoescaped; prompt Jinja is not.** Provider text is
  untrusted HTML in a card (`test_field_map_escapes_provider_html`,
  `test_catch_all_escapes_provider_html_but_keeps_structure`). Card
  `qfmt`/`afmt` are Anki mustache and never go through Jinja.
- **The meaning sub-surface omits its own outputs.** Meanings render over `name`,
  `label`, `variables` only; `categories`/`common_*` are outputs of that render
  and would be circular.
- **Ask the model only for what code cannot derive.** Compute the rest in the
  pack's `filter.py`; prefer a natural form over an abstract label, and add no
  key an existing field already shows.
- **Derived keys never reach the prompt.** A key the filter sets is declared
  under `derived`, not `properties`/`collections`; declaring it both ways is a
  load error (`test_key_both_derived_and_prompted_in_a_category_raises`,
  `test_common_prompted_key_derived_in_a_category_raises`,
  `test_derived_keys_are_not_rendered_into_the_prompt`,
  `test_prompt_context_carries_no_derived_keys`).
- **The German prompt has byte-for-byte goldens** in `tests/fixtures/`. A change
  to `packs/de/pack.toml` meanings or `packs/de/prompts/system.j2` changes them;
  update them deliberately (`test_unhinted_prompt_matches_golden_byte_for_byte`).
- **`duplicateScope = "deck"` on `add_note`.** Without it AnkiConnect rejects
  cross-deck repeats.
- **AnkiConnect is always HTTP 200.** Failures are in the body's `error` field;
  a status check alone passes errors through (`test_inband_error_raises_sink_error`).
- **Pack code is loaded by file path.** Imports must be absolute, and a logger
  must be named `ankery.…` explicitly: the module name is `ankery_pack_*`,
  outside the `ankery` tree, so `-vv` would not show it.
- **Never log request headers** in a provider: they carry the bearer token.
- **Tables after bare keys.** In `pack.toml` and `config.toml` a table header
  captures every key after it. `config.py` catches an engine key under
  `[variables]` (`test_load_rejects_engine_key_captured_under_variables_table`);
  keep that check.
- **`main` dispatches on the first argv token only.** `ankery -- sync-note-types`
  adds a term (`test_sync_command_only_selected_by_first_token`).
- **`--var` replaces the `[variables]` table from `config.toml`**, never merges
  per key: the operator's values come from one source
  (`test_var_flag_replaces_config_variables`).
- **Sync loads no auth.** `with_auth=False`; nothing in sync needs the key.
- **Dry run touches no Anki**, provisioning and the shared-field query included
  (`test_dry_run_previews_without_touching_anki`,
  `test_preview_does_not_query_shared_fields`).
- **`warn_if_shared` only warns.** The note is added regardless, and a match with
  the same first field is left to Anki's duplicate check
  (`test_shared_field_warns_and_still_adds`,
  `test_note_with_the_same_first_field_is_not_warned_about`).

## Commands

```bash
uv sync
uv run pytest
uv run python -m ankery <term>
```

## Before editing, read

| editing | read |
|---|---|
| `models.py`, `manager.py`, `providers/`, module layout | [docs/architecture.md](./docs/architecture.md) |
| `pack.py`, anything under `packs/` | [docs/packs.md](./docs/packs.md) |
| `prompts.py`, `defaults/prompts/`, a pack's `prompts/` | [docs/prompts.md](./docs/prompts.md) |
| `notedef.py`, routing, `sinks/`, `defaults/notes/` | [docs/notes.md](./docs/notes.md) |
| `config.py`, `__main__.py`, uv/packaging | [docs/configuration.md](./docs/configuration.md) |
| the pack authoring guide | [docs/authoring-packs.md](./docs/authoring-packs.md) |

## Style

Comments and docstrings: [doc-style/comment-style.md](./doc-style/comment-style.md).
Documentation: [doc-style/doc-style.md](./doc-style/doc-style.md).
