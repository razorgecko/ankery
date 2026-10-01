import base64
import json
import logging
import os
import stat
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from ankery import signin
from ankery.config import Config, ConfigError, _token_source
from ankery.providers.base import ProviderError
from ankery.signin import (
    REDIRECT_URI,
    REGISTRATION_CLIENT_ID,
    RESOURCE,
    SCOPES,
    TOKEN_URL,
    Authorization,
    CallbackServer,
    SignInError,
    StoredTokens,
    TokenStore,
)

ISSUED = "oaiapp_123"
NOW = 1_790_000_000.0


@pytest.fixture(autouse=True)
def _isolate_state_dir(monkeypatch, tmp_path):
    # Keep tests away from the developer's real ~/.local/state/ankery/tokens.json.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "_state"))


def _jwt(claims: dict) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.sig"


def _claims(auth: Authorization, **overrides) -> dict:
    return {
        "iss": "https://auth.openai.com",
        "aud": ISSUED,
        "sub": "user-1",
        "email": "a@example.com",
        "nonce": auth.nonce,
        "exp": NOW + 3600,
        **overrides,
    }


def _token_response(auth: Authorization | None = None, **overrides) -> dict:
    body = {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "scope": "openid email chatgpt.tokens.use.direct",
        "token_type": "Bearer",
    }
    if auth is not None:
        body["id_token"] = _jwt(_claims(auth))
    return {**body, **overrides}


def _redirect(auth: Authorization, **params) -> str:
    query = {"code": "the-code", "state": auth.state, "client_id": ISSUED, **params}
    query = {k: v for k, v in query.items() if v is not None}
    return f"{REDIRECT_URI}?{urlencode(query)}"


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _record(**overrides) -> dict:
    return {
        "email": "a@example.com",
        "issuer": "https://auth.openai.com",
        "subject": "user-1",
        "client_id": ISSUED,
        "ext_agent_host_id": "urn:uuid:host",
        "id_token": "id-1",
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "scopes": ["openid"],
        "saved_at": datetime.fromtimestamp(NOW, UTC).isoformat(),
        **overrides,
    }


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_code_challenge_matches_the_rfc_7636_example():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

    assert signin.code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_first_sign_in_registers_with_the_dynamic_client():
    auth = signin.begin(None)
    query = _query(auth.url)

    assert auth.url.startswith("https://auth.openai.com/api/accounts/authorize?")
    assert query["client_id"] == REGISTRATION_CLIENT_ID
    assert query["agent_name_hint"] == "ankery"
    assert query["ext_agent_host_id"].startswith("urn:uuid:")
    assert query["ext_agent_host_id"] == auth.ext_agent_host_id
    assert "id_token_hint" not in query
    assert "login_hint" not in query
    assert auth.browser_url == auth.url
    assert query["response_type"] == "code"
    assert query["redirect_uri"] == "http://127.0.0.1:1455/auth/callback"
    assert query["scope"] == SCOPES
    assert query["resource"] == RESOURCE
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] == signin.code_challenge(auth.verifier)
    assert query["state"] == auth.state
    assert query["nonce"] == auth.nonce


def test_repeat_sign_in_reuses_the_issued_client_and_hints_the_account():
    auth = signin.begin(_record())
    query = _query(auth.url)

    assert query["client_id"] == ISSUED
    assert auth.client_id == ISSUED
    assert query["ext_agent_host_id"] == "urn:uuid:host"
    assert query["login_hint"] == "a@example.com"
    assert "agent_name_hint" not in query
    assert _query(auth.browser_url) == {**query, "id_token_hint": "id-1"}


def test_printed_url_never_carries_the_id_token():
    auth = signin.begin(_record(id_token="id-secret"))

    assert "id-secret" not in auth.url
    assert "id_token_hint" not in _query(auth.url)


def test_sign_in_after_logout_keeps_the_registration_without_a_hint():
    auth = signin.begin(signin.signed_out(_record()))
    query = _query(auth.url)

    assert query["client_id"] == ISSUED
    assert query["ext_agent_host_id"] == "urn:uuid:host"
    assert "id_token_hint" not in query
    assert "login_hint" not in query
    assert auth.browser_url == auth.url


def test_each_sign_in_has_fresh_secrets():
    first, second = signin.begin(None), signin.begin(None)

    assert first.verifier != second.verifier
    assert first.state != second.state
    assert first.nonce != second.nonce


