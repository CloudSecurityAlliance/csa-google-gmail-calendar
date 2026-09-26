"""The auth-lifecycle tools: `authenticate`, `auth_status`, `logout`.

Reachable regardless of `policy.enabled` - see `_capabilities.py` - because a deployment with
every mail/calendar capability disabled must still be able to log in, check on, or revoke its
own credential. None of the three goes through `PolicyBackend`/`Backend` at all; they operate
directly on the local token cache (`auth.py`) and, for `authenticate`, Google's OAuth endpoint.

`authenticate`'s in-band flow is ported from
`../csa-google-workspace/src/csa_google_workspace/mcp/_tools/auth.py`: MCP's own OAuth is for
HTTP transports and authenticates the *client to the server*; this project needs the opposite
(this server authorizing outbound to Google), and for stdio the spec's answer is credentials
from the environment - which is what the `login` CLI command is for. **URL-mode elicitation**
is the sanctioned way to do the equivalent in-band: the server hands the client a URL to send
the user to, and the sensitive exchange happens out-of-band, never through the model's context.
A client without URL elicitation support falls back to a plain instruction to run `login` in a
terminal - the behaviour before this tool existed.

`auth_status` and `logout` have no equivalent in the sibling project; they follow csa-zendesk's
precedent (`AUTH_TOOLS` in that project's `server.py`), adapted to this project's three-state
question (see `auth_status`'s own docstring for why it is three states and not two).
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

import anyio
from google.oauth2.credentials import Credentials
from mcp.server import MCPServer
from mcp.server.elicitation import AcceptedUrlElicitation
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ... import auth
from ... import exceptions as exc
from .._auth_flow import build_flow, consent_url, finish, start_loopback
from .._config import Settings
from ._base import LOCAL_READ, WRITE, tool

# Neither WRITE nor DESTRUCTIVE fits `logout`: it destroys the local credential (and, best
# effort, revokes it at Google) but is safe to call twice - the second call finds nothing to
# remove and says so, rather than failing. `DESTRUCTIVE` in `_base.py` hard-codes
# `idempotent_hint=False` because that is true of every *other* destructive tool this project
# ships (deleting an event twice 404s on the second call); `logout` is the one exception, and
# csa-zendesk's own `logout` tool makes the identical judgment call for the identical reason.
_LOGOUT = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True,
                          open_world_hint=True)

_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def _revoke_best_effort(creds: Credentials) -> bool:
    """Ask Google to revoke the token. Best effort: a network failure here must never stop the
    local file from being removed, because "logout did nothing" is a worse failure than "logout
    could not confirm the remote half."""
    token = creds.token or creds.refresh_token
    if not token:
        return False
    try:
        data = urllib.parse.urlencode({"token": token}).encode("ascii")
        # Fixed, hard-coded HTTPS URL; no caller-supplied value reaches this request.
        request = urllib.request.Request(_REVOKE_URL, data=data, method="POST")  # nosec B310
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _auth_status_payload(token_path: str, required: list[str]) -> dict[str, Any]:
    """No network call, ever - see `auth_status`'s own docstring for why that is the whole
    point of this function existing separately from `auth.load_cached_credentials`, which
    refreshes an expired access token over the network as part of returning usable credentials."""
    if not os.path.exists(token_path):
        return {"status": "no_credential", "token_path": token_path,
                "detail": f"No credential cached at {token_path}. Call `authenticate` to log in."}
    try:
        # `auth._read_cached`, not `auth.load_cached_credentials`: the public function refreshes
        # an expired access token over the network before returning, which is exactly the call
        # this function exists to avoid making.
        creds = auth._read_cached(token_path, required, explain_missing_scopes=True)
    except auth.ScopesMissingError as e:
        return {"status": "scope_short", "token_path": token_path,
                "missing_scopes": list(e.scopes), "detail": str(e)}
    except exc.AuthError as e:
        return {"status": "no_credential", "token_path": token_path,
                "detail": f"The cached credential at {token_path} could not be read ({e}). "
                          f"Call `authenticate` to log in again."}
    detail = f"Credential cached at {token_path} with every required scope."
    # `.expired`/`.refresh_token` are plain attribute reads on the already-loaded object - no
    # network call - so this nuance costs nothing to include and is the same honesty
    # csa-zendesk's own auth_status states: whether a refresh will actually succeed is not
    # knowable without trying it.
    if creds is not None and creds.expired and not creds.refresh_token:
        detail += (" The access token has expired and no refresh token is stored, so the next "
                  "call will fail; call `authenticate` again.")
    elif creds is not None and creds.expired:
        detail += " The access token has expired and will be refreshed automatically on next use."
    return {"status": "ready", "token_path": token_path, "detail": detail}


def register_auth_tools(app: MCPServer, settings: Settings) -> None:
    required = settings.required_scopes

    @tool(app, annotations=WRITE)
    async def authenticate(ctx: Context, force: bool = False) -> dict[str, Any]:
        """Authorize this server to reach your Google Mail and Calendar, via your browser.

        Call this when another tool reports missing credentials, or when `auth_status` reports
        `scope_short` (a cached credential that needs re-consent for a newly-required scope).
        Use force=True to re-authorize even if a cached credential looks usable - for example
        if it belongs to the wrong account."""
        if not force:
            try:
                await anyio.to_thread.run_sync(
                    lambda: auth.load_cached_credentials(settings.token_path, required))
            except exc.AuthError:
                pass
            else:
                return {"status": "already_authorized",
                        "detail": f"A usable credential is already cached at "
                                  f"{settings.token_path}. Pass force=true to authorize again."}

        if not settings.client_secrets:
            raise ToolError(
                "No OAuth client is configured, so a consent URL cannot be built. Set "
                "CSA_GGC_CLIENT_SECRETS or place the client at "
                "~/.csa_google_gmail_calendar/client_secret.json, then run "
                "`csa-google-gmail-calendar login` in a terminal.")

        loopback = start_loopback()
        try:
            flow = build_flow(settings.client_secrets, settings.policy.enabled,
                              loopback.redirect_uri_base)
            url = consent_url(flow)
            elicitation_id = uuid.uuid4().hex

            try:
                answer = await ctx.elicit_url(
                    message=("Authorize access to your Google Mail and Calendar. You will "
                             "sign in as yourself and reach only your own account."),
                    url=url,
                    elicitation_id=elicitation_id,
                )
            except Exception as e:
                # No URL elicitation support (or the client refused the request). Degrade to
                # the terminal path rather than failing outright.
                raise ToolError(
                    "This client cannot open an authorization URL for me. Run "
                    "`csa-google-gmail-calendar login` in a terminal instead - it does the "
                    "same thing, once.") from e

            if not isinstance(answer, AcceptedUrlElicitation):
                return {"status": "declined",
                        "detail": "Authorization was not started. Nothing changed."}

            # Wait off-thread: the listener blocks, and the event loop must not.
            redirect = await anyio.to_thread.run_sync(lambda: loopback.wait(300.0))
            if not redirect:
                return {"status": "timed_out",
                        "detail": "No response from the browser within 5 minutes. Call "
                                  "authenticate again when ready."}

            await anyio.to_thread.run_sync(lambda: finish(flow, redirect, settings.token_path))
            await ctx.session.send_elicit_complete(elicitation_id)
            return {"status": "authorized",
                    "detail": f"Token cached at {settings.token_path}."}
        finally:
            loopback.close()

    @tool(app, annotations=LOCAL_READ)
    def auth_status() -> dict[str, Any]:
        """Report whether a credential is cached, whether it covers every scope this
        deployment's enabled capabilities need, and - if so - whether it looks usable right
        now. Makes no network call and never returns the credential itself.

        Three states, not two: `no_credential` (nothing usable cached - call `authenticate`),
        `scope_short` (a credential IS cached and IS valid, it just predates a capability this
        deployment has since enabled - also call `authenticate`, but the fix is a re-consent,
        not a first login), and `ready`. Collapsing the first two would tell whoever reads this
        "you are not logged in" about a credential that is sitting right there and working
        fine for everything it was issued for - the wrong thing to say when the actual gap is
        one scope.
        """
        return _auth_status_payload(settings.token_path, required)

    @tool(app, annotations=_LOGOUT)
    def logout() -> dict[str, Any]:
        """Revoke the stored credential at Google (best effort) and delete the local token
        file. Safe to call even when already logged out - it reports that rather than
        failing. The only way back is a fresh `authenticate` call."""
        if not os.path.exists(settings.token_path):
            return {"status": "already_logged_out",
                    "detail": f"No credential cached at {settings.token_path}."}
        revoked = False
        try:
            creds = Credentials.from_authorized_user_file(settings.token_path)
            revoked = _revoke_best_effort(creds)
        except (ValueError, OSError):
            pass          # a corrupt or unreadable token still gets removed below
        os.remove(settings.token_path)
        detail = f"Removed the cached credential at {settings.token_path}."
        if not revoked:
            detail += (" Server-side revocation could not be confirmed; the credential may "
                      "still be listed at myaccount.google.com/permissions.")
        return {"status": "logged_out", "revoked_server_side": revoked, "detail": detail}
