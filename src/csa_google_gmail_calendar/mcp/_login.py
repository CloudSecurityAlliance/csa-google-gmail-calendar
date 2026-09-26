"""The `login` subcommand - the only code path permitted to run interactive consent.

Ported from `../csa-google-workspace/src/csa_google_workspace/mcp/_login.py`, simplified: that
project drives an interactive OAuth flow through its `Workspace.from_oauth` wrapper; this one
calls `auth.load_credentials` directly, since there is no `Workspace` layer here and
`load_credentials` already *is* "reuse the cache, else open a browser for consent" (see its own
docstring in `auth.py`).

Isolated in its own module on purpose. `server.py` (and everything it imports) never imports
this module, so `InstalledAppFlow.run_local_server()` is not reachable from the stdio process
even by mistake: it is not in the call graph. `run_local_server()` prints the consent URL with a
bare `print()` and blocks on the browser redirect - harmless in a terminal, fatal under stdio
where stdout carries JSON-RPC.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Mapping

from .. import auth
from ..exceptions import AuthError
from ._config import DEFAULT_CLIENT_SECRETS_PATH, Settings


@contextlib.contextmanager
def _branded_success_page():
    """Serve the CSA-branded page instead of google_auth_oauthlib's plain-text one.

    `_RedirectWSGIApp.__call__` hardcodes `Content-type: text/plain`, so the public
    `success_message=` argument cannot carry markup. Swapping the class for the duration of the
    flow is the only seam.

    Scope is deliberately narrow: the replacement changes the *response body only* and still
    records `last_request_uri`, which is the security-relevant part (it carries the
    authorization code, and oauthlib validates `state` from it). Token exchange is untouched.

    Every failure path falls back to the stock page: a cosmetic upgrade must never be able to
    break authorization.
    """
    try:
        import wsgiref.util

        import google_auth_oauthlib.flow as _flow
        original = _flow._RedirectWSGIApp
    except (ImportError, AttributeError):
        yield
        return

    from ._success_page import SUCCESS_HTML

    class _BrandedRedirectApp:
        def __init__(self, success_message: str) -> None:
            self.last_request_uri: str | None = None
            self._success_message = success_message      # kept for signature compatibility

        def __call__(self, environ, start_response):
            start_response("200 OK", [("Content-type", "text/html; charset=utf-8")])
            self.last_request_uri = wsgiref.util.request_uri(environ)
            return [SUCCESS_HTML.encode("utf-8")]

    _flow._RedirectWSGIApp = _BrandedRedirectApp
    try:
        yield
    finally:
        _flow._RedirectWSGIApp = original


def _client_id_of(path: str) -> str | None:
    """The client_id from a client-secrets file, or None if unreadable.

    None-on-unreadable is deliberate: this only feeds a warning, and a login must not fail
    because the warning could not be computed. Reading through `read_client_secrets` (rather
    than a bare `json.load`) means a BOM or malformed file is treated the same way here as
    everywhere else that reads one.
    """
    try:
        d = auth.read_client_secrets(path)
    except AuthError:
        return None
    return (d.get("installed") or d.get("web") or {}).get("client_id") or None


def _token_client_id(token_path: str) -> str | None:
    try:
        with open(os.path.expanduser(token_path), encoding="utf-8") as f:
            client_id = json.load(f).get("client_id")
            return str(client_id) if client_id else None
    except (OSError, ValueError):
        return None


def login(settings: Settings, env: Mapping[str, str], *, force: bool = False, out=None) -> int:
    """Run interactive OAuth and cache the token. Returns a process exit code."""
    out = out or sys.stdout
    client_secrets = env.get("CSA_GGC_CLIENT_SECRETS")
    if not client_secrets:
        default = os.path.expanduser(DEFAULT_CLIENT_SECRETS_PATH)
        if os.path.exists(default):
            client_secrets = default
        else:
            print("No OAuth client secrets found. Looked at:\n"
                  "  $CSA_GGC_CLIENT_SECRETS  (not set)\n"
                  f"  {DEFAULT_CLIENT_SECRETS_PATH}  (does not exist)\n"
                  "Set CSA_GGC_CLIENT_SECRETS to your Desktop-app OAuth client JSON, or put it "
                  "at the path above.", file=sys.stderr)
            return 2

    required = settings.required_scopes
    if not force:
        try:
            auth.load_cached_credentials(settings.token_path, required)
        except AuthError:
            pass                                  # nothing usable cached - consent below
        else:
            # A cached token can be valid, carry exactly the right scopes, and still have been
            # issued by a *different* OAuth client. Everything then works while running
            # against the wrong project's quota and consent screen - silently. Worth naming,
            # because no error will ever surface it.
            want, have = _client_id_of(client_secrets), _token_client_id(settings.token_path)
            if want and have and want != have:
                print(f"Warning: the cached token was issued by a different OAuth client\n"
                      f"  cached: {have}\n  wanted: {want}\n"
                      f"Re-authorize with `csa-google-gmail-calendar login --force` to use the "
                      f"intended client.", file=sys.stderr)
            print(f"Already authorized (token cache: {settings.token_path}).\n"
                  f"Use `login --force` to authorize again.", file=out)
            return 0

    print(f"Opening a browser to authorize access to your Google Mail and Calendar.\n"
          f"  token cache: {settings.token_path}", file=out)
    # force=True bypasses the cache but deletes nothing: the old token is replaced only once a
    # new one exists, so a cancelled consent leaves the previous one working.
    with _branded_success_page():
        auth.load_credentials(client_secrets, settings.token_path, required, force=force)
    print("Authorized. The MCP server can now start without prompting.", file=out)
    return 0
