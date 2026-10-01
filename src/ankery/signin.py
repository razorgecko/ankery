"""Sign in with ChatGPT: authorization, token store, refresh."""

import base64
import errno
import hashlib
import http.server
import json
import logging
import os
import secrets
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from ankery.providers.base import ProviderError
from ankery.providers.llm import error_detail

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
REVOKE_URL = f"{ISSUER}/api/accounts/oauth/revoke"
RESOURCE = "https://api.openai.com/v1"
MODELS_URL = f"{RESOURCE}/models"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"

CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"

# Registration entry point; the callback returns the client ID to use from then on.
REGISTRATION_CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "ankery"

# Refresh this long before the access token expires.
REFRESH_MARGIN = 300.0
# Tolerated clock difference when checking the ID token's `exp`.
CLOCK_SKEW = 60.0

TOKENS_FILE = "tokens.json"
# Kept through logout: the registration and the account.
_REGISTRATION_KEYS = ("email", "issuer", "subject", "client_id", "ext_agent_host_id")


class SignInError(Exception):
    """Raised when a sign-in, refresh, revocation or token store operation fails."""


def _same(value: str, expected: str) -> bool:
    """Constant-time comparison that also accepts non-ASCII `value`."""
    return secrets.compare_digest(value.encode(), expected.encode())


def tokens_path() -> Path:
    """Return the token store path, honoring XDG."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "state"
    return base / "ankery" / TOKENS_FILE


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def code_challenge(verifier: str) -> str:
    """The PKCE S256 challenge for `verifier`."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


@dataclass(frozen=True)
class Authorization:
    """One sign-in attempt: the authorize URLs and the values that complete it."""

    # Never carries `id_token_hint`.
    url: str
    # `url` plus `id_token_hint` when an ID token is held.
    browser_url: str
    # The client ID sent to authorize: REGISTRATION_CLIENT_ID on first sign-in.
    client_id: str
    ext_agent_host_id: str
    verifier: str
    state: str
    nonce: str


def begin(record: dict[str, Any] | None) -> Authorization:
    """Build an authorization from fresh PKCE, `state` and `nonce`, reusing the
    stored registration if any."""
    record = record or {}
    verifier = _b64url(secrets.token_bytes(32))
    state = _b64url(secrets.token_bytes(16))
    nonce = _b64url(secrets.token_bytes(16))
    client_id = record.get("client_id")
    host_id = record.get("ext_agent_host_id") or f"urn:uuid:{uuid.uuid4()}"
    params = {
        "client_id": client_id or REGISTRATION_CLIENT_ID,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
        "nonce": nonce,
        "resource": RESOURCE,
    }
    id_token_hint = {}
    if client_id is None:
        params["agent_name_hint"] = AGENT_NAME
    elif record.get("id_token"):
        if record.get("email"):
            params["login_hint"] = record["email"]
        id_token_hint = {"id_token_hint": record["id_token"]}
    return Authorization(
        url=f"{AUTHORIZE_URL}?{urlencode(params)}",
        browser_url=f"{AUTHORIZE_URL}?{urlencode({**params, **id_token_hint})}",
        client_id=params["client_id"],
        ext_agent_host_id=host_id,
        verifier=verifier,
        state=state,
        nonce=nonce,
    )


def parse_callback(auth: Authorization, url: str) -> tuple[str, str]:
    """Check a redirect URL against `auth`; return its code and the issued client ID.

    Messages never quote the URL: it carries the authorization code.
    """
    parts = urlsplit(url.strip())
    if parts.path != CALLBACK_PATH:
        raise SignInError(f"not the redirect URL: its path is not {CALLBACK_PATH}")
    query = {key: values[0] for key, values in parse_qs(parts.query).items()}
    if not _same(query.get("state", ""), auth.state):
        raise SignInError("the redirect URL is not from this sign-in (state mismatch)")
    if "error" in query:
        description = query.get("error_description")
        raise SignInError(
            f"sign-in refused: {query['error']}" + (f": {description}" if description else "")
        )
    code = query.get("code")
    if not code:
        raise SignInError("the redirect URL carries no authorization code")
    # A repeat sign-in may omit client_id; the one sent to authorize stays valid.
    client_id = query.get("client_id") or auth.client_id
    if client_id == REGISTRATION_CLIENT_ID:
        raise SignInError("the sign-in did not issue a client ID")
    return code, client_id


def complete(
    auth: Authorization,
    url: str,
    *,
    timeout: float = 30.0,
    clock=time.time,
) -> dict[str, Any]:
    """Exchange the code from redirect `url` and return the record to store."""
    code, client_id = parse_callback(auth, url)
    saved_at = clock()
    body = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": auth.verifier,
            "redirect_uri": REDIRECT_URI,
            "resource": RESOURCE,
        },
        timeout=timeout,
    )
    claims = _id_token_claims(body.get("id_token"))
    _check_id_token(claims, client_id=client_id, nonce=auth.nonce, now=clock())
    return {
        "email": claims.get("email"),
        "issuer": claims["iss"],
        "subject": claims["sub"],
        "client_id": client_id,
        "ext_agent_host_id": auth.ext_agent_host_id,
        "id_token": body["id_token"],
        **_token_fields(body, saved_at),
    }


