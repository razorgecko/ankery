import json
import logging
from collections.abc import Callable, Iterator
from itertools import chain
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
    # None: the model must be configured.
    DEFAULT_MODEL: str | None

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's response text; raise ProviderError on failure."""
        ...


class TokenSource(Protocol):
    """Supplies a current OAuth access token."""

    def access_token(self) -> str:
        """Return a token valid for the next request; raise ProviderError if none."""
        ...


class ChatCompletionsTransport:
    """Transport for an OpenAI-compatible /chat/completions endpoint."""

    name = "chat-completions"
    OWNED_KEYS = frozenset({"model", "messages", "stream"})
    DEFAULT_BASE_URL = "http://localhost:8080/v1"
    DEFAULT_MODEL: str | None = "local-model"
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


# Failure codes of plan-billed (Sign in with ChatGPT) usage.
_USAGE_ERRORS = {
    "subscription_sharing_usage_limit_exceeded": "ChatGPT plan usage limit reached",
    "subscription_sharing_usage_unavailable": "ChatGPT plan usage data unavailable",
}


class ChatGPTTransport:
    """Transport for the streamed /responses endpoint, authorized by a ChatGPT
    sign-in token."""

    name = "chatgpt"
    OWNED_KEYS = frozenset({"model", "input", "instructions", "stream", "store"})
    # Model slugs depend on the user's plan.
    DEFAULT_MODEL: str | None = None
    # Fixed: sign-in tokens are issued for this resource only.
    URL = "https://api.openai.com/v1/responses"
    # Empty: the endpoint rejects temperature, and json_object text format needs
    # the word "json" in an input message, which a pack's user prompt may lack.
    DEFAULT_PARAMS: dict[str, Any] = {}

    def __init__(
        self,
        model: str,
        *,
        token_source: TokenSource,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.token_source = token_source
        self.params = merge_params(self.DEFAULT_PARAMS, params or {})
        self.timeout = timeout

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            **self.params,
            "model": self.model,
            "instructions": system_prompt,
            "input": [{"role": "user", "content": user_prompt}],
            # The endpoint accepts only streamed, unstored responses.
            "stream": True,
            "store": False,
        }
        headers = {"Authorization": f"Bearer {self.token_source.access_token()}"}

        url = self.URL
        # Log the URL and model only, never `headers` — they carry the bearer token.
        logger.info("llm: POST %s (model %r, streamed)", url, self.model)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                request = client.build_request("POST", url, json=payload, headers=headers)
                response = request_with_retry(lambda: client.send(request, stream=True))
                try:
                    if not response.is_success:
                        response.read()
                        raise ProviderError(
                            f"LLM request to {url} failed: HTTP {response.status_code}: "
                            f"{_error_detail(response)}"
                        )
                    return _read_response_stream(response.iter_lines())
                finally:
                    response.close()
        except httpx.HTTPError as exc:
            raise ProviderError(f"LLM request to {url} failed: {exc}") from exc


def _read_response_stream(lines: Iterator[str]) -> str:
    """Join the output text deltas of a Responses event stream; the text counts
    only once `response.completed` arrives."""
    parts: list[str] = []
    for event in _sse_events(lines):
        kind = event.get("type")
        if kind == "response.output_text.delta":
            parts.append(event.get("delta", ""))
        elif kind == "response.completed":
            return "".join(parts)
        elif kind == "response.failed":
            error = (event.get("response") or {}).get("error") or {}
            code = error.get("code")
            if code in _USAGE_ERRORS:
                raise ProviderError(f"{_USAGE_ERRORS[code]} ({code})")
            raise ProviderError(
                f"LLM response failed: {code or 'unknown error'}: {error.get('message', '')}"
            )
        elif kind == "response.incomplete":
            details = (event.get("response") or {}).get("incomplete_details") or {}
            raise ProviderError(
                f"LLM response incomplete: {details.get('reason', 'unknown reason')}"
            )
        elif kind == "error":
            raise ProviderError(
                f"LLM stream error: {event.get('code') or 'unknown error'}: "
                f"{event.get('message', '')}"
            )
    raise ProviderError("LLM stream ended before response.completed")


def _sse_events(lines: Iterator[str]) -> Iterator[dict]:
    """Decode server-sent events: the JSON of each event's joined `data:` lines."""
    data: list[str] = []
    # The trailing blank line dispatches an event the stream left unterminated.
    for line in chain(lines, [""]):
        if line:
            if line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            continue
        if data:
            try:
                event = json.loads("\n".join(data))
            except json.JSONDecodeError as exc:
                raise ProviderError(f"LLM stream sent invalid JSON: {exc}") from exc
            data = []
            if isinstance(event, dict):
                yield event


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
