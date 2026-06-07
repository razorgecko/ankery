import json

import pytest

from ankery.providers.base import ProviderError
from ankery.providers.llm import LLMProvider

BASE_URL = "http://localhost:8080/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"

# Stand-in for the pack-rendered system prompt; the provider only passes it
# through, so its exact content is the renderer's concern (see test_prompts).
SYSTEM = "You are a lexicographer building Anki vocabulary cards for German."


def _completion(content: str) -> dict:
    """Wrap a content string in an OpenAI chat-completion envelope."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _provider(**kwargs) -> LLMProvider:
    # The provider renders the system prompt per fetch from a (category_hint -> str)
    # callable; the default ignores the hint and returns the constant SYSTEM.
    kwargs.setdefault("system_prompt_for", lambda category_hint=None: SYSTEM)
    kwargs.setdefault("pack", "de")
    kwargs.setdefault("variables", {"target_language": "en"})
    # The pack's category label is the JSON key the model fills; the provider
    # maps it onto WordInfo.category. The German pack labels it "part of speech".
    kwargs.setdefault("category_key", "part of speech")
    return LLMProvider(base_url=BASE_URL, model="test-model", **kwargs)


def test_fetch_returns_wordinfo(httpx_mock):
    word_json = json.dumps(
        {
            "word": "Buch",
            "part of speech": "noun",
            "definitions": ["gebundene Seiten zum Lesen"],
            "features": {"gender": "das", "genitive_sg": "Buches", "nominative_pl": "Bücher"},
        }
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(word_json))

    info = _provider().fetch("Buch")

    assert info is not None
    assert info.word == "Buch"
    # The model filled the pack's label key; the provider mapped it onto `category`.
    assert info.category == "noun"
    assert info.features["gender"] == "das"
    assert info.features["nominative_pl"] == "Bücher"


def test_hinted_fetch_misses_on_empty_object(httpx_mock):
    # The user asserted a category the word does not have; the model returns the
    # empty-object miss signal. The provider must read that as a clean miss
    # (None) so the chain moves on, not fabricate a card for the wrong word.
    httpx_mock.add_response(url=CHAT_URL, json=_completion("{}"))

    assert _provider().fetch("laufen", category_hint="noun") is None


def test_empty_object_without_hint_raises(httpx_mock):
    # The miss signal is only honoured under a hint. Without one, a word-less
    # object is just a malformed response and must fail loudly.
    httpx_mock.add_response(url=CHAT_URL, json=_completion("{}"))

    with pytest.raises(ProviderError):
        _provider().fetch("laufen")


def test_fetch_renders_system_prompt_with_the_category_hint(httpx_mock):
    # The hint reaches the renderer, not just the user prompt — so a hint can
    # trim the system prompt to the named category.
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "schnell"}'))

    provider = _provider(
        system_prompt_for=lambda category_hint=None: f"hint={category_hint}"
    )
    provider.fetch("schnell", category_hint="adjective")

    [request] = httpx_mock.get_requests()
    system_message = json.loads(request.content)["messages"][0]["content"]
    assert system_message == "hint=adjective"


def test_fetch_does_not_normalize_forms(httpx_mock):
    # Bare-form normalization is the pack's filter, applied by the manager — not
    # the provider. The provider returns features verbatim.
    word_json = json.dumps(
        {"word": "Haus", "features": {"genitive_sg": "des Hauses"}}
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(word_json))

    info = _provider().fetch("Haus")

    assert info.features["genitive_sg"] == "des Hauses"


def test_fetch_sets_provenance_and_variables(httpx_mock):
    # The model echoes back bogus provenance values; the provider must overwrite
    # them with what it controls (its construction-time pack and variables).
    word_json = json.dumps(
        {
            "word": "Buch",
            "source": "hallucinated",
            "pack": "xx",
            "variables": {"target_language": "yy"},
        }
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(word_json))

    info = _provider().fetch("Buch")

    assert info.source == "llm"
    assert info.pack == "de"
    assert info.variables == {"target_language": "en"}


def test_fetch_strips_code_fences(httpx_mock):
    fenced = '```json\n{"word": "Buch"}\n```'
    httpx_mock.add_response(url=CHAT_URL, json=_completion(fenced))

    info = _provider().fetch("Buch")

    assert info.word == "Buch"


def test_request_payload_carries_prompt_and_model(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    _provider().fetch("Buch")

    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["response_format"] == {"type": "json_object"}
    roles = {m["role"]: m["content"] for m in body["messages"]}
    assert roles["system"] == SYSTEM
    # The user turn is just the word; the language pair lives in the system prompt.
    assert "Word: Buch" in roles["user"]


def test_no_response_format_when_disabled(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    _provider(request_json_format=False).fetch("Buch")

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "response_format" not in body


def test_api_key_sends_bearer_header(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    _provider(api_key="sk-123").fetch("Buch")

    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer sk-123"


def test_no_auth_header_without_api_key(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    _provider().fetch("Buch")

    request = httpx_mock.get_requests()[0]
    assert "Authorization" not in request.headers


def test_auth_rejection_raises_provider_error(httpx_mock):
    # A wrong or missing key earns a 401 from the server (verified live against a
    # keyed llama-server); raise_for_status must turn that into a ProviderError.
    httpx_mock.add_response(url=CHAT_URL, status_code=401)

    with pytest.raises(ProviderError):
        _provider(api_key="wrong").fetch("Buch")


def test_http_error_raises_provider_error(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, status_code=500)

    with pytest.raises(ProviderError):
        _provider().fetch("Buch")


def test_fetch_retries_on_429(httpx_mock, monkeypatch):
    # A 429 is the server asking us to back off, not a refusal: the provider must
    # retry and succeed rather than surface a ProviderError. Patch sleep so the
    # backoff between attempts doesn't slow the test.
    monkeypatch.setattr("ankery.providers.retry.time.sleep", lambda _: None)
    httpx_mock.add_response(url=CHAT_URL, status_code=429)
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": "Buch"}'))

    info = _provider().fetch("Buch")

    assert info is not None
    assert info.word == "Buch"
    assert len(httpx_mock.get_requests()) == 2


def test_non_json_content_raises_provider_error(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion("not json at all"))

    with pytest.raises(ProviderError):
        _provider().fetch("Buch")


def test_unexpected_response_shape_raises_provider_error(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json={"unexpected": "shape"})

    with pytest.raises(ProviderError):
        _provider().fetch("Buch")


def test_invalid_wordinfo_raises_provider_error(httpx_mock):
    # Empty `word` fails WordInfo validation (min_length=1).
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"word": ""}'))

    with pytest.raises(ProviderError):
        _provider().fetch("Buch")


def test_malformed_output_raises_with_validation_detail(httpx_mock):
    # The motivating case: a small model returns `definitions` as a string
    # instead of a list. The rejection must surface as a ProviderError whose
    # message carries the validation detail (the CLI reports this to the user).
    bad = json.dumps({"word": "schnell", "definitions": "rasch, zügig"})
    httpx_mock.add_response(url=CHAT_URL, json=_completion(bad))

    with pytest.raises(ProviderError, match="definitions"):
        _provider().fetch("schnell")