def _token_fields(body: dict[str, Any], saved_at: float) -> dict[str, Any]:
    """The record fields a token response sets."""
    for key in ("access_token", "refresh_token", "expires_in"):
        if key not in body:
            raise SignInError(f"the token response has no {key}")
    return {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_in": body["expires_in"],
        "scopes": str(body.get("scope", "")).split(),
        "saved_at": datetime.fromtimestamp(saved_at, UTC).isoformat(),
    }


def _id_token_claims(id_token: Any) -> dict[str, Any]:
    """Decode an ID token's payload without verifying its signature: the token
    came straight from TOKEN_URL over TLS."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (AttributeError, IndexError, ValueError) as exc:
        raise SignInError("the token response has no readable ID token") from exc
    if not isinstance(claims, dict):
        raise SignInError("the token response has no readable ID token")
    return claims


def _check_id_token(
    claims: dict[str, Any], *, client_id: str, nonce: str, now: float
) -> None:
    """Check the ID token's claims."""
    if claims.get("iss") != ISSUER:
        raise SignInError(f"the ID token's issuer is not {ISSUER}")
    audience = claims.get("aud")
    if client_id not in (audience if isinstance(audience, list) else [audience]):
        raise SignInError("the ID token was not issued to this client")
    if not isinstance(claims.get("nonce"), str) or not _same(claims["nonce"], nonce):
        raise SignInError("the ID token is not from this sign-in (nonce mismatch)")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp + CLOCK_SKEW < now:
        raise SignInError("the ID token has expired")
    if not claims.get("sub"):
        raise SignInError("the ID token names no account")


# Token endpoint error codes after which the grant is unusable and only a new
# sign-in helps, per the SIWC errors-and-recovery guide.
_TERMINAL_ERRORS = frozenset({
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
})


class _Rejected(SignInError):
    """The token endpoint reported the grant unusable."""


