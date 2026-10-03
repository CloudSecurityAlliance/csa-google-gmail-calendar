"""`_tools/auth.py`'s own paths not already exercised by `test_mcp_server_shape.py`: the
elicitation flow's accept/decline/timeout/no-support branches, and `logout`'s best-effort
revocation. Monkeypatches at the same boundary the rest of this project mocks - the module's
OWN imports of `start_loopback`/`build_flow`/`consent_url`/`finish` (the real versions of which
are covered directly, without mocking, in `test_auth_flow.py`), and `ctx.elicit_url`/
`ctx.session` (an MCP client's real elicitation UI, which is the one thing here that is
genuinely not offline-testable - task 14's gated live suite covers the real client
round-trip)."""
import urllib.error

import anyio
import pytest
from mcp.server.elicitation import AcceptedUrlElicitation

from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.mcp import _tools
from csa_google_gmail_calendar.mcp._config import Settings
from csa_google_gmail_calendar.mcp._tools import auth as auth_tools


def _call_async(server, name, *args, **kw):
    async def go():
        return await server._tool_manager.get_tool(name).fn(*args, **kw)
    return anyio.run(go)


class _FakeLoopback:
    def __init__(self, redirect: str | None) -> None:
        self.redirect_uri_base = "http://127.0.0.1:12345/"
        self._redirect = redirect
        self.closed = False

    def wait(self, timeout: float) -> str | None:
        return self._redirect

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self) -> None:
        self.completed: list[str] = []

    async def send_elicit_complete(self, elicitation_id: str) -> None:
        self.completed.append(elicitation_id)


class _FakeContext:
    def __init__(self, answer=None, raise_on_elicit: Exception | None = None) -> None:
        self._answer = answer
        self._raise = raise_on_elicit
        self.session = _FakeSession()

    async def elicit_url(self, *, message: str, url: str, elicitation_id: str):
        if self._raise is not None:
            raise self._raise
        return self._answer


def _build_server_and_settings(tmp_path, monkeypatch, *, redirect: str | None = "http://127.0.0.1:1/?state=x&code=y"):
    from mcp.server import MCPServer

    monkeypatch.setattr(auth_tools, "start_loopback", lambda: _FakeLoopback(redirect))
    monkeypatch.setattr(auth_tools, "build_flow", lambda secrets, enabled, redirect_uri: object())
    monkeypatch.setattr(auth_tools, "consent_url", lambda flow: "https://example.invalid/consent")
    finished = []
    monkeypatch.setattr(
        auth_tools, "finish",
        lambda flow, redirect_uri, token_path, client_project=None: finished.append(redirect_uri))

    secrets = tmp_path / "client_secret.json"
    secrets.write_text("{}")
    settings = Settings(token_path=str(tmp_path / "token.json"),
                       client_secrets_path=str(secrets), policy=policy.Policy())
    app = MCPServer(name="test", version="0", instructions="")
    _tools.register_auth_tools(app, settings)
    return app, finished


def test_authenticate_succeeds_when_the_user_accepts_and_the_browser_redirects(
        tmp_path, monkeypatch):
    app, finished = _build_server_and_settings(tmp_path, monkeypatch)
    ctx = _FakeContext(answer=AcceptedUrlElicitation())
    out = _call_async(app, "authenticate", ctx, force=True)
    assert out["status"] == "authorized"
    assert finished == ["http://127.0.0.1:1/?state=x&code=y"]
    assert ctx.session.completed


def test_authenticate_falls_through_to_the_flow_when_no_credential_is_cached(
        tmp_path, monkeypatch):
    """force=False (the default): `load_cached_credentials` raises `AuthError` for a token path
    with nothing on it, which the probe swallows (`except exc.AuthError: pass`) rather than
    treating as a reason to refuse - it means only 'not already authorized', not a failure, so
    the function must still fall through to the ordinary elicitation flow below it."""
    app, finished = _build_server_and_settings(tmp_path, monkeypatch)
    ctx = _FakeContext(answer=AcceptedUrlElicitation())
    out = _call_async(app, "authenticate", ctx)
    assert out["status"] == "authorized"
    assert finished == ["http://127.0.0.1:1/?state=x&code=y"]


