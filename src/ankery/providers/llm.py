import json

import httpx
from pydantic import ValidationError

from ankery.models import WordInfo
from ankery.prompts import SYSTEM_PROMPT, build_user_prompt
from ankery.providers.base import ProviderError
from ankery.providers.normalize import strip_leading_article


class LLMProvider:
    """Word provider backed by an OpenAI-compatible chat endpoint.

    Targets any server exposing `/v1/chat/completions` — local (Ollama,
    LM Studio, llama.cpp) or hosted (Groq, Together, OpenRouter, ...), since
    that protocol is the de facto standard. The model fills the `WordInfo`
    schema as JSON; this class is the validation boundary for that untrusted
    output.
    """

    name = "llm"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = 30.0,
        request_json_format: bool = True,
        api_key: str | None = None,
    ) -> None:
        # base_url is the OpenAI-compatible root, e.g. "http://localhost:8080/v1".
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Most servers honor response_format json_object; disable for the few
        # that reject the field.
        self.request_json_format = request_json_format
        # Bearer token for hosted endpoints; None means no auth (local servers).
        self.api_key = api_key

    def fetch(
        self,
        word: str,
        *,
        source_language: str,
        target_language: str,
    ) -> WordInfo | None:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(word, source_language, target_language),
                },
            ],
            "temperature": 0,
        }
        if self.request_json_format:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        url = f"{self.base_url}/chat/completions"
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"LLM request to {url} failed: {exc}") from exc

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"Unexpected LLM response shape: {exc}") from exc

        data = _parse_json_object(content)

        # Provenance and the language pair are inputs we own — never trust the
        # model to set them. Overwrite whatever it may have echoed back.
        data["source"] = self.name
        data["source_language"] = source_language
        data["target_language"] = target_language

        try:
            info = WordInfo.model_validate(data)
        except ValidationError as exc:
            # Small local models routinely emit JSON of the wrong shape (a field
            # as a string instead of a list, a missing required field). This is
            # the untrusted boundary: bad output must not become a silent
            # half-filled note, so raise rather than salvage. The CLI reports
            # this error (word + reason) to the user.
            raise ProviderError(f"LLM output failed WordInfo validation: {exc}") from exc

        # Enforce the WordInfo.inflections bare-form contract the prompt also
        # asks for: a model may still return "des Hauses" instead of "Hauses".
        # The prompt is the request; this strip is the guarantee, applied only
        # at this untrusted boundary (verbformen is bare by construction).
        info.inflections = {
            key: strip_leading_article(form, source_language)
            for key, form in info.inflections.items()
        }
        return info


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    lines = lines[1:]  # drop opening ``` / ```json
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