def test_pasted_redirect_yields_code_and_issued_client():
    auth = signin.begin(None)

    assert signin.parse_callback(auth, _redirect(auth)) == ("the-code", ISSUED)


def test_redirect_without_client_id_keeps_the_issued_one():
    auth = signin.begin(_record())

    assert signin.parse_callback(auth, _redirect(auth, client_id=None)) == ("the-code", ISSUED)


def test_registration_redirect_without_client_id_raises():
    auth = signin.begin(None)

    with pytest.raises(SignInError, match="did not issue a client ID"):
        signin.parse_callback(auth, _redirect(auth, client_id=None))


@pytest.mark.parametrize("state", ["forged", "\u00e9", None], ids=["other", "non-ascii", "missing"])
def test_pasted_redirect_with_another_state_is_rejected(state):
    auth = signin.begin(None)

    with pytest.raises(SignInError, match="state mismatch"):
        signin.parse_callback(auth, _redirect(auth, state=state))


def test_pasted_redirect_without_a_code_is_rejected():
    auth = signin.begin(None)

    with pytest.raises(SignInError, match="no authorization code"):
        signin.parse_callback(auth, _redirect(auth, code=None))


def test_pasted_redirect_with_an_error_is_rejected():
    auth = signin.begin(None)
    url = _redirect(auth, code=None, error="access_denied", error_description="User declined")

    with pytest.raises(SignInError, match="sign-in refused: access_denied: User declined"):
        signin.parse_callback(auth, url)


def test_pasted_authorize_url_is_not_a_redirect():
    auth = signin.begin(None)

    with pytest.raises(SignInError, match="not the redirect URL"):
        signin.parse_callback(auth, auth.url)


def test_rejected_redirect_message_does_not_quote_the_code():
    auth = signin.begin(None)

    with pytest.raises(SignInError) as exc:
        signin.parse_callback(auth, _redirect(auth, state="forged", code="secret-code"))
    assert "secret-code" not in str(exc.value)


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


def test_exchange_posts_the_authorization_code_form(httpx_mock):
    auth = signin.begin(None)
    httpx_mock.add_response(url=TOKEN_URL, json=_token_response(auth))

    signin.complete(auth, _redirect(auth), clock=lambda: NOW)

    request = httpx_mock.get_request()
    assert request.method == "POST"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert _form(request) == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "client_id": ISSUED,
        "code_verifier": auth.verifier,
        "redirect_uri": REDIRECT_URI,
        "resource": RESOURCE,
    }


def test_exchange_returns_the_record_to_store(httpx_mock):
    auth = signin.begin(None)
    httpx_mock.add_response(url=TOKEN_URL, json=_token_response(auth))

    record = signin.complete(auth, _redirect(auth), clock=lambda: NOW)

    assert record == {
        "email": "a@example.com",
        "issuer": "https://auth.openai.com",
        "subject": "user-1",
        "client_id": ISSUED,
        "ext_agent_host_id": auth.ext_agent_host_id,
        "id_token": _token_response(auth)["id_token"],
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "scopes": ["openid", "email", "chatgpt.tokens.use.direct"],
        "saved_at": datetime.fromtimestamp(NOW, UTC).isoformat(),
    }


def test_rejected_redirect_makes_no_token_request(httpx_mock):
    auth = signin.begin(None)

    with pytest.raises(SignInError, match="state mismatch"):
        signin.complete(auth, _redirect(auth, state="forged"))
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ({"nonce": "other"}, "nonce mismatch"),
        ({"iss": "https://evil.example"}, "issuer"),
        ({"aud": "oaiapp_other"}, "not issued to this client"),
        ({"exp": NOW - 3600}, "expired"),
        ({"sub": ""}, "names no account"),
    ],
)
def test_exchange_rejects_a_bad_id_token(httpx_mock, claims, message):
    auth = signin.begin(None)
    body = _token_response(auth, id_token=_jwt(_claims(auth, **claims)))
    httpx_mock.add_response(url=TOKEN_URL, json=body)

    with pytest.raises(SignInError, match=message):
        signin.complete(auth, _redirect(auth), clock=lambda: NOW)


