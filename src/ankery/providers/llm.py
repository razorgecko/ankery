import json
import logging
from collections.abc import Callable

import httpx
from pydantic import ValidationError

from ankery.models import Entry
from ankery.providers.base import ProviderError
from ankery.providers.retry import request_with_retry

logger = logging.getLogger(__name__)


class LLMProvider:
    """Entry provider backed by an OpenAI-compatible /v1/chat/completions endpoint."""

    name = "llm"

    def __init__(
        self,
        base_url: str,
        model: str,
        system_prompt_for: Callable[[str | None], str],
        user_prompt_for: Callable[[str], str],
        *,
        pack: str,
        variables: dict[str, str],
        category_key: str,
        timeout: float = 30.0,
        request_json_format: bool = True,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
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
        self.timeout = timeout
        self.request_json_format = request_json_format
        self.api_key = api_key

    def fetch(self, term: str, category_hint: str | None = None) -> Entry | None:
        system_prompt = self.system_prompt_for(category_hint)
        user_prompt = self.user_prompt_for(term)
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
        }
        if self.request_json_format:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        url = f"{self.base_url}/chat/completions"
        # Log the payload only, never `headers` — they carry the bearer token.
        logger.info("llm: POST %s (model %r, hint=%r)", url, self.model, category_hint)
        logger.debug("llm: system prompt:\n%s", system_prompt)
        logger.debug("llm: user prompt: %s", user_prompt)
        try:
            # 429 is transient; retry it before raise_for_status handles the rest.
            response = request_with_retry(
                lambda: httpx.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"LLM request to {url} failed: {exc}") from exc

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"Unexpected LLM response shape: {exc}") from exc

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
