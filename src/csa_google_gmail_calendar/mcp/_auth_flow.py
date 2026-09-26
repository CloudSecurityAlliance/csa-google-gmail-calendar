"""Loopback OAuth pieces for in-band authorization, without `run_local_server()`.

Ported from `../csa-google-workspace/src/csa_google_workspace/mcp/_auth_flow.py`. One
substantive change: `build_flow` takes the *enabled capability set*, not a `read_only: bool` -
this project computes scopes from `auth.scopes_for(enabled)`, not from a single on/off flag (see
`auth.py`'s module docstring, deviation 1). Everything else - the loopback listener, the
`state`-gated collector, the WSGI plumbing - is that project's, carried across intact.

`InstalledAppFlow.run_local_server()` cannot be used here for two reasons: it `print()`s the
consent URL to stdout (which is the JSON-RPC channel), and it blocks the calling thread until
the browser redirect arrives. In-band auth needs the URL as a *value* to hand to the client, and
needs the wait to happen without stalling the event loop.

So the flow is driven directly:

    start_loopback()            -> a listener on 127.0.0.1:<random port>
    consent_url(flow)           -> the URL to show the user
    loopback.wait(...)          -> the full redirect URI, once Google calls back
    finish(flow, redirect, ...) -> flow.fetch_token(...) and persist the token

`fetch_token` is given the whole redirect URI rather than a bare code, so oauthlib validates the
`state` parameter for us - that check is what stops a CSRF-style injected authorization code.

Loopback with a random port is Google's documented pattern for desktop clients, and no redirect
URI needs pre-registering. The out-of-band copy/paste alternative no longer exists (Google
removed it), so a listener is not optional.
"""
from __future__ import annotations

import threading
import urllib.parse
import wsgiref.simple_server
import wsgiref.util
from dataclasses import dataclass, field

from ..auth import _write_token, read_client_secrets, scopes_for
from ._success_page import SUCCESS_HTML


class _Collector:
    """WSGI app that records the redirect URI and shows the branded success page."""

    def __init__(self) -> None:
        self.redirect_uri: str | None = None
        self.arrived = threading.Event()

    def __call__(self, environ, start_response):
        uri = wsgiref.util.request_uri(environ)
        query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))

        # ONLY AN OAUTH REDIRECT ENDS THE WAIT. Accepting any path/method means the first
        # request to arrive wins - and the listener sits on 127.0.0.1 for 300 seconds while a
        # browser is open, so a favicon fetch or a local port scanner can consume it before the
        # real redirect arrives.
        #
        # `state` is the discriminator because Google always sends it back, on success and on
        # cancel alike - this is not itself a security check (oauthlib validates `state` and
        # PKCE later, during the token exchange); it is a "was this meant for me" check, and the
        # defect it fixes is availability, not authentication.
        #
        # `error` counts as an answer: the user clicking Cancel sends
        # ?error=access_denied&state=..., and treating that as noise would hang the login for
        # the full timeout with nothing on screen explaining why.
        wanted = "state" in query and ("code" in query or "error" in query)
        if not wanted:
            start_response("204 No Content", [("Content-Length", "0")])
            return [b""]

        start_response("200 OK", [("Content-type", "text/html; charset=utf-8")])
        self.redirect_uri = uri
        self.arrived.set()
        return [SUCCESS_HTML.encode("utf-8")]


@dataclass
class Loopback:
    """A one-shot local listener for the OAuth redirect."""

    port: int
    _server: wsgiref.simple_server.WSGIServer
    _collector: _Collector
    _thread: threading.Thread = field(repr=False)

    @property
    def redirect_uri_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def wait(self, timeout: float) -> str | None:
        """Block until the redirect arrives, or `timeout` seconds pass."""
        self._collector.arrived.wait(timeout)
        return self._collector.redirect_uri

    def close(self) -> None:
        try:
            self._server.server_close()
        except OSError:
            pass


class _QuietHandler(wsgiref.simple_server.WSGIRequestHandler):
    """wsgiref logs each request to stderr by default; silence it.

    stderr is safe under stdio (only stdout carries JSON-RPC), but a bare access-log line in the
    middle of an MCP session is noise the user cannot act on.
    """

    def log_message(self, format: str, *args: object) -> None:
        pass


def start_loopback() -> Loopback:
    """Bind 127.0.0.1 on a free port and serve until the OAuth redirect arrives, off-thread.

    Serving in a LOOP rather than exactly once: one-shot handling means the first request to
    arrive wins whether or not it was the redirect. The loop ends when the collector has its
    answer; the caller closes the server, and the thread is a daemon so a process exiting
    mid-consent is not held open by it.
    """
    collector = _Collector()
    wsgiref.simple_server.WSGIServer.allow_reuse_address = False
    server = wsgiref.simple_server.make_server("127.0.0.1", 0, collector,
                                               handler_class=_QuietHandler)
    server.timeout = 0.5          # so the loop notices `arrived` even with no traffic

    def serve_until_answered() -> None:
        while not collector.arrived.is_set():
            try:
                server.handle_request()
            except (OSError, ValueError):  # pragma: no cover - a genuine thread race (the
                # caller's `close()` running on another thread between this thread's own
                # iterations); reachable in production, not deterministically reproducible in
                # a unit test without a fragile timing hack. Covered by the gated live suite
                # (task 14), which exercises the real interactive `authenticate` path this
                # loop backs.
                # `server_close()` sets the descriptor to -1, and `handle_request()` then fails
                # inside `selectors` with `ValueError: Invalid file descriptor: -1` rather than
                # an OSError - both are needed, and the race is inherent to closing from
                # another thread, so the exception IS the stop signal.
                return

    thread = threading.Thread(target=serve_until_answered, daemon=True)
    thread.start()
    return Loopback(port=server.server_port, _server=server,
                    _collector=collector, _thread=thread)


def build_flow(client_secrets: str, enabled: frozenset[str], redirect_uri: str):
    """A google_auth_oauthlib Flow pointed at our loopback, requesting exactly what `enabled`
    needs (`auth.scopes_for`), pointed at our loopback."""
    from google_auth_oauthlib.flow import Flow

    # autogenerate_code_verifier EXPLICITLY. Already the library default, so this changes no
    # behaviour today - it changes what we are relying on. PKCE S256 is a property this flow
    # depends on, and depending on somebody else's default means a future release could turn it
    # off silently.
    #
    # `from_client_config`, not `from_client_secrets_file`: we read the file ourselves so a
    # UTF-8 BOM does not make a valid client unreadable and a malformed one names itself
    # (`auth.read_client_secrets`).
    flow = Flow.from_client_config(read_client_secrets(client_secrets),
                                   scopes=scopes_for(enabled),
                                   autogenerate_code_verifier=True)
    flow.redirect_uri = redirect_uri
    return flow


def consent_url(flow) -> str:
    """The URL to send the user to. `prompt='consent'` so re-auth actually re-prompts."""
    url, _state = flow.authorization_url(access_type="offline", prompt="consent")
    return str(url)


def finish(flow, redirect_uri: str, token_path: str) -> None:
    """Exchange the redirect for a token and persist it with the usual hardening.

    Passing the full `authorization_response` (not a bare code) is deliberate: oauthlib
    validates the `state` parameter from it.
    """
    flow.fetch_token(authorization_response=redirect_uri)
    _write_token(token_path, flow.credentials)
