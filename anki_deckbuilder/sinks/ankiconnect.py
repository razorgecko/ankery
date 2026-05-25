import httpx

from anki_deckbuilder.sinks.base import SinkError

ANKICONNECT_VERSION = 6


class AnkiConnectSink:
    """Writes notes to a running Anki via the AnkiConnect add-on.

    AnkiConnect speaks a small JSON-RPC dialect over HTTP. Two quirks shape this
    code: it always returns HTTP 200 and reports failures in the body's `error`
    field, and every response carries both `result` and `error` keys.
    """

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

        # Every valid AnkiConnect response is an object with exactly these keys.
        if not isinstance(body, dict) or "error" not in body or "result" not in body:
            raise SinkError(f"Unexpected AnkiConnect response shape: {body!r}")
        if body["error"] is not None:
            raise SinkError(f"AnkiConnect error: {body['error']}")
        return body["result"]
