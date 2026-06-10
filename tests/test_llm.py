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
    kwargs.setdefault("user_prompt_for", lambda term: f"Term: {term}")
    kwargs.setdefault("pack", "de")
    kwargs.setdefault("variables", {"target_language": "en"})
    # The pack's category label is the JSON key the model fills; the provider
    # maps it onto Entry.category. The German pack labels it "part of speech".
    kwargs.setdefault("category_key", "part of speech")
    return LLMProvider(base_url=BASE_URL, model="test-model", **kwargs)


def test_fetch_returns_entry(httpx_mock):
    entry_json = json.dumps(
        {
            "term": "Buch",
            "part of speech": "noun",
            "collections": {"definitions": ["gebundene Seiten zum Lesen"]},
            "properties": {"gender": "das", "genitive_sg": "Buches", "nominative_pl": "Bücher"},
        }
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(entry_json))

    entry = _provider().fetch("Buch")

    assert entry is not None
    assert entry.term == "Buch"
    # The model filled the pack's label key; the provider mapped it onto `category`.
    assert entry.category == "noun"
    # The nested collections object is taken verbatim.
    assert entry.collections["definitions"] == ["gebundene Seiten zum Lesen"]
    assert entry.properties["gender"] == "das"
    assert entry.properties["nominative_pl"] == "Bücher"


def test_hinted_fetch_misses_on_empty_object(httpx_mock):
    # The user asserted a category the term does not have; the model returns the
    # empty-object miss signal. The provider must read that as a clean miss
    # (None) so the chain moves on, not fabricate a card for the wrong term.
    httpx_mock.add_response(url=CHAT_URL, json=_completion("{}"))

    assert _provider().fetch("laufen", category_hint="noun") is None


def test_empty_object_without_hint_raises(httpx_mock):
    # The miss signal is only honoured under a hint. Without one, a term-less
    # object is just a malformed response and must fail loudly.
    httpx_mock.add_response(url=CHAT_URL, json=_completion("{}"))

    with pytest.raises(ProviderError):
        _provider().fetch("laufen")


def test_fetch_renders_system_prompt_with_the_category_hint(httpx_mock):
    # The hint reaches the renderer, not just the user prompt — so a hint can
    # trim the system prompt to the named category.
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"term": "schnell"}'))

    provider = _provider(
        system_prompt_for=lambda category_hint=None: f"hint={category_hint}"
    )
    provider.fetch("schnell", category_hint="adjective")

    [request] = httpx_mock.get_requests()
    system_message = json.loads(request.content)["messages"][0]["content"]
    assert system_message == "hint=adjective"


def test_fetch_does_not_normalize_forms(httpx_mock):
    # Bare-form normalization is the pack's filter, applied by the manager — not
    # the provider. The provider returns properties verbatim.
    entry_json = json.dumps(
        {"term": "Haus", "properties": {"genitive_sg": "des Hauses"}}
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(entry_json))

    entry = _provider().fetch("Haus")

    assert entry.properties["genitive_sg"] == "des Hauses"


def test_fetch_sets_provenance_and_variables(httpx_mock):
    # The model echoes back bogus provenance values; the provider must overwrite
    # them with what it controls (its construction-time pack and variables).
    entry_json = json.dumps(
        {
            "term": "Buch",
            "source": "hallucinated",
            "pack": "xx",
            "variables": {"target_language": "yy"},
        }
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(entry_json))

    entry = _provider().fetch("Buch")

    assert entry.source == "llm"
    assert entry.pack == "de"
    assert entry.variables == {"target_language": "en"}


def test_fetch_strips_code_fences(httpx_mock):
    fenced = '```json\n{"term": "Buch"}\n```'
    httpx_mock.add_response(url=CHAT_URL, json=_completion(fenced))

    entry = _provider().fetch("Buch")

    assert entry.term == "Buch"


def test_request_payload_carries_prompt_and_model(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"term": "Buch"}'))

    _provider().fetch("Buch")

    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["response_format"] == {"type": "json_object"}
    roles = {m["role"]: m["content"] for m in body["messages"]}
    assert roles["system"] == SYSTEM
    # The user turn is just the term; the language pair lives in the system prompt.
    assert "Term: Buch" in roles["user"]


def test_no_response_format_when_disabled(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"term": "Buch"}'))

    _provider(request_json_format=False).fetch("Buch")

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "response_format" not in body


def test_api_key_sends_bearer_header(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"term": "Buch"}'))

    _provider(api_key="sk-123").fetch("Buch")

    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer sk-123"


def test_no_auth_header_without_api_key(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"term": "Buch"}'))

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
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"term": "Buch"}'))

    entry = _provider().fetch("Buch")

    assert entry is not None
    assert entry.term == "Buch"
    assert len(httpx_mock.get_requests()) == 2


def test_non_json_content_raises_provider_error(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json=_completion("not json at all"))

    with pytest.raises(ProviderError):
        _provider().fetch("Buch")


def test_unexpected_response_shape_raises_provider_error(httpx_mock):
    httpx_mock.add_response(url=CHAT_URL, json={"unexpected": "shape"})

    with pytest.raises(ProviderError):
        _provider().fetch("Buch")


def test_invalid_entry_raises_provider_error(httpx_mock):
    # Empty `term` fails Entry validation (min_length=1).
    httpx_mock.add_response(url=CHAT_URL, json=_completion('{"term": ""}'))

    with pytest.raises(ProviderError):
        _provider().fetch("Buch")


def test_malformed_output_raises_with_validation_detail(httpx_mock):
    # A model returns `collections` as a bare array instead of the keyed object the
    # schema requires (a string section value would be coerced, not rejected — so
    # this exercises the outer-shape mismatch). The rejection must surface as a
    # ProviderError whose message carries the validation detail.
    bad = json.dumps({"term": "schnell", "collections": ["rasch", "zügig"]})
    httpx_mock.add_response(url=CHAT_URL, json=_completion(bad))

    with pytest.raises(ProviderError, match="collections"):
        _provider().fetch("schnell")


def test_unknown_top_level_keys_are_dropped_but_logged(httpx_mock, caplog):
    # Validation silently ignores keys outside the Entry schema (e.g. a section
    # the model failed to nest); the DEBUG log is their only trace.
    stray = json.dumps(
        {"term": "Buch", "examples": ["Das Buch ist gut."], "collections.definitions": ["x"]}
    )
    httpx_mock.add_response(url=CHAT_URL, json=_completion(stray))

    with caplog.at_level("DEBUG", logger="ankery.providers.llm"):
        entry = _provider().fetch("Buch")

    assert entry is not None
    assert entry.collections == {}
    [record] = [r for r in caplog.records if "unknown top-level keys" in r.message]
    assert "examples" in record.getMessage()
    assert "collections.definitions" in record.getMessage()