def test_exchange_accepts_an_audience_list(httpx_mock):
    auth = signin.begin(None)
    body = _token_response(auth, id_token=_jwt(_claims(auth, aud=[ISSUED, "other"])))
    httpx_mock.add_response(url=TOKEN_URL, json=body)

    assert signin.complete(auth, _redirect(auth), clock=lambda: NOW)["subject"] == "user-1"


def test_exchange_error_carries_the_oauth_error(httpx_mock):
    auth = signin.begin(None)
    httpx_mock.add_response(
        url=TOKEN_URL,
        status_code=400,
        json={"error": "invalid_grant", "error_description": "code expired"},
    )

    with pytest.raises(SignInError, match="HTTP 400: invalid_grant: code expired"):
        signin.complete(auth, _redirect(auth))


def test_token_requests_log_no_secrets(httpx_mock, caplog):
    auth = signin.begin(None)
    httpx_mock.add_response(url=TOKEN_URL, json=_token_response(auth))
    httpx_mock.add_response(url=TOKEN_URL, json=_token_response(access_token="access-2"))

    with caplog.at_level(logging.DEBUG, logger="ankery"):
        record = signin.complete(auth, _redirect(auth), clock=lambda: NOW)
        signin.refresh(record)

    for secret in ("the-code", auth.verifier, "access-1", "access-2", "refresh-1", record["id_token"]):
        assert secret not in caplog.text


# ---------------------------------------------------------------------------
# Loopback callback
# ---------------------------------------------------------------------------


def _get(port: int, path: str) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return response.status


def _serve(path: str) -> tuple[str, list]:
    """Send `path` to a CallbackServer for state `s` on a free port; return what it captured."""
    with CallbackServer("s", port=0) as server:
        port = server._server.server_address[1]
        statuses: list = []
        thread = threading.Thread(target=lambda: statuses.append(_get(port, path)))
        thread.start()
        url = server.wait(5)
        thread.join()
    return url, statuses


def test_loopback_captures_the_redirect():
    url, statuses = _serve("/auth/callback?code=c&state=s")

    assert url == "http://127.0.0.1:1455/auth/callback?code=c&state=s"
    assert statuses == [200]


@pytest.mark.parametrize(
    "query", ["code=c&state=forged", "code=c&state=%C3%A9", "code=c"],
    ids=["other", "non-ascii", "missing"],
)
def test_loopback_redirect_with_another_state_is_refused_and_the_wait_goes_on(query):
    with CallbackServer("s", port=0) as server:
        port = server._server.server_address[1]
        refused: list = []

        def requests():
            with pytest.raises(urllib.error.HTTPError) as exc:
                _get(port, f"/auth/callback?{query}")
            refused.append(exc.value.code)
            _get(port, "/auth/callback?code=c&state=s")

        thread = threading.Thread(target=requests)
        thread.start()
        url = server.wait(5)
        thread.join()

    assert refused == [400]
    assert url.endswith("/auth/callback?code=c&state=s")


def test_loopback_ignores_other_paths():
    with CallbackServer("s", port=0) as server:
        port = server._server.server_address[1]

        def requests():
            with pytest.raises(urllib.error.HTTPError):
                _get(port, "/favicon.ico")
            _get(port, "/auth/callback?code=c&state=s")

        thread = threading.Thread(target=requests)
        thread.start()
        url = server.wait(5)
        thread.join()

    assert url.endswith("/auth/callback?code=c&state=s")


def test_loopback_times_out():
    with CallbackServer("s", port=0) as server, pytest.raises(SignInError, match="timed out"):
        server.wait(0.1)


def test_busy_port_raises_os_error():
    with CallbackServer("s", port=0) as server:
        port = server._server.server_address[1]
        with pytest.raises(OSError):
            CallbackServer("s", port=port)


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("linux", {}, False),
        ("linux", {"DISPLAY": ":0"}, True),
        ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, True),
        ("freebsd14", {}, False),
        ("darwin", {}, True),
        ("win32", {}, True),
    ],
)
def test_browser_needs_a_display_on_linux(platform, env, expected):
    assert signin.can_open_browser(env, platform) is expected


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_tokens_path_honors_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert signin.tokens_path() == tmp_path / "state" / "ankery" / "tokens.json"


def test_tokens_path_falls_back_to_local_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".local" / "state" / "ankery" / "tokens.json"
    for value in (None, "", "relative/path"):
        if value is None:
            monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        else:
            monkeypatch.setenv("XDG_STATE_HOME", value)
        assert signin.tokens_path() == expected


