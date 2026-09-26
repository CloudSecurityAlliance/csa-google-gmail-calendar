"""`_auth_flow.py`: the loopback listener and the flow wiring around it.

`start_loopback` binds a REAL socket on 127.0.0.1 and serves a REAL local HTTP request - that
is a vertical test (TESTING.md's ladder), not an interactive one: nothing here opens a browser
or reaches Google, only `urllib.request` talking to a listener this same test started. The one
piece genuinely out of reach offline is a real Google `Flow` object's `fetch_token`/
`authorization_url` methods, which need a live authorization code from a live consent screen -
those are exercised by `build_flow`/`consent_url`/`finish` here through a FAKE flow object that
verifies the WIRING (right scopes requested, right method called with the right argument),
which is what this module's own code is responsible for; task 14's gated live suite is what
proves the real `Flow` object itself works.
"""
import json
import urllib.error
import urllib.request

from csa_google_gmail_calendar.mcp import _auth_flow


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310 - fixed 127.0.0.1 url
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# --- Loopback / _Collector, via a real local socket ------------------------------------------

def test_start_loopback_ignores_a_request_with_no_oauth_answer_and_keeps_waiting():
    loopback = _auth_flow.start_loopback()
    try:
        status, body = _get(loopback.redirect_uri_base)
        assert status == 204
        assert body == b""
        assert loopback.wait(0.2) is None
    finally:
        loopback.close()


def test_start_loopback_ignores_a_request_with_state_but_no_code_or_error():
    loopback = _auth_flow.start_loopback()
    try:
        status, _ = _get(loopback.redirect_uri_base + "?state=abc")
        assert status == 204
        assert loopback.wait(0.2) is None
    finally:
        loopback.close()


def test_start_loopback_captures_the_redirect_when_state_and_code_arrive():
    loopback = _auth_flow.start_loopback()
    try:
        status, body = _get(loopback.redirect_uri_base + "?state=abc&code=xyz")
        assert status == 200
        assert b"<html" in body.lower()
        redirect = loopback.wait(5.0)
        assert redirect is not None
        assert "state=abc" in redirect
        assert "code=xyz" in redirect
    finally:
        loopback.close()


def test_start_loopback_treats_error_the_same_as_code():
    """The user clicking Cancel sends `?error=access_denied&state=...` - this must end the
    wait, not hang for the full timeout with nothing to show for it."""
    loopback = _auth_flow.start_loopback()
    try:
        status, _ = _get(loopback.redirect_uri_base + "?state=abc&error=access_denied")
        assert status == 200
        redirect = loopback.wait(5.0)
        assert redirect is not None
        assert "error=access_denied" in redirect
    finally:
        loopback.close()


def test_loopback_close_is_safe_to_call_twice():
    loopback = _auth_flow.start_loopback()
    loopback.close()
    loopback.close()  # must not raise


def test_loopback_close_swallows_an_oserror_from_server_close():
    loopback = _auth_flow.start_loopback()

    def _boom():
        raise OSError("already closed")

    loopback._server.server_close = _boom  # type: ignore[method-assign]
    loopback.close()  # must not raise


def test_loopback_redirect_uri_base_uses_the_bound_port():
    loopback = _auth_flow.start_loopback()
    try:
        assert loopback.redirect_uri_base == f"http://127.0.0.1:{loopback.port}/"
    finally:
        loopback.close()


def test_quiet_handler_log_message_produces_no_output(capsys):
    handler = _auth_flow._QuietHandler.__new__(_auth_flow._QuietHandler)
    _auth_flow._QuietHandler.log_message(handler, "%s - - [%s] %s", "ignored", "ignored", "ignored")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# --- build_flow / consent_url / finish, via a fake Flow (no real Google endpoint) -------------

class _FakeCredentials:
    def __init__(self) -> None:
        self.token = "at"
        self.refresh_token = "rt"


class _FakeFlow:
    def __init__(self) -> None:
        self.redirect_uri: str | None = None
        self.fetch_token_calls: list[str] = []
        self.credentials = _FakeCredentials()

    def authorization_url(self, **kwargs):
        assert kwargs["access_type"] == "offline"
        assert kwargs["prompt"] == "consent"
        return "https://accounts.google.com/o/oauth2/auth?fake=1", "state123"

    def fetch_token(self, *, authorization_response):
        self.fetch_token_calls.append(authorization_response)


def test_consent_url_requests_offline_access_and_forces_the_consent_prompt():
    flow = _FakeFlow()
    url = _auth_flow.consent_url(flow)
    assert url == "https://accounts.google.com/o/oauth2/auth?fake=1"


def test_finish_exchanges_the_redirect_and_writes_the_token(tmp_path, monkeypatch):
    flow = _FakeFlow()
    written = {}
    monkeypatch.setattr(_auth_flow, "_write_token",
                       lambda token_path, creds: written.setdefault("args", (token_path, creds)))
    token_path = str(tmp_path / "token.json")
    _auth_flow.finish(flow, "http://127.0.0.1:1/?state=abc&code=xyz", token_path)
    assert flow.fetch_token_calls == ["http://127.0.0.1:1/?state=abc&code=xyz"]
    assert written["args"] == (token_path, flow.credentials)


def test_build_flow_requests_exactly_the_scopes_the_enabled_capabilities_need(
        tmp_path, monkeypatch):
    from csa_google_gmail_calendar import policy as policy_mod

    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {
        "client_id": "abc", "client_secret": "s",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}}))

    captured = {}

    class _FakeFlowClass:
        @classmethod
        def from_client_config(cls, config, *, scopes, autogenerate_code_verifier):
            captured["config"] = config
            captured["scopes"] = scopes
            captured["autogenerate_code_verifier"] = autogenerate_code_verifier
            return _FakeFlow()

    monkeypatch.setattr("google_auth_oauthlib.flow.Flow", _FakeFlowClass)

    enabled = frozenset({policy_mod.MAIL_READ})
    flow = _auth_flow.build_flow(str(secrets), enabled, "http://127.0.0.1:9/")

    assert captured["scopes"] == _auth_flow.scopes_for(enabled)
    assert captured["autogenerate_code_verifier"] is True
    assert flow.redirect_uri == "http://127.0.0.1:9/"