def _token_request(form: dict[str, str], *, timeout: float) -> dict[str, Any]:
    # Log the grant type only: the form and the response carry the secrets.
    logger.info("signin: POST %s (%s)", TOKEN_URL, form["grant_type"])
    try:
        response = httpx.post(TOKEN_URL, data=form, timeout=timeout)
    except httpx.HTTPError as exc:
        raise SignInError(f"token request failed: {exc}") from exc
    if not response.is_success:
        error = _Rejected if _error_code(response) in _TERMINAL_ERRORS else SignInError
        raise error(
            f"token request failed: HTTP {response.status_code}: {_oauth_error(response)}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise SignInError("the token response is not JSON") from exc
    if not isinstance(body, dict):
        raise SignInError("the token response is not an object")
    return body


def _error_code(response: httpx.Response) -> str | None:
    """An OAuth error body's `error`, or the API's `error.code`."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        error = error.get("code")
    return error if isinstance(error, str) else None


def _oauth_error(response: httpx.Response) -> str:
    """An OAuth error body's `error` and `error_description`, else the API's message."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        description = body.get("error_description")
        return body["error"] + (f": {description}" if description else "")
    return error_detail(response)


def refresh(
    record: dict[str, Any], *, timeout: float = 30.0, clock=time.time
) -> dict[str, Any]:
    """Return `record` with a refreshed token set; the refresh token rotates."""
    saved_at = clock()
    body = _token_request(
        {
            "grant_type": "refresh_token",
            "client_id": record["client_id"],
            "refresh_token": record["refresh_token"],
            "resource": RESOURCE,
        },
        timeout=timeout,
    )
    # A refresh response may omit the refresh token; the current one then stays.
    body.setdefault("refresh_token", record["refresh_token"])
    updated = {**record, **_token_fields(body, saved_at)}
    if body.get("id_token"):
        updated["id_token"] = body["id_token"]
    return updated


def revoke(record: dict[str, Any], *, timeout: float = 30.0) -> None:
    """Revoke the record's refresh token."""
    logger.info("signin: POST %s", REVOKE_URL)
    try:
        response = httpx.post(
            REVOKE_URL,
            data={
                "token": record["refresh_token"],
                "token_type_hint": "refresh_token",
                "client_id": record["client_id"],
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SignInError(f"revocation request failed: {exc}") from exc
    if not response.is_success:
        raise SignInError(
            f"revocation request failed: HTTP {response.status_code}: {_oauth_error(response)}"
        )


def list_models(access_token: str, *, timeout: float = 30.0) -> list[str]:
    """Slugs of the models listed for the signed-in plan."""
    logger.info("signin: GET %s", MODELS_URL)
    try:
        response = httpx.get(
            MODELS_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=timeout
        )
    except httpx.HTTPError as exc:
        raise SignInError(f"model list request failed: {exc}") from exc
    if not response.is_success:
        raise SignInError(
            f"model list request failed: HTTP {response.status_code}: {error_detail(response)}"
        )
    try:
        models = response.json()["models"]
        return [m["slug"] for m in models if m.get("visibility") == "list"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SignInError(f"unexpected model list shape: {exc}") from exc


def is_signed_in(record: dict[str, Any] | None) -> bool:
    return bool(record and record.get("refresh_token") and record.get("client_id"))


def expires_at(record: dict[str, Any]) -> datetime:
    """When the record's access token expires."""
    saved_at = datetime.fromisoformat(record["saved_at"])
    return datetime.fromtimestamp(saved_at.timestamp() + record["expires_in"], UTC)


def signed_out(record: dict[str, Any]) -> dict[str, Any]:
    """`record` without its tokens; the registration and account stay."""
    return {key: record[key] for key in _REGISTRATION_KEYS if key in record}


if sys.platform == "win32":

    def _lock_fd(fd: int) -> None:
        # LK_LOCK gives up with EDEADLOCK after ten one-second attempts.
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError as exc:
                if exc.errno != errno.EDEADLOCK:
                    raise

    def _unlock_fd(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class TokenStore:
    """The token record in one JSON file, written only by ankery."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Hold the store's exclusive lock, which other processes see too."""
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise SignInError(f"could not lock {self.path}: {exc}") from exc
        try:
            try:
                _lock_fd(fd)
            except OSError as exc:
                raise SignInError(f"could not lock {self.path}: {exc}") from exc
            try:
                yield
            finally:
                _unlock_fd(fd)
        finally:
            os.close(fd)

    def load(self) -> dict[str, Any] | None:
        """The stored record; None if there is no file."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SignInError(f"could not read {self.path}: {exc}") from exc
        try:
            record = json.loads(text)
        except ValueError as exc:
            raise SignInError(
                f"{self.path} is not valid JSON; delete it and run `ankery login`"
            ) from exc
        if not isinstance(record, dict):
            raise SignInError(
                f"{self.path} does not hold an object; delete it and run `ankery login`"
            )
        return record

    def save(self, record: dict[str, Any]) -> None:
        """Write `record` atomically with mode 0600."""
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tokens-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(record, fh, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise SignInError(f"could not write {self.path}: {exc}") from exc


class StoredTokens:
    """TokenSource over a TokenStore; refreshes and saves near expiry."""

    def __init__(self, store: TokenStore, *, timeout: float = 30.0, clock=time.time) -> None:
        self.store = store
        self.timeout = timeout
        self.clock = clock

    def access_token(self) -> str:
        # Under the store lock, so no other thread or process spends the same
        # rotating refresh token; it then re-reads the record another one saved.
        try:
            with self.store.lock():
                return self._access_token()
        except SignInError as exc:
            raise ProviderError(str(exc)) from exc

    def _access_token(self) -> str:
        record = self.store.load()
        if not is_signed_in(record):
            raise ProviderError("not signed in to ChatGPT; run `ankery login`")
        if expires_at(record).timestamp() - REFRESH_MARGIN > self.clock():
            return record["access_token"]
        try:
            refreshed = refresh(record, timeout=self.timeout, clock=self.clock)
        except _Rejected as exc:
            # The tokens are unusable; the registration stays for the next login.
            with suppress(SignInError):
                self.store.save(signed_out(record))
            raise ProviderError(
                f"the ChatGPT sign-in has expired or was revoked ({exc}); "
                "run `ankery login`"
            ) from exc
        except SignInError as exc:
            raise ProviderError(f"could not refresh the ChatGPT sign-in: {exc}") from exc
        self.store.save(refreshed)
        return refreshed["access_token"]


def can_open_browser(environ: dict[str, str] | None = None, platform: str | None = None) -> bool:
    """Whether a graphical browser can be expected. Elsewhere `webbrowser` falls
    back to a console browser, which would take over the terminal."""
    env = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform in ("darwin", "win32"):
        return True
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    # Drops a browser's idle preconnect instead of blocking the single-threaded server.
    timeout = 5

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != CALLBACK_PATH:
            self.send_error(404)
            return
        # Another state is a stale tab or another page; refusing it without
        # ending the wait keeps it from cancelling this sign-in.
        state = parse_qs(parts.query).get("state", [""])[0]
        if not _same(state, self.server.state):
            self._reply(400, b"This is not the sign-in ankery is waiting for.\n")
            return
        self.server.callback_url = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{self.path}"
        self._reply(
            200, b"ankery received the sign-in. You can close this tab and return to the terminal.\n"
        )

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # The default writes the request line, which carries the code, to stderr.
        pass


class CallbackServer:
    """Listens on REDIRECT_URI for the redirect carrying `state`."""

    def __init__(self, state: str, host: str = CALLBACK_HOST, port: int = CALLBACK_PORT) -> None:
        self._server = http.server.HTTPServer((host, port), _CallbackHandler)
        self._server.state = state
        self._server.callback_url = None

    def wait(self, timeout: float) -> str:
        """Serve until the redirect with the expected state arrives; return its URL."""
        deadline = time.monotonic() + timeout
        while self._server.callback_url is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SignInError("timed out waiting for the browser sign-in")
            self._server.timeout = remaining
            self._server.handle_request()
        return self._server.callback_url

    def close(self) -> None:
        self._server.server_close()

    def __enter__(self) -> "CallbackServer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