def test_authenticate_reports_declined_when_the_user_does_not_accept(tmp_path, monkeypatch):
    app, _ = _build_server_and_settings(tmp_path, monkeypatch)
    ctx = _FakeContext(answer="something-else")
    out = _call_async(app, "authenticate", ctx, force=True)
    assert out["status"] == "declined"


def test_authenticate_reports_timed_out_when_the_browser_never_redirects(tmp_path, monkeypatch):
    app, _ = _build_server_and_settings(tmp_path, monkeypatch, redirect=None)
    ctx = _FakeContext(answer=AcceptedUrlElicitation())
    out = _call_async(app, "authenticate", ctx, force=True)
    assert out["status"] == "timed_out"


def test_authenticate_falls_back_to_login_when_the_client_has_no_url_elicitation(
        tmp_path, monkeypatch):
    app, _ = _build_server_and_settings(tmp_path, monkeypatch)
    ctx = _FakeContext(raise_on_elicit=RuntimeError("no elicitation support"))
    with pytest.raises(Exception, match="cannot open an authorization URL"):
        _call_async(app, "authenticate", ctx, force=True)


def test_authenticate_closes_the_loopback_even_when_elicitation_raises(tmp_path, monkeypatch):
    from mcp.server import MCPServer

    loopback = _FakeLoopback("http://127.0.0.1:1/?state=x&code=y")
    monkeypatch.setattr(auth_tools, "start_loopback", lambda: loopback)
    monkeypatch.setattr(auth_tools, "build_flow", lambda secrets, enabled, redirect_uri: object())
    monkeypatch.setattr(auth_tools, "consent_url", lambda flow: "https://example.invalid/consent")
    secrets = tmp_path / "client_secret.json"
    secrets.write_text("{}")
    settings = Settings(token_path=str(tmp_path / "token.json"),
                       client_secrets_path=str(secrets), policy=policy.Policy())
    app = MCPServer(name="test", version="0", instructions="")
    _tools.register_auth_tools(app, settings)

    ctx = _FakeContext(raise_on_elicit=RuntimeError("no support"))
    with pytest.raises(Exception, match="cannot open an authorization URL"):
        _call_async(app, "authenticate", ctx, force=True)
    assert loopback.closed


# --- logout / _revoke_best_effort ------------------------------------------------------------

def test_revoke_best_effort_returns_false_with_no_token():
    class _NoToken:
        token = None
        refresh_token = None

    assert auth_tools._revoke_best_effort(_NoToken()) is False


def test_revoke_best_effort_returns_true_on_a_successful_revoke(monkeypatch):
    class _Creds:
        token = "at"
        refresh_token = "rt"

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(auth_tools.urllib.request, "urlopen", lambda *a, **kw: _Resp())
    assert auth_tools._revoke_best_effort(_Creds()) is True


def test_revoke_best_effort_returns_false_on_a_network_error(monkeypatch):
    class _Creds:
        token = "at"
        refresh_token = "rt"

    def _boom(*a, **kw):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(auth_tools.urllib.request, "urlopen", _boom)
    assert auth_tools._revoke_best_effort(_Creds()) is False


