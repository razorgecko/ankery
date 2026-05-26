import json

import pytest

from ankery.providers.base import ProviderError
from ankery.providers.llm import LLMProvider

BASE_URL = "http://localhost:8080/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"


def _completion(content: str) -> dict:
    """Wrap a content string in an OpenAI chat-completion envelope."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _provider() -> LLMProvider:
    return LLMProvider(base_url=BASE_URL, model="test-model")


def test_fetch_returns_wordinfo(httpx_mock):
    word_json = json.dumps(
        {
            "word": "Buch",
            "part_of_speech": "noun",
            "gender": "das",
            "definitions": ["gebundene Seiten zum Lesen"],
            "inflections": {"genitive_sg": "Buches", "nominative_pl": "Bücher"},
        }
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(word_json))

    info = _provider().fetch("Buch", source_language="de", target_language="en")

    assert info is not None
    assert info.word == "Buch"
    assert info.gender == "das"
    assert info.inflections["nominative_pl"] == "Bücher"


def test_fetch_strips_leading_articles_from_inflections(httpx_mock):
    # The prompt asks for bare forms, but a model may still return them with an
    # article; the provider enforces the WordInfo.inflections bare-form contract
    # at this untrusted boundary.
    word_json = json.dumps(
        {
            "word": "Haus",
            "part_of_speech": "noun",
            "gender": "das",
            "inflections": {"genitive_sg": "des Hauses", "nominative_pl": "die Häuser"},
        }
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(word_json))

    info = _provider().fetch("Haus", source_language="de", target_language="en")

    assert info.inflections == {"genitive_sg": "Hauses", "nominative_pl": "Häuser"}


def test_fetch_sets_provenance_and_languages(httpx_mock):
    # The model echoes back bogus provenance/language values; the provider must
    # overwrite them with what it controls.
    word_json = json.dumps(
        {
            "word": "Buch",
            "source": "hallucinated",
            "source_language": "xx",
            "target_language": "yy",
        }
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(word_json))

    info = _provider().fetch("Buch", source_language="de", target_language="en")

    assert info.source == "llm"
    assert info.source_language == "de"
    assert info.target_language == "en"


def test_fetch_strips_code_fences(httpx_mock):
    fenced = '```json\n{"word": "Buch"}\n```'
    httpx_mock.add_response(url=CHAT_URL, json=_completion(fenced))

    info = _provider().fetch("Buch", source_language="de", target_language="en")

    assert info.word == "Buch"


def test_request_payload_carries_prompt_and_model(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    _provider().fetch("Buch", source_language="de", target_language="en")

    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["response_format"] == {"type": "json_object"}
    roles = {m["role"]: m["content"] for m in body["messages"]}
    assert "lexicographer" in roles["system"]
    assert "Word: Buch" in roles["user"]


def test_no_response_format_when_disabled(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    provider = LLMProvider(
        base_url=BASE_URL, model="test-model", request_json_format=False
    )
    provider.fetch("Buch", source_language="de", target_language="en")

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "response_format" not in body


def test_api_key_sends_bearer_header(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    provider = LLMProvider(base_url=BASE_URL, model="test-model", api_key="sk-123")
    provider.fetch("Buch", source_language="de", target_language="en")

    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer sk-123"


def test_no_auth_header_without_api_key(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    _provider().fetch("Buch", source_language="de", target_language="en")

    request = httpx_mock.get_requests()[0]
    assert "Authorization" not in request.headers


def test_auth_rejection_raises_provider_error(httpx_mock):
    # A wrong or missing key earns a 401 from the server (verified live against a
    # keyed llama-server); raise_for_status must turn that into a ProviderError.
    httpx_mock.add_response(url=CHAT_URL, status_code=401)

    provider = LLMProvider(base_url=BASE_URL, model="test-model", api_key="wrong")
    with pytest.raises(ProviderError):
        provider.fetch("Buch", source_language="de", target_language="en")


def test_http_error_raises_provider_error(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, status_code=500)

    with pytest.raises(ProviderError):
        _provider().fetch("Buch", source_language="de", target_language="en")


def test_non_json_content_raises_provider_error(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion("not json at all"))

    with pytest.raises(ProviderError):
        _provider().fetch("Buch", source_language="de", target_language="en")


def test_unexpected_response_shape_raises_provider_error(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json={"unexpected": "shape"})

    with pytest.raises(ProviderError):
        _provider().fetch("Buch", source_language="de", target_language="en")


def test_invalid_wordinfo_raises_provider_error(httpx_mock):
    # Empty `word` fails WordInfo validation (min_length=1).
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": ""}'))

    with pytest.raises(ProviderError):
        _provider().fetch("Buch", source_language="de", target_language="en")


def test_malformed_output_raises_with_validation_detail(httpx_mock):
    # The motivating case: a small model returns `definitions` as a string
    # instead of a list. The rejection must surface as a ProviderError whose
    # message carries the validation detail (the CLI reports this to the user).
    bad = json.dumps({"word": "schnell", "definitions": "rasch, zügig"})
    httpx_mock.add_response(url=CHAT_URL, json=_completion(bad))

    with pytest.raises(ProviderError, match="definitions"):
        _provider().fetch("schnell", source_language="de", target_language="en")
