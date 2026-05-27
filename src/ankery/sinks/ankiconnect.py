from collections.abc import Iterable

import httpx

from ankery.notedef import NoteDefinition
from ankery.sinks.base import SinkError

ANKICONNECT_VERSION = 6


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
            "options": {"allowDuplicate": self.allow_duplicate},
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
    ) -> None:
        """Create missing note types; raise SinkError if fields don't match an existing model.

        Field order is contractual: Anki keys duplicate detection and empty-note
        guard on the first field. Never mutates an existing model. Safe to re-run.
        """
        existing = self._model_names()
        fallback_css = self._catch_all_css(catch_all, default_css, existing)
        for note_def in definitions:
            if note_def.name not in existing:
                self._create_model(note_def, css=note_def.css or fallback_css)
                continue
            actual = self._model_field_names(note_def.name)
            if actual != note_def.fields:
                raise SinkError(
                    f"note type {note_def.name!r} already exists in Anki with "
                    f"different fields: found {actual}, expected {note_def.fields}. "
                    "Refusing to modify a note type that may already have notes; "
                    "reconcile the fields in Anki or rename the note type."
                )

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
