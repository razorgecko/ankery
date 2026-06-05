import json
from collections.abc import Callable

import httpx
from pydantic import ValidationError

from ankery.models import WordInfo
from ankery.prompts import build_user_prompt
from ankery.providers.base import ProviderError
from ankery.providers.retry import request_with_retry


class LLMProvider:
    """Word provider backed by an OpenAI-compatible /v1/chat/completions endpoint."""

    name = "llm"

    def __init__(
        self,
        base_url: str,
        model: str,
        system_prompt_for: Callable[[str | None], str],
        *,
        source_language: str,
        target_language: str,
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
        # The pack's label for its routing dimension (e.g. "part of speech"). The
        # prompt asks the model to fill a JSON key by this name; we map it onto
        # WordInfo's generic `category` before validation.
        self.category_key = category_key
        self.source_language = source_language
        self.target_language = target_language
        self.timeout = timeout
        self.request_json_format = request_json_format
        self.api_key = api_key

    def fetch(self, word: str, category_hint: str | None = None) -> WordInfo | None:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt_for(category_hint)},
                {
                    "role": "user",
                    "content": build_user_prompt(
                        word, self.source_language, self.target_language
                    ),
                },
            ],
            "temperature": 0,
        }
        if self.request_json_format:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        url = f"{self.base_url}/chat/completions"
        try:
            # 429 (rate limited) is transient: retry it transparently, honouring
            # the server's Retry-After, before raise_for_status escalates the rest.
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

        data = _parse_json_object(content)

        # Under a category_hint the system prompt offers the model an empty object
        # as the way to reject a mistaken assertion (the word is not that class).
        # An empty/word-less object is that signal: a clean miss, not a hard error,
        # so the chain moves on rather than fabricating a card for the wrong word.
        if category_hint and not data.get("word"):
            return None

        # The prompt asks the model to fill the category under the pack's own
        # label (e.g. "part of speech"); map it onto WordInfo's generic field.
        if self.category_key in data:
            data["category"] = data.pop(self.category_key)

        # Always overwrite — never trust the model to set provenance fields.
        data["source"] = self.name
        data["source_language"] = self.source_language
        data["target_language"] = self.target_language

        try:
            return WordInfo.model_validate(data)
        except ValidationError as exc:
            raise ProviderError(f"LLM output failed WordInfo validation: {exc}") from exc


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