def test_store_round_trips_and_writes_mode_0600(tmp_path):
    store = TokenStore(tmp_path / "state" / "ankery" / "tokens.json")

    store.save(_record())

    assert store.load() == _record()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_store_missing_file_loads_none(tmp_path):
    assert TokenStore(tmp_path / "tokens.json").load() is None


def test_store_write_is_atomic(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "tokens.json")
    store.save(_record())

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(SignInError, match="could not write"):
        store.save(_record(access_token="access-2"))

    assert store.load() == _record()
    assert [p.name for p in tmp_path.iterdir()] == ["tokens.json"]


def test_store_replaces_through_a_temp_file_in_the_same_directory(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "tokens.json")
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src"], seen["dst"] = src, dst
        seen["mode"] = stat.S_IMODE(os.stat(src).st_mode)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    store.save(_record())

    assert os.path.dirname(seen["src"]) == str(tmp_path)
    assert seen["dst"] == store.path
    assert seen["mode"] == 0o600


def test_store_rejects_invalid_json(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text("{not json")

    with pytest.raises(SignInError, match="not valid JSON; delete it"):
        TokenStore(path).load()


def test_signed_out_keeps_only_the_registration():
    assert signin.signed_out(_record()) == {
        "email": "a@example.com",
        "issuer": "https://auth.openai.com",
        "subject": "user-1",
        "client_id": ISSUED,
        "ext_agent_host_id": "urn:uuid:host",
    }
    assert not signin.is_signed_in(signin.signed_out(_record()))


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def _tokens(tmp_path, record, *, now):
    store = TokenStore(tmp_path / "tokens.json")
    store.save(record)
    return store, StoredTokens(store, clock=lambda: now)


def test_fresh_token_is_returned_without_a_request(tmp_path, httpx_mock):
    _, tokens = _tokens(tmp_path, _record(), now=NOW + 60)

    assert tokens.access_token() == "access-1"
    assert httpx_mock.get_requests() == []


def test_token_near_expiry_is_refreshed_rotated_and_saved(tmp_path, httpx_mock):
    later = NOW + 3600 - 120
    store, tokens = _tokens(tmp_path, _record(), now=later)
    httpx_mock.add_response(
        url=TOKEN_URL,
        json=_token_response(access_token="access-2", refresh_token="refresh-2"),
    )

    assert tokens.access_token() == "access-2"

    assert _form(httpx_mock.get_request()) == {
        "grant_type": "refresh_token",
        "client_id": ISSUED,
        "refresh_token": "refresh-1",
        "resource": RESOURCE,
    }
    saved = store.load()
    assert saved["access_token"] == "access-2"
    assert saved["refresh_token"] == "refresh-2"
    assert saved["saved_at"] == datetime.fromtimestamp(later, UTC).isoformat()
    assert saved["client_id"] == ISSUED
    assert saved["ext_agent_host_id"] == "urn:uuid:host"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_refreshed_token_is_reused_until_near_expiry(tmp_path, httpx_mock):
    now = [NOW + 3600]
    store = TokenStore(tmp_path / "tokens.json")
    store.save(_record())
    tokens = StoredTokens(store, clock=lambda: now[0])
    httpx_mock.add_response(url=TOKEN_URL, json=_token_response(access_token="access-2"))

    assert tokens.access_token() == "access-2"
    now[0] += 60
    assert tokens.access_token() == "access-2"
    assert len(httpx_mock.get_requests()) == 1


def test_refresh_without_a_new_refresh_token_keeps_the_current_one(tmp_path, httpx_mock):
    store, tokens = _tokens(tmp_path, _record(), now=NOW + 3600)
    body = _token_response(access_token="access-2")
    del body["refresh_token"]
    httpx_mock.add_response(url=TOKEN_URL, json=body)

    tokens.access_token()

    assert store.load()["refresh_token"] == "refresh-1"


@pytest.mark.parametrize(
    "body",
    [
        {"error": "invalid_grant"},
        {"error": "refresh_token_reused"},
        {"error": {"code": "refresh_token_expired", "message": "expired"}},
    ],
    ids=["oauth", "reused", "api-shape"],
)
def test_unusable_refresh_token_is_cleared_and_asks_for_a_new_sign_in(tmp_path, httpx_mock, body):
    store, tokens = _tokens(tmp_path, _record(), now=NOW + 3600)
    httpx_mock.add_response(url=TOKEN_URL, status_code=401, json=body)

    with pytest.raises(ProviderError, match="expired or was revoked.*run `ankery login`"):
        tokens.access_token()
    assert store.load() == signin.signed_out(_record())


@pytest.mark.parametrize(
    ("status", "body"),
    [(429, {"error": "rate_limited"}), (400, {"error": "invalid_request"}), (408, None)],
    ids=["429", "other-oauth-error", "no-body"],
)
def test_other_refresh_rejection_keeps_the_tokens(tmp_path, httpx_mock, status, body):
    store, tokens = _tokens(tmp_path, _record(), now=NOW + 3600)
    httpx_mock.add_response(url=TOKEN_URL, status_code=status, json=body)

    with pytest.raises(ProviderError, match="could not refresh the ChatGPT sign-in") as exc:
        tokens.access_token()
    assert "ankery login" not in str(exc.value)
    assert store.load() == _record()


def test_refresh_waits_for_the_store_lock_and_uses_what_the_holder_saved(tmp_path, httpx_mock):
    # Two StoredTokens over one file stand in for two processes: the lock is
    # per open file, so they contend as separate processes would.
    store, tokens = _tokens(tmp_path, _record(), now=NOW + 3600)
    rotated = _record(access_token="access-2", refresh_token="refresh-2",
                      saved_at=datetime.fromtimestamp(NOW + 3600, UTC).isoformat())
    result: list = []

    with TokenStore(store.path).lock():
        thread = threading.Thread(target=lambda: result.append(tokens.access_token()))
        thread.start()
        thread.join(0.3)
        assert thread.is_alive()
        store.save(rotated)
    thread.join(5)

    assert result == ["access-2"]
    assert httpx_mock.get_requests() == []


def test_refresh_server_error_is_a_provider_error(tmp_path, httpx_mock):
    _, tokens = _tokens(tmp_path, _record(), now=NOW + 3600)
    httpx_mock.add_response(url=TOKEN_URL, status_code=503, text="unavailable")

    with pytest.raises(ProviderError, match="could not refresh the ChatGPT sign-in: .*503"):
        tokens.access_token()


def test_signed_out_store_is_a_provider_error(tmp_path):
    _, tokens = _tokens(tmp_path, signin.signed_out(_record()), now=NOW)

    with pytest.raises(ProviderError, match="not signed in to ChatGPT; run `ankery login`"):
        tokens.access_token()


# ---------------------------------------------------------------------------
# Revocation and models
# ---------------------------------------------------------------------------


def test_revoke_posts_the_refresh_token(httpx_mock):
    httpx_mock.add_response(url=signin.REVOKE_URL)

    signin.revoke(_record())

    assert _form(httpx_mock.get_request()) == {
        "token": "refresh-1",
        "token_type_hint": "refresh_token",
        "client_id": ISSUED,
    }


def test_list_models_keeps_listed_slugs(httpx_mock):
    httpx_mock.add_response(
        url=signin.MODELS_URL,
        json={
            "models": [
                {"slug": "gpt-5.5", "visibility": "list"},
                {"slug": "gpt-reserve", "visibility": "hide"},
                {"slug": "gpt-6-astra", "visibility": "list"},
            ]
        },
    )

    assert signin.list_models("access-1") == ["gpt-5.5", "gpt-6-astra"]
    assert httpx_mock.get_request().headers["Authorization"] == "Bearer access-1"


def test_list_models_error_carries_the_detail(httpx_mock):
    httpx_mock.add_response(url=signin.MODELS_URL, status_code=401, json={"detail": "bad token"})

    with pytest.raises(SignInError, match="HTTP 401: bad token"):
        signin.list_models("access-1")


# ---------------------------------------------------------------------------
# config wiring
# ---------------------------------------------------------------------------


def test_token_source_needs_a_sign_in():
    with pytest.raises(ConfigError, match="needs a ChatGPT sign-in; run `ankery login`"):
        _token_source(Config())


def test_token_source_reads_the_stored_sign_in(httpx_mock):
    TokenStore(signin.tokens_path()).save(_record(saved_at=datetime.now(UTC).isoformat()))

    source = _token_source(Config(llm_timeout=12.0))

    assert source.access_token() == "access-1"
    assert source.timeout == 12.0
