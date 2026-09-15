import logging
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from ankery.notedef import NoteDefinition
from ankery.sinks.base import SinkError, SyncResult

logger = logging.getLogger(__name__)

ANKICONNECT_VERSION = 6


@dataclass(frozen=True)
class _ModelUpdate:
    """Writes one existing model needs; a None part is already in sync."""

    name: str
    templates: dict[str, dict[str, str]] | None
    css: str | None

    @property
    def parts(self) -> list[str]:
        return [
            part
            for part, value in (("templates", self.templates), ("styling", self.css))
            if value is not None
        ]


class AnkiConnectSink:
    """AnkiConnect JSON-RPC sink. Always HTTP 200; failures are in the body's `error` field."""

    def __init__(
        self,
        base_url: str = "http://localhost:8765",
        *,
        timeout: float = 10.0,
        allow_duplicate: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.allow_duplicate = allow_duplicate

    def add_note(
        self,
        *,
        deck: str,
        note_type: str,
        fields: dict[str, str],
        tags: list[str] | None = None,
    ) -> int:
        note = {
            "deckName": deck,
            "modelName": note_type,
            "fields": fields,
            "options": {
                "allowDuplicate": self.allow_duplicate,
                # Scope dedup to the target deck so the same term can live in
                # another deck; without this AnkiConnect checks the whole
                # collection for the note type and blocks cross-deck repeats.
                "duplicateScope": "deck",
                "duplicateScopeOptions": {
                    "deckName": deck,
                    "checkChildren": False,
                    "checkAllModels": False,
                },
            },
            "tags": tags or [],
        }
        result = self._invoke("addNote", note=note)
        if not isinstance(result, int):
            raise SinkError(f"addNote returned an unexpected result: {result!r}")
        return result

    def verify_note_types(
        self,
        definitions: Iterable[NoteDefinition],
        *,
        default_css: str = "",
        catch_all: str | None = None,
    ) -> list[str]:
        """Create missing note types and return the names created; raise SinkError
        if fields don't match an existing model.

        Field order is contractual: Anki keys duplicate detection and empty-note
        guard on the first field. Never mutates an existing model, and checks every
        existing model before creating any. Safe to re-run.
        """
        definitions = list(definitions)
        logger.info(
            "ankiconnect: verifying note types: %s",
            ", ".join(repr(d.name) for d in definitions) or "(none)",
        )
        existing = self._model_names()
        fallback_css = self._catch_all_css(catch_all, default_css, existing)
        _, missing = self._split_existing(definitions, existing)
        for note_def in missing:
            self._create_model(note_def, css=note_def.css or fallback_css)
        return [note_def.name for note_def in missing]

    def sync_note_types(
        self,
        definitions: Iterable[NoteDefinition],
        *,
        default_css: str = "",
    ) -> SyncResult:
        """Create missing models and overwrite the card templates and styling of
        existing ones from their definitions; return the names created and each
        changed model's name mapped to the parts written ("templates", "styling").

        A field or card-name mismatch raises SinkError; every model is checked
        before any is written, so a mismatch leaves Anki unchanged.
        """
        definitions = list(definitions)
        logger.info(
            "ankiconnect: syncing note types: %s",
            ", ".join(repr(d.name) for d in definitions) or "(none)",
        )
        present, missing = self._split_existing(definitions, self._model_names())
        # A list, not a generator: every plan must be built (and may raise) before
        # the first write below.
        updates = [
            self._plan_update(note_def, css=note_def.css or default_css)
            for note_def in present
        ]
        for note_def in missing:
            self._create_model(note_def, css=note_def.css or default_css)
        for update in updates:
            self._apply_update(update)
        return SyncResult(
            [note_def.name for note_def in missing],
            {update.name: update.parts for update in updates if update.parts},
        )

    def _split_existing(
        self, definitions: list[NoteDefinition], existing: set[str]
    ) -> tuple[list[NoteDefinition], list[NoteDefinition]]:
        """Return (present, missing); raise SinkError if a present model's fields differ."""
        present: list[NoteDefinition] = []
        missing: list[NoteDefinition] = []
        for note_def in definitions:
            if note_def.name in existing:
                self._check_fields(note_def)
                present.append(note_def)
            else:
                missing.append(note_def)
        return present, missing

    def _plan_update(self, note_def: NoteDefinition, *, css: str) -> _ModelUpdate:
        """Diff one existing model against its definition; raise SinkError if its
        card names differ."""
        live = self._model_templates(note_def.name)
        templates = {
            card.name: {"Front": card.qfmt, "Back": card.afmt} for card in note_def.cards
        }
        if set(live) != set(templates):
            raise SinkError(
                f"note type {note_def.name!r} card types differ: "
                f"found {sorted(live)}, expected {sorted(templates)}"
            )
        return _ModelUpdate(
            note_def.name,
            templates if templates != live else None,
            css if css != self._model_css(note_def.name) else None,
        )

    def _apply_update(self, update: _ModelUpdate) -> None:
        if update.templates is not None:
            logger.info("ankiconnect: updating templates of %r", update.name)
            self._invoke(
                "updateModelTemplates",
                model={"name": update.name, "templates": update.templates},
            )
        if update.css is not None:
            logger.info("ankiconnect: updating styling of %r", update.name)
            self._invoke(
                "updateModelStyling", model={"name": update.name, "css": update.css}
            )
        if not update.parts:
            logger.info("ankiconnect: note type %r already in sync", update.name)

    def _check_fields(self, note_def: NoteDefinition) -> None:
        actual = self._model_field_names(note_def.name)
        if actual != note_def.fields:
            raise SinkError(
                f"note type {note_def.name!r} fields differ: "
                f"found {actual}, expected {note_def.fields}"
            )
        logger.info("ankiconnect: note type %r exists, fields match", note_def.name)

    def _model_templates(self, note_type: str) -> dict[str, dict[str, str]]:
        result = self._invoke("modelTemplates", modelName=note_type)
        if not isinstance(result, dict):
            raise SinkError(f"modelTemplates returned an unexpected result: {result!r}")
        return result

    def _model_css(self, note_type: str) -> str:
        result = self._invoke("modelStyling", modelName=note_type)
        if not isinstance(result, dict) or not isinstance(result.get("css"), str):
            raise SinkError(f"modelStyling returned an unexpected result: {result!r}")
        return result["css"]

    def _catch_all_css(
        self, catch_all: str | None, default_css: str, existing: set[str]
    ) -> str:
        """CSS for created models with no css of their own; prefers the catch-all model's styling."""
        if not catch_all or catch_all not in existing:
            return default_css
        try:
            result = self._invoke("modelStyling", modelName=catch_all)
        except SinkError:
            return default_css
        if isinstance(result, dict) and isinstance(result.get("css"), str):
            return result["css"]
        return default_css

    def _model_names(self) -> set[str]:
        result = self._invoke("modelNames")
        if not isinstance(result, list):
            raise SinkError(f"modelNames returned an unexpected result: {result!r}")
        return set(result)

    def _model_field_names(self, note_type: str) -> list[str]:
        result = self._invoke("modelFieldNames", modelName=note_type)
        if not isinstance(result, list):
            raise SinkError(f"modelFieldNames returned an unexpected result: {result!r}")
        return result

    def _create_model(self, note_def: NoteDefinition, *, css: str) -> None:
        logger.info("ankiconnect: creating note type %r", note_def.name)
        self._invoke(
            "createModel",
            modelName=note_def.name,
            inOrderFields=note_def.fields,
            css=css,
            isCloze=False,
            cardTemplates=[
                {"Name": card.name, "Front": card.qfmt, "Back": card.afmt}
                for card in note_def.cards
            ],
        )

    def _invoke(self, action: str, **params: object) -> object:
        # Wire-level line; name the model when the params carry one so repeated
        # actions (modelFieldNames per note type) are tellable apart.
        if "modelName" in params:
            logger.debug("ankiconnect: %s %r", action, params["modelName"])
        else:
            logger.debug("ankiconnect: %s", action)
        payload = {
            "action": action,
            "version": ANKICONNECT_VERSION,
            "params": params,
        }
        try:
            response = httpx.post(self.base_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SinkError(f"AnkiConnect request to {self.base_url} failed: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise SinkError(f"AnkiConnect returned non-JSON response: {exc}") from exc

        if not isinstance(body, dict) or "error" not in body or "result" not in body:
            raise SinkError(f"Unexpected AnkiConnect response shape: {body!r}")
        if body["error"] is not None:
            raise SinkError(f"AnkiConnect error: {body['error']}")
        return body["result"]
