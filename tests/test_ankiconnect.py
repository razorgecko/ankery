import json

import pytest

from ankery.notedef import Card, NoteDefinition
from ankery.sinks.ankiconnect import AnkiConnectSink
from ankery.sinks.base import SinkError

URL = "http://localhost:8765"


def _sink() -> AnkiConnectSink:
    return AnkiConnectSink(base_url=URL)


def _fields() -> dict[str, str]:
    return {"Front": "Buch", "Back": "book"}


def _note_def() -> NoteDefinition:
    return NoteDefinition(
        name="Noun (DE)",
        field_map={"Word": "{{ word }}", "Article": "{{ gender }}"},
        applies_to="noun",
        cards=(Card("N1", "{{Article}} {{Word}}", "{{FrontSide}}"),),
        css=".card { color: blue; }",
    )


def _actions(httpx_mock) -> list[str]:
    return [json.loads(r.content)["action"] for r in httpx_mock.get_requests()]


def test_add_note_returns_note_id(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": 1496198395707, "error": None})

    note_id = _sink().add_note(deck="German", note_type="Basic", fields=_fields())

    assert note_id == 1496198395707


def test_add_note_builds_jsonrpc_payload(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": 1, "error": None})

    _sink().add_note(
        deck="German",
        note_type="Basic",
        fields=_fields(),
        tags=["auto", "de"],
    )

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["action"] == "addNote"
    assert body["version"] == 6
    note = body["params"]["note"]
    assert note["deckName"] == "German"
    assert note["modelName"] == "Basic"
    assert note["fields"] == _fields()
    assert note["tags"] == ["auto", "de"]
    assert note["options"] == {"allowDuplicate": False}


def test_tags_default_to_empty_list(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": 1, "error": None})

    _sink().add_note(deck="German", note_type="Basic", fields=_fields())

    note = json.loads(httpx_mock.get_requests()[0].content)["params"]["note"]
    assert note["tags"] == []


def test_allow_duplicate_flag_propagates(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": 1, "error": None})

    AnkiConnectSink(base_url=URL, allow_duplicate=True).add_note(
        deck="German", note_type="Basic", fields=_fields()
    )

    note = json.loads(httpx_mock.get_requests()[0].content)["params"]["note"]
    assert note["options"] == {"allowDuplicate": True}


def test_inband_error_raises_sink_error(httpx_mock):
    # AnkiConnect reports failures in the body with HTTP 200.
    httpx_mock.add_response(
        url=URL,
        json={"result": None, "error": "cannot create note because it is a duplicate"},
    )

    with pytest.raises(SinkError, match="duplicate"):
        _sink().add_note(deck="German", note_type="Basic", fields=_fields())


def test_http_error_raises_sink_error(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=500)

    with pytest.raises(SinkError):
        _sink().add_note(deck="German", note_type="Basic", fields=_fields())


def test_unexpected_response_shape_raises_sink_error(httpx_mock):
    # Missing the mandatory result/error keys.
    httpx_mock.add_response(url=URL, json={"unexpected": "shape"})

    with pytest.raises(SinkError):
        _sink().add_note(deck="German", note_type="Basic", fields=_fields())


def test_non_int_result_raises_sink_error(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": "not-an-id", "error": None})

    with pytest.raises(SinkError):
        _sink().add_note(deck="German", note_type="Basic", fields=_fields())


def test_verify_creates_missing_model(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": [], "error": None})  # modelNames
    httpx_mock.add_response(url=URL, json={"result": 12345, "error": None})  # createModel

    _sink().verify_note_types([_note_def()])

    requests = httpx_mock.get_requests()
    assert _actions(httpx_mock) == ["modelNames", "createModel"]
    params = json.loads(requests[1].content)["params"]
    assert params["modelName"] == "Noun (DE)"
    assert params["inOrderFields"] == ["Word", "Article"]  # Anki field order
    assert params["isCloze"] is False
    assert params["css"] == ".card { color: blue; }"
    assert params["cardTemplates"] == [
        {"Name": "N1", "Front": "{{Article}} {{Word}}", "Back": "{{FrontSide}}"}
    ]


def test_verify_accepts_exact_match_without_creating(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": ["Noun (DE)"], "error": None})
    httpx_mock.add_response(url=URL, json={"result": ["Word", "Article"], "error": None})

    _sink().verify_note_types([_note_def()])  # no raise

    assert _actions(httpx_mock) == ["modelNames", "modelFieldNames"]  # no createModel


def test_verify_rejects_different_fields(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": ["Noun (DE)"], "error": None})
    httpx_mock.add_response(url=URL, json={"result": ["Word", "Plural"], "error": None})

    with pytest.raises(SinkError, match="different fields"):
        _sink().verify_note_types([_note_def()])


def test_verify_rejects_superset_model(httpx_mock):
    # Existing model has our fields plus an extra one — rejected, not accepted.
    httpx_mock.add_response(url=URL, json={"result": ["Noun (DE)"], "error": None})
    httpx_mock.add_response(
        url=URL, json={"result": ["Word", "Article", "Notes"], "error": None}
    )

    with pytest.raises(SinkError, match="different fields"):
        _sink().verify_note_types([_note_def()])


def test_verify_rejects_field_order_difference(httpx_mock):
    # Same field names, wrong order — the first field drives duplicate detection.
    httpx_mock.add_response(url=URL, json={"result": ["Noun (DE)"], "error": None})
    httpx_mock.add_response(url=URL, json={"result": ["Article", "Word"], "error": None})

    with pytest.raises(SinkError, match="different fields"):
        _sink().verify_note_types([_note_def()])
