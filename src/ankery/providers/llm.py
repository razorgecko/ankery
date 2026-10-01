import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from ankery.models import Entry
from ankery.providers.base import ProviderError
from ankery.providers.retry import request_with_retry

logger = logging.getLogger(__name__)


class Transport(Protocol):
    """Sends one system/user prompt pair to an LLM endpoint."""

    name: str
    # Request fields the transport builds itself.
    OWNED_KEYS: frozenset[str]

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's response text; raise ProviderError on failure."""
        ...


class ChatCompletionsTransport:
    """Transport for an OpenAI-compatible /chat/completions endpoint."""

    name = "chat-completions"
    OWNED_KEYS = frozenset({"model", "messages", "stream"})
    DEFAULT_PARAMS: dict[str, Any] = {
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.params = merge_params(self.DEFAULT_PARAMS, params or {})
        self.timeout = timeout
        self.api_key = api_key

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            **self.params,
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        url = f"{self.base_url}/chat/completions"
        # Log the URL and model only, never `headers` — they carry the bearer token.
        logger.info("llm: POST %s (model %r)", url, self.model)
        try:
            # 429 is transient; retry it before the status check handles the rest.
            response = request_with_retry(
                lambda: httpx.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"LLM request to {url} failed: {exc}") from exc
        if not response.is_success:
            raise ProviderError(
                f"LLM request to {url} failed: HTTP {response.status_code}: "
                f"{_error_detail(response)}"
            )

        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"Unexpected LLM response shape: {exc}") from exc


TRANSPORTS: dict[str, type[Transport]] = {
    ChatCompletionsTransport.name: ChatCompletionsTransport,
}


def merge_params(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Overlay `overrides` on `defaults` key by key; a None value removes the key.

    Shallow: a nested value in `overrides` replaces the default's whole.
    """
    merged = {**defaults, **overrides}
    return {key: value for key, value in merged.items() if value is not None}


def _error_detail(response: httpx.Response) -> str:
    """The endpoint's error message, from `error.message` or `detail`."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(body.get("detail"), str):
            return body["detail"]
    return response.text[:500] or response.reason_phrase


class LLMProvider:
    """Entry provider that asks an LLM, through a Transport, for a JSON entry."""

    name = "llm"

    def __init__(
        self,
        transport: Transport,
        system_prompt_for: Callable[[str | None], str],
        user_prompt_for: Callable[[str], str],
        *,
        pack: str,
        variables: dict[str, str],
        category_key: str,
    ) -> None:
        self.transport = transport
        # Rendered per fetch, not once at construction: a category_hint trims the
        # prompt to the hinted class, so the system message depends on the call.
        self.system_prompt_for = system_prompt_for
        self.user_prompt_for = user_prompt_for
        # The pack's label for its routing dimension (e.g. "part of speech"). The
        # model fills a JSON key by this name; fetch maps it onto Entry.category.
        self.category_key = category_key
        # Stamped onto the Entry as provenance; fetch overwrites whatever the model
        # echoed, never trusting it.
        self.pack = pack
        self.variables = variables

    def fetch(self, term: str, category_hint: str | None = None) -> Entry | None:
        system_prompt = self.system_prompt_for(category_hint)
        user_prompt = self.user_prompt_for(term)
        logger.info("llm: fetch %r (hint=%r)", term, category_hint)
        logger.debug("llm: system prompt:\n%s", system_prompt)
        logger.debug("llm: user prompt: %s", user_prompt)

        content = self.transport.complete(system_prompt, user_prompt)

        logger.debug("llm: response content:\n%s", content)
        data = _parse_json_object(content)

        # Under a hint, an empty/term-less object is the model's signal that the
        # entry is not that class; treat it as a clean miss, not a fabricated card.
        # Pairs with the escape-hatch clause prompts.py appends under a hint.
        if category_hint and not data.get("term"):
            logger.info("llm: term-less object under hint %r -> miss", category_hint)
            return None

        # Map the pack-labelled category key onto Entry's generic field.
        if self.category_key in data:
            data["category"] = data.pop(self.category_key)

        # Always overwrite — never trust the model to set provenance fields.
        data["source"] = self.name
        data["pack"] = self.pack
        data["variables"] = self.variables

        # Validation silently ignores unknown keys, so a mis-nested key (e.g. a
        # bare `examples` at top level instead of inside `collections`) vanishes
        # without an error; this is its only trace.
        dropped = data.keys() - Entry.model_fields.keys()
        if dropped:
            logger.debug("llm: ignoring unknown top-level keys: %s", sorted(dropped))

        try:
            return Entry.model_validate(data)
        except ValidationError as exc:
            raise ProviderError(f"LLM output failed Entry validation: {exc}") from exc


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]  # drop opening ``` / ```json
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_object(content: str) -> dict:
    text = _strip_code_fences(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"LLM did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProviderError("LLM returned valid JSON but not an object")
    return data
