import json

import pytest

from ankery.notedef import Card, NoteDefinition
from ankery.sinks.ankiconnect import AnkiConnectSink
from ankery.sinks.base import SinkError, SyncResult

URL = "http://localhost:8765"


def _sink() -> AnkiConnectSink:
    return AnkiConnectSink(base_url=URL)


def _fields() -> dict[str, str]:
    return {"Front": "Buch", "Back": "book"}


def _note_def() -> NoteDefinition:
    return NoteDefinition(
        name="Ankery DE: Noun",
        field_map={"Word": "{{ word }}", "Article": "{{ gender }}"},
        applies_to="noun",
        cards=(Card("N1", "{{Article}} {{Word}}", "{{FrontSide}}"),),
        css=".card { color: blue; }",
    )


def _styleless_def() -> NoteDefinition:
    # A definition that sets no css of its own — the case where the sink supplies
    # a fallback (the catch-all model's styling, or the bundled default).
    return NoteDefinition(
        name="Ankery DE: Noun",
        field_map={"Word": "{{ word }}", "Article": "{{ gender }}"},
        applies_to="noun",
        cards=(Card("N1", "{{Article}} {{Word}}", "{{FrontSide}}"),),
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
    assert note["options"] == {
        "allowDuplicate": False,
        "duplicateScope": "deck",
        "duplicateScopeOptions": {
            "deckName": "German",
            "checkChildren": False,
            "checkAllModels": False,
        },
    }


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
    assert note["options"]["allowDuplicate"] is True
    assert note["options"]["duplicateScope"] == "deck"


def test_inband_error_raises_sink_error(httpx_mock):
    # AnkiConnect reports failures in the body with HTTP 200.
    httpx_mock.add_response(
        url=URL,
        json={"result": None, "error": "cannot create note because it is a duplicate"},
    )

    with pytest.raises(SinkError, match="duplicate"):
        _sink().add_note(deck="German", note_type="Basic", fields=_fields())


def test_sync_collection_sends_the_sync_action_with_its_own_timeout(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": None, "error": None})

    AnkiConnectSink(base_url=URL, timeout=5.0, sync_timeout=75.0).sync_collection()

    [request] = httpx_mock.get_requests()
    assert json.loads(request.content) == {"action": "sync", "version": 6, "params": {}}
    assert request.extensions["timeout"]["read"] == 75.0


def test_sync_collection_raises_on_inband_error(httpx_mock):
    httpx_mock.add_response(
        url=URL, json={"result": None, "error": "sync: auth not configured"}
    )

    with pytest.raises(SinkError, match="auth not configured"):
        _sink().sync_collection()


def test_other_actions_keep_the_request_timeout(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": 1, "error": None})

    AnkiConnectSink(base_url=URL, timeout=5.0, sync_timeout=75.0).add_note(
        deck="German", note_type="Basic", fields=_fields()
    )

    [request] = httpx_mock.get_requests()
    assert request.extensions["timeout"]["read"] == 5.0


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

    created = _sink().verify_note_types([_note_def()])

    assert created == ["Ankery DE: Noun"]
    requests = httpx_mock.get_requests()
    assert _actions(httpx_mock) == ["modelNames", "createModel"]
    params = json.loads(requests[1].content)["params"]
    assert params["modelName"] == "Ankery DE: Noun"
    assert params["inOrderFields"] == ["Word", "Article"]  # Anki field order
    assert params["isCloze"] is False
    assert params["css"] == ".card { color: blue; }"
    assert params["cardTemplates"] == [
        {"Name": "N1", "Front": "{{Article}} {{Word}}", "Back": "{{FrontSide}}"}
    ]


def test_verify_styles_created_model_from_catch_all(httpx_mock):
    # A definition with no css of its own: match the catch-all model's styling so
    # new cards look like the user's existing Basic ones.
    httpx_mock.add_response(url=URL, json={"result": ["Basic"], "error": None})  # modelNames
    httpx_mock.add_response(  # modelStyling
        url=URL, json={"result": {"css": ".card { color: green; }"}, "error": None}
    )
    httpx_mock.add_response(url=URL, json={"result": 1, "error": None})  # createModel

    _sink().verify_note_types([_styleless_def()], default_css=".card {}", catch_all="Basic")

    assert _actions(httpx_mock) == ["modelNames", "modelStyling", "createModel"]
    styling = json.loads(httpx_mock.get_requests()[1].content)["params"]
    assert styling["modelName"] == "Basic"
    create = json.loads(httpx_mock.get_requests()[2].content)["params"]
    assert create["css"] == ".card { color: green; }"


def test_verify_falls_back_to_default_css_when_catch_all_absent(httpx_mock):
    # The catch-all model isn't in Anki, so there is nothing to copy — no
    # modelStyling call, and the bundled default is used.
    httpx_mock.add_response(url=URL, json={"result": [], "error": None})  # modelNames
    httpx_mock.add_response(url=URL, json={"result": 1, "error": None})  # createModel

    _sink().verify_note_types([_styleless_def()], default_css=".card {}", catch_all="Basic")

    assert _actions(httpx_mock) == ["modelNames", "createModel"]  # no modelStyling
    create = json.loads(httpx_mock.get_requests()[1].content)["params"]
    assert create["css"] == ".card {}"


def test_verify_falls_back_to_default_css_when_styling_unreadable(httpx_mock):
    # The catch-all exists but its styling response is malformed — fall back to
    # the default rather than letting a bad shape through.
    httpx_mock.add_response(url=URL, json={"result": ["Basic"], "error": None})  # modelNames
    httpx_mock.add_response(url=URL, json={"result": {"nope": 1}, "error": None})  # modelStyling
    httpx_mock.add_response(url=URL, json={"result": 1, "error": None})  # createModel

    _sink().verify_note_types([_styleless_def()], default_css=".card {}", catch_all="Basic")

    create = json.loads(httpx_mock.get_requests()[2].content)["params"]
    assert create["css"] == ".card {}"


def test_verify_definition_css_overrides_catch_all(httpx_mock):
    # A definition with its own css keeps it; the catch-all is not even consulted.
    httpx_mock.add_response(url=URL, json={"result": [], "error": None})  # modelNames
    httpx_mock.add_response(url=URL, json={"result": 1, "error": None})  # createModel

    _sink().verify_note_types([_note_def()], default_css=".card {}", catch_all="Basic")

    assert _actions(httpx_mock) == ["modelNames", "createModel"]  # no modelStyling
    create = json.loads(httpx_mock.get_requests()[1].content)["params"]
    assert create["css"] == ".card { color: blue; }"


def test_verify_accepts_exact_match_without_creating(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": ["Ankery DE: Noun"], "error": None})
    httpx_mock.add_response(url=URL, json={"result": ["Word", "Article"], "error": None})

    created = _sink().verify_note_types([_note_def()])  # no raise

    assert created == []  # nothing was missing, nothing reported as created
    assert _actions(httpx_mock) == ["modelNames", "modelFieldNames"]  # no createModel


def test_verify_rejects_different_fields(httpx_mock):
    httpx_mock.add_response(url=URL, json={"result": ["Ankery DE: Noun"], "error": None})
    httpx_mock.add_response(url=URL, json={"result": ["Word", "Plural"], "error": None})

    with pytest.raises(SinkError, match="fields differ"):
        _sink().verify_note_types([_note_def()])


def test_verify_rejects_superset_model(httpx_mock):
    # Existing model has our fields plus an extra one — rejected, not accepted.
    httpx_mock.add_response(url=URL, json={"result": ["Ankery DE: Noun"], "error": None})
    httpx_mock.add_response(
        url=URL, json={"result": ["Word", "Article", "Notes"], "error": None}
    )

    with pytest.raises(SinkError, match="fields differ"):
        _sink().verify_note_types([_note_def()])


def test_verify_rejects_field_order_difference(httpx_mock):
    # Same field names, wrong order — the first field drives duplicate detection.
    httpx_mock.add_response(url=URL, json={"result": ["Ankery DE: Noun"], "error": None})
    httpx_mock.add_response(url=URL, json={"result": ["Article", "Word"], "error": None})

    with pytest.raises(SinkError, match="fields differ"):
        _sink().verify_note_types([_note_def()])


def test_verify_checks_every_model_before_creating_any(httpx_mock):
    absent = NoteDefinition(
        name="Ankery DE: Verb", field_map={"Infinitive": "{{ term }}"}, applies_to="verb"
    )
    httpx_mock.add_response(url=URL, json={"result": ["Ankery DE: Noun"], "error": None})
    httpx_mock.add_response(url=URL, json={"result": ["Word"], "error": None})

    with pytest.raises(SinkError, match="fields differ"):
        _sink().verify_note_types([absent, _note_def()])

    assert _actions(httpx_mock) == ["modelNames", "modelFieldNames"]


# ---------------------------------------------------------------------------
# sync_note_types
# ---------------------------------------------------------------------------

_LIVE_FIELDS = ["Word", "Article"]
_LIVE_TEMPLATES = {"N1": {"Front": "{{Article}} {{Word}}", "Back": "{{FrontSide}}"}}


def _respond(httpx_mock, result) -> None:
    httpx_mock.add_response(url=URL, json={"result": result, "error": None})


def _verb_def(**overrides) -> NoteDefinition:
    return NoteDefinition(
        name="Ankery DE: Verb",
        field_map={"Infinitive": "{{ term }}"},
        applies_to="verb",
        cards=(Card("V1", "{{Infinitive}}", "{{FrontSide}}"),),
        **overrides,
    )


def test_sync_in_sync_model_writes_nothing(httpx_mock):
    _respond(httpx_mock, ["Ankery DE: Noun"])  # modelNames
    _respond(httpx_mock, _LIVE_FIELDS)  # modelFieldNames
    _respond(httpx_mock, _LIVE_TEMPLATES)  # modelTemplates
    _respond(httpx_mock, {"css": ".card { color: blue; }"})  # modelStyling

    result = _sink().sync_note_types([_note_def()])

    assert result == SyncResult([], {})
    assert _actions(httpx_mock) == [
        "modelNames", "modelFieldNames", "modelTemplates", "modelStyling",
    ]


def test_sync_updates_changed_templates_only(httpx_mock):
    _respond(httpx_mock, ["Ankery DE: Noun"])  # modelNames
    _respond(httpx_mock, _LIVE_FIELDS)  # modelFieldNames
    _respond(httpx_mock, {"N1": {"Front": "old", "Back": "{{FrontSide}}"}})  # modelTemplates
    _respond(httpx_mock, {"css": ".card { color: blue; }"})  # modelStyling
    _respond(httpx_mock, None)  # updateModelTemplates

    result = _sink().sync_note_types([_note_def()])

    assert result == SyncResult([], {"Ankery DE: Noun": ["templates"]})
    assert _actions(httpx_mock)[-1] == "updateModelTemplates"
    params = json.loads(httpx_mock.get_requests()[-1].content)["params"]
    assert params == {"model": {"name": "Ankery DE: Noun", "templates": _LIVE_TEMPLATES}}


def test_sync_updates_changed_styling_only(httpx_mock):
    _respond(httpx_mock, ["Ankery DE: Noun"])  # modelNames
    _respond(httpx_mock, _LIVE_FIELDS)  # modelFieldNames
    _respond(httpx_mock, _LIVE_TEMPLATES)  # modelTemplates
    _respond(httpx_mock, {"css": ".card { color: red; }"})  # modelStyling
    _respond(httpx_mock, None)  # updateModelStyling

    result = _sink().sync_note_types([_note_def()])

    assert result == SyncResult([], {"Ankery DE: Noun": ["styling"]})
    assert _actions(httpx_mock)[-1] == "updateModelStyling"
    params = json.loads(httpx_mock.get_requests()[-1].content)["params"]
    assert params == {"model": {"name": "Ankery DE: Noun", "css": ".card { color: blue; }"}}


def test_sync_styleless_definition_uses_default_css(httpx_mock):
    _respond(httpx_mock, ["Ankery DE: Noun"])  # modelNames
    _respond(httpx_mock, _LIVE_FIELDS)  # modelFieldNames
    _respond(httpx_mock, _LIVE_TEMPLATES)  # modelTemplates
    _respond(httpx_mock, {"css": ".old {}"})  # modelStyling
    _respond(httpx_mock, None)  # updateModelStyling

    _sink().sync_note_types([_styleless_def()], default_css=".pack {}")

    params = json.loads(httpx_mock.get_requests()[-1].content)["params"]
    assert params["model"]["css"] == ".pack {}"


def test_sync_creates_missing_models_with_default_css(httpx_mock):
    _respond(httpx_mock, [])  # modelNames
    _respond(httpx_mock, 1)  # createModel (noun)
    _respond(httpx_mock, 2)  # createModel (verb)

    result = _sink().sync_note_types([_note_def(), _verb_def()], default_css=".pack {}")

    assert result == SyncResult(["Ankery DE: Noun", "Ankery DE: Verb"], {})
    assert _actions(httpx_mock) == ["modelNames", "createModel", "createModel"]
    noun, verb = (json.loads(r.content)["params"] for r in httpx_mock.get_requests()[1:])
    assert noun["css"] == ".card { color: blue; }"
    assert verb["css"] == ".pack {}"


def test_sync_creates_missing_before_updating_existing(httpx_mock):
    _respond(httpx_mock, ["Ankery DE: Noun"])  # modelNames
    _respond(httpx_mock, _LIVE_FIELDS)  # modelFieldNames (noun)
    _respond(httpx_mock, {"N1": {"Front": "old", "Back": "old"}})  # modelTemplates (noun)
    _respond(httpx_mock, {"css": ".card { color: blue; }"})  # modelStyling (noun)
    _respond(httpx_mock, 1)  # createModel (verb)
    _respond(httpx_mock, None)  # updateModelTemplates (noun)

    result = _sink().sync_note_types([_note_def(), _verb_def()])

    assert result == SyncResult(["Ankery DE: Verb"], {"Ankery DE: Noun": ["templates"]})
    assert _actions(httpx_mock)[-2:] == ["createModel", "updateModelTemplates"]


@pytest.mark.parametrize(
    ("responses", "match"),
    [
        ([["Word"]], "fields differ"),
        ([_LIVE_FIELDS, {"N1": {}, "N2": {}}], "card types differ"),
    ],
)
def test_sync_rejects_mismatch_before_writing_anything(httpx_mock, responses, match):
    _respond(httpx_mock, ["Ankery DE: Noun"])  # modelNames; the verb model is absent
    for result in responses:  # modelFieldNames[, modelTemplates] (noun)
        _respond(httpx_mock, result)

    with pytest.raises(SinkError, match=match):
        _sink().sync_note_types([_verb_def(), _note_def()])

    assert not any(
        a.startswith(("update", "create")) for a in _actions(httpx_mock)
    )


def test_find_notes_queries_one_deck_model_and_field(httpx_mock):
    _respond(httpx_mock, [7])  # findNotes
    _respond(httpx_mock, [
        {"noteId": 7, "fields": {"Word": {"value": "Junge", "order": 0}}},
    ])  # notesInfo

    notes = _sink().find_notes(
        deck="Deutsch", note_type="Ankery DE: Noun", field="Plural", value="Jungen/Jungs"
    )

    assert notes == {7: {"Word": "Junge"}}
    query = json.loads(httpx_mock.get_requests()[0].content)["params"]["query"]
    assert query == (
        '"deck:Deutsch" -"deck:Deutsch::*" "note:Ankery DE: Noun" "Plural:Jungen/Jungs"'
    )


def test_find_notes_escapes_anki_search_syntax(httpx_mock):
    _respond(httpx_mock, [])

    _sink().find_notes(deck="my_deck", note_type="N", field="F", value='a*b "c" \\d')

    query = json.loads(httpx_mock.get_requests()[0].content)["params"]["query"]
    assert query == (
        '"deck:my\\_deck" -"deck:my\\_deck::*" "note:N" "F:a\\*b \\"c\\" \\\\d"'
    )


def test_find_notes_without_matches_skips_notes_info(httpx_mock):
    _respond(httpx_mock, [])

    assert _sink().find_notes(deck="D", note_type="N", field="F", value="v") == {}
    assert _actions(httpx_mock) == ["findNotes"]


def test_find_notes_rejects_malformed_notes_info(httpx_mock):
    _respond(httpx_mock, [7])
    _respond(httpx_mock, [{"noteId": 7}])

    with pytest.raises(SinkError, match="notesInfo"):
        _sink().find_notes(deck="D", note_type="N", field="F", value="v")