def test_logout_reports_whether_server_side_revocation_was_confirmed(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", "scopes": []}')
    monkeypatch.setattr(auth_tools, "_revoke_best_effort", lambda creds: False)
    from mcp.server import MCPServer
    s = Settings(token_path=str(token_path), client_secrets_path=None, policy=policy.Policy())
    app = MCPServer(name="test", version="0", instructions="")
    _tools.register_auth_tools(app, s)
    out = app._tool_manager.get_tool("logout").fn()
    assert out["status"] == "logged_out"
    assert out["revoked_server_side"] is False
    assert "could not be confirmed" in out["detail"]


def test_logout_reports_confirmed_revocation_when_it_succeeds(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", "scopes": []}')
    monkeypatch.setattr(auth_tools, "_revoke_best_effort", lambda creds: True)
    from mcp.server import MCPServer
    s = Settings(token_path=str(token_path), client_secrets_path=None, policy=policy.Policy())
    app = MCPServer(name="test", version="0", instructions="")
    _tools.register_auth_tools(app, s)
    out = app._tool_manager.get_tool("logout").fn()
    assert out["revoked_server_side"] is True
    assert "could not be confirmed" not in out["detail"]


def test_auth_status_reports_no_credential_for_an_unreadable_token_file(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("not valid json at all")
    out = auth_tools._auth_status_payload(str(token_path), [])
    assert out["status"] == "no_credential"
    assert "could not be read" in out["detail"]


def test_auth_status_payload_client_project_defaults_to_none_without_client_secrets(tmp_path):
    """`_auth_status_payload` takes `client_secrets_path` as an optional third argument, not the
    environment - a caller (like the tests above) that omits it entirely still gets a
    `client_project` key, just `None`."""
    out = auth_tools._auth_status_payload(str(tmp_path / "no-token-here.json"), [])
    assert out["client_project"] is None


def test_auth_status_payload_reads_the_client_project_from_the_given_client_secrets(tmp_path):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text('{"installed":{"client_id":"cid","project_id":"my-fake-project-123"}}')
    out = auth_tools._auth_status_payload(str(tmp_path / "no-token-here.json"), [], str(secrets))
    assert out["status"] == "no_credential"
    assert out["client_project"] == "my-fake-project-123"


def test_auth_status_detail_says_no_refresh_is_available_when_expired_with_none(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(
        '{"token": "at", "refresh_token": null, "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"expiry": "1970-01-01T00:00:00Z", "scopes": []}')
    out = auth_tools._auth_status_payload(str(token_path), [])
    # `cached`, not `ready`: this calls the payload directly with no verifier, and the
    # function may not claim `ready` for something it has not checked. The detail below
    # is what this test is about, and reaching it at all proves the local checks passed.
    assert out["status"] == "cached"
    assert "no refresh token is stored" in out["detail"]


def test_auth_status_detail_says_refresh_is_automatic_when_a_refresh_token_is_present(
        tmp_path):
    """The other half of the expired-token detail: with a refresh token present, the next call
    will refresh silently rather than fail - a different message from the no-refresh-token
    case above, and neither is what an unexpired token reports (`elif` vs falling through)."""
    token_path = tmp_path / "token.json"
    token_path.write_text(
        '{"token": "at", "refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"expiry": "1970-01-01T00:00:00Z", "scopes": []}')
    out = auth_tools._auth_status_payload(str(token_path), [])
    # `cached`, not `ready`: this calls the payload directly with no verifier, and the
    # function may not claim `ready` for something it has not checked. The detail below
    # is what this test is about, and reaching it at all proves the local checks passed.
    assert out["status"] == "cached"
    assert "refreshed automatically" in out["detail"]


def test_auth_status_detail_carries_no_expiry_caveat_when_not_expired(tmp_path):
    """The third case, distinct from both expired variants above: a credential with a real,
    future `expiry` is not expired at all, so NEITHER the no-refresh-token detail NOR the
    will-refresh-automatically detail applies - the function must fall through the whole
    if/elif untouched to the plain `detail` set at the top."""
    token_path = tmp_path / "token.json"
    token_path.write_text(
        '{"token": "at", "refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"expiry": "2999-01-01T00:00:00Z", "scopes": []}')
    out = auth_tools._auth_status_payload(str(token_path), [])
    # `cached`, not `ready`: this calls the payload directly with no verifier, and the
    # function may not claim `ready` for something it has not checked. The detail below
    # is what this test is about, and reaching it at all proves the local checks passed.
    assert out["status"] == "cached"
    assert "expired" not in out["detail"]


def test_logout_removes_a_corrupt_token_file_without_raising(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("not valid json")
    from mcp.server import MCPServer
    s = Settings(token_path=str(token_path), client_secrets_path=None, policy=policy.Policy())
    app = MCPServer(name="test", version="0", instructions="")
    _tools.register_auth_tools(app, s)
    out = app._tool_manager.get_tool("logout").fn()
    assert out["status"] == "logged_out"
    assert not token_path.exists()
