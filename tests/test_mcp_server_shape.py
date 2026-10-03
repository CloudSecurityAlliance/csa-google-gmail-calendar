"""The MCP server skeleton: `create_server`'s shape, the error ladder, unknown-argument
refusal, and the three auth-lifecycle tools.
"""
import anyio
import pytest

from csa_google_gmail_calendar import __version__, policy
from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.mcp import create_server


def _call(server, name, **kw):
    return server._tool_manager.get_tool(name).fn(**kw)


def _call_async(server, name, *args, **kw):
    async def go():
        return await server._tool_manager.get_tool(name).fn(*args, **kw)
    return anyio.run(go)


def test_create_server_returns_a_named_versioned_server():
    server = create_server(backend=None, policy=policy.Policy())
    assert server.name == "csa-google-gmail-calendar"
    # `pyproject.toml`'s [project.name] has no `-mcp` suffix, and neither does the server name -
    # they are the same string on purpose (see task-10-brief.md's context section).
    assert "-mcp" not in server.name
    assert server.version == __version__


def test_a_disabled_capability_set_still_starts_the_server():
    """`create_server` never resolves credentials and never requires a non-empty policy - an
    unconfigured or fully-narrowed deployment still starts; see `_config.py`'s module
    docstring on why nothing here resolves eagerly."""
    server = create_server(backend=None, policy=policy.Policy(frozenset()))
    names = {t.name for t in server._tool_manager.list_tools()}
    # Task 13 adds three more tools that, like the auth lifecycle ones, are gated by no
    # capability at all and so survive a fully-narrowed policy: `describe_configuration`,
    # `demonstration_plan`, `report_a_problem` (`_capabilities.py`). `whoami`/`get_profile`/
    # `list_history` are NOT in this set - they call `Backend.get_profile`/`list_history`,
    # gated `mail.read`, which this policy does not enable.
    assert names == {"authenticate", "auth_status", "logout",
                     "describe_configuration", "demonstration_plan", "report_a_problem"}


def test_auth_status_reports_no_credential_when_nothing_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(tmp_path / "token.json"))
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "auth_status")
    assert out["status"] == "no_credential"


def test_auth_status_reports_scope_short_for_a_token_missing_a_required_scope(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    # A token that only ever requested MAIL_READ's scope, under a policy that also wants
    # CALENDAR_READ - a genuine re-consent case, not a missing login.
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    server = create_server(backend=None,
                           policy=policy.Policy(frozenset({policy.MAIL_READ, policy.CALENDAR_READ})))
    out = _call(server, "auth_status")
    assert out["status"] == "scope_short"
    assert any("calendar" in s for s in out["missing_scopes"])


def test_auth_status_accepts_a_token_carrying_every_required_scope(tmp_path, monkeypatch):
    """`cached` rather than `ready` because this server is built with `backend=None`, so there
    is nothing to verify against - and the function may not claim `ready` for something it has
    not checked. What this test is about is unchanged: a token with every required scope must
    NOT report `scope_short`."""
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    server = create_server(backend=None, policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "auth_status")
    assert out["status"] == "cached"


def _configure_client_secrets_with_project(tmp_path, monkeypatch, project_id="my-fake-project-123"):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(
        '{"installed":{"client_id":"cid","client_secret":"cs",'
        '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
        '"token_uri":"https://oauth2.googleapis.com/token",'
        f'"project_id":"{project_id}"}}}}')
    monkeypatch.setenv("CSA_GGC_CLIENT_SECRETS", str(secrets))


def test_auth_status_reports_client_project_when_no_credential_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(tmp_path / "token.json"))
    _configure_client_secrets_with_project(tmp_path, monkeypatch)
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "auth_status")
    assert out["status"] == "no_credential"
    assert out["client_project"] == "my-fake-project-123"


def test_auth_status_reports_client_project_when_scope_short(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    _configure_client_secrets_with_project(tmp_path, monkeypatch)
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    server = create_server(backend=None,
                           policy=policy.Policy(frozenset({policy.MAIL_READ, policy.CALENDAR_READ})))
    out = _call(server, "auth_status")
    assert out["status"] == "scope_short"
    assert out["client_project"] == "my-fake-project-123"


def test_auth_status_reports_client_project_when_ready(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    _configure_client_secrets_with_project(tmp_path, monkeypatch)
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    server = create_server(backend=None, policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "auth_status")
    # `cached`: `backend=None`, so nothing could be verified. `client_project` rides on every
    # state, which is the point of this test and is unaffected.
    assert out["status"] == "cached"
    assert out["client_project"] == "my-fake-project-123"


def test_auth_status_reports_client_project_as_none_when_no_client_secrets_are_configured(
        tmp_path, monkeypatch):
    # An explicit, nonexistent path - not `delenv` - so this does not depend on whether the
    # machine it runs on happens to have a real default client-secrets file on disk.
    monkeypatch.setenv("CSA_GGC_CLIENT_SECRETS", str(tmp_path / "absent-client-secret.json"))
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(tmp_path / "token.json"))
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "auth_status")
    assert out["client_project"] is None


def test_auth_status_never_refreshes_the_token(tmp_path, monkeypatch):
    """The whole point of the three states: `auth_status` must never refresh a token, which is
    the one operation on this path that talks to the network. `auth._refresh` is the exact
    function that would do it (see `auth.load_cached_credentials`), so patching it to explode
    is a direct regression guard against `auth_status` growing a call to that function."""
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"expiry": "1970-01-01T00:00:00Z", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')

    from csa_google_gmail_calendar import auth as auth_module

    def _boom(creds):
        raise AssertionError("auth_status must not refresh - no network call")

    monkeypatch.setattr(auth_module, "_refresh", _boom)
    server = create_server(backend=None, policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "auth_status")
    # THE ASSERTION THAT MATTERS is that `_boom` was never invoked, which pytest would have
    # raised through by now - and it still holds: verification calls `get_profile`, never
    # `_refresh`. A refresh happens transparently on the next real call, not here.
    #
    # The status is `cached` rather than `ready` only because this server has `backend=None`,
    # so there is nothing to verify against. The test's subject is the absence of a refresh,
    # which is why it was renamed from `..._makes_no_network_call`: auth_status now makes one
    # bounded call on purpose, and that is a different claim from refreshing.
    assert out["status"] == "cached"
    assert "expired" in out["detail"]


def test_logout_is_safe_when_already_logged_out(tmp_path, monkeypatch):
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(tmp_path / "token.json"))
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "logout")
    assert out["status"] == "already_logged_out"


def test_logout_removes_the_token_file(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", "scopes": []}')
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "logout")
    assert out["status"] == "logged_out"
    assert not token_path.exists()


def test_an_unknown_argument_is_refused_rather_than_ignored():
    """csa-zendesk's lesson: a silently-dropped argument is a silently-wrong call."""
    server = create_server(backend=None, policy=policy.Policy())
    with pytest.raises(Exception, match="unknown argument"):
        _call(server, "auth_status", bogus="x")


def test_authenticate_reports_already_authorized_for_a_usable_cached_credential(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    # "token"/"expiry" in the future, so `Credentials.valid` is True and `load_cached_credentials`
    # returns without refreshing (a refresh would be a network call this test cannot make).
    token_path.write_text(
        '{"token": "at", "refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"expiry": "2999-01-01T00:00:00Z", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    server = create_server(backend=None, policy=policy.Policy(frozenset({policy.MAIL_READ})))
    # `ctx` positionally: it is a context-injected parameter, excluded from the declared
    # schema `_refuse_unknown_arguments` checks against (a real MCP client never supplies it
    # as a named argument - the SDK injects it) - passing it as a keyword here would be flagged
    # as an unknown argument by the very refusal this task adds.
    out = _call_async(server, "authenticate", None)
    assert out["status"] == "already_authorized"


def test_authenticate_refuses_without_an_oauth_client_configured(tmp_path, monkeypatch):
    from csa_google_gmail_calendar.mcp import _config
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(tmp_path / "token.json"))
    monkeypatch.delenv("CSA_GGC_CLIENT_SECRETS", raising=False)
    # Isolates this test from whatever the real machine happens to have at the real default
    # path - the assertion is about a deployment with NO client configured, not about this
    # developer's own home directory.
    monkeypatch.setattr(_config, "DEFAULT_CLIENT_SECRETS_PATH", str(tmp_path / "absent.json"))
    server = create_server(backend=None, policy=policy.Policy())
    # force=True skips the cached-credential probe entirely, reaching the "no client secrets"
    # check without needing a Context - ctx is never touched on this path.
    with pytest.raises(Exception, match="No OAuth client is configured"):
        _call_async(server, "authenticate", None, force=True)


def test_authenticate_refuses_an_unknown_argument():
    server = create_server(backend=None, policy=policy.Policy())
    with pytest.raises(Exception, match="unknown argument"):
        _call_async(server, "authenticate", None, bogus="x")


def test_the_installed_mcp_tool_object_names_the_input_schema_attribute_input_schema():
    """Documents the exact attribute this project's `_refuse_unknown_arguments` depends on,
    verified against the installed `mcp` package (2.2.0) rather than assumed - see
    `_tools/_base.py::_declared_properties`'s own docstring for the failure mode of getting
    this wrong (an empty set that refuses every argument, not none)."""
    import mcp.types as wire

    assert "input_schema" in wire.Tool.model_fields
    assert wire.Tool.model_fields["input_schema"].alias == "inputSchema"

# --- auth_status verifies against Google (csa-google-workspace#517, same change) -----------
#
# `ready` used to be a liveness claim made from a file read, and this module's own
# `_auth_status_payload` docstring admitted the gap: "whether a refresh will actually succeed is
# not knowable without trying it." These cover the trying.


def _token_with_scopes(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    return token_path


def test_ready_is_earned_and_names_the_account(tmp_path, monkeypatch):
    """The address is reported because it is what the check LEARNED, and knowing which mailbox
    a server is about to act on is worth seeing before a send."""
    _token_with_scopes(tmp_path, monkeypatch)
    backend = FakeBackend(profile={"emailAddress": "me@example.org"})
    server = create_server(backend=backend,
                           policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "auth_status")
    assert out["status"] == "ready", out
    assert "me@example.org" in out["detail"]


def test_ready_withholds_the_address_when_the_policy_does(tmp_path, monkeypatch):
    """Verification still runs - the policy gates the `get_profile` TOOL, not the backend
    method - but `auth_status` must not hand out an address a deployment withholds. The
    decision lives in `server.py`, next to the policy object; this is what asserts it."""
    _token_with_scopes(tmp_path, monkeypatch)
    backend = FakeBackend(profile={"emailAddress": "me@example.org"})
    server = create_server(backend=backend, policy=policy.Policy(frozenset()))
    out = _call(server, "auth_status")
    assert out["status"] == "ready", out
    assert "me@example.org" not in out["detail"], "an address leaked past the policy"
    assert "Verified against Google" in out["detail"]


def test_google_refusing_the_credential_is_its_own_state(tmp_path, monkeypatch):
    """Not `ready`, and not `no_credential` either: the file IS there and IS well formed, so
    "you have no credential" would be a false description of the machine."""
    from csa_google_gmail_calendar.exceptions import AuthError

    _token_with_scopes(tmp_path, monkeypatch)

    class Refuses(FakeBackend):
        def get_profile(self):
            raise AuthError("invalid_grant: token has been expired or revoked")

    server = create_server(backend=Refuses(),
                           policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "auth_status")
    assert out["status"] == "credential_rejected", out
    assert "revoked" in out["detail"]


def test_an_unknown_failure_degrades_to_cached_not_to_rejected(tmp_path, monkeypatch):
    """THE RULE. A `NotFoundError`, a socket error or a 500 says nothing about the credential.
    Reporting `credential_rejected` would send somebody to log in again because something
    unrelated broke - the same shape of wrong answer as the old `ready`, reversed."""
    _token_with_scopes(tmp_path, monkeypatch)
    # An unseeded FakeBackend raises NotFoundError from `get_profile` by design, which is a
    # realistic stand-in for "the call failed for a reason that is not about the credential".
    server = create_server(backend=FakeBackend(),
                           policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "auth_status")
    assert out["status"] == "cached", out
    assert "could not be checked against Google" in out["detail"]
    assert "NotFoundError" in out["detail"], "the cause has to survive to the caller"


def test_a_hanging_google_times_out_rather_than_hanging_the_tool(tmp_path, monkeypatch):
    """`auth_status` is what somebody calls when things are ALREADY failing, so it has to answer
    when the network is the broken thing. Bounded on a thread, because `signal.alarm` is
    POSIX-only and most of this server's users are on Windows."""
    import time

    from csa_google_gmail_calendar.mcp._tools import auth as auth_tools

    _token_with_scopes(tmp_path, monkeypatch)

    class Hangs(FakeBackend):
        def get_profile(self):
            time.sleep(30)
            raise AssertionError("should have been abandoned")

    monkeypatch.setattr(auth_tools, "_VERIFY_TIMEOUT", 0.05)
    server = create_server(backend=Hangs(),
                           policy=policy.Policy(frozenset({policy.MAIL_READ})))
    started = time.monotonic()
    out = _call(server, "auth_status")
    took = time.monotonic() - started
    assert out["status"] == "cached", out
    assert "did not answer within" in out["detail"]
    assert took < 5, f"the bound did not hold: {took:.1f}s"


def test_a_local_verdict_never_waits_on_the_network(tmp_path, monkeypatch):
    """Verification is reached only once the file exists, parses and carries every scope, so a
    missing credential must not sit through a timeout before saying so."""
    import time

    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(tmp_path / "absent.json"))

    class Hangs(FakeBackend):
        def get_profile(self):
            time.sleep(30)

    server = create_server(backend=Hangs(),
                           policy=policy.Policy(frozenset({policy.MAIL_READ})))
    started = time.monotonic()
    out = _call(server, "auth_status")
    assert out["status"] == "no_credential"
    assert time.monotonic() - started < 1, "a local verdict waited on the network"


def test_it_cannot_claim_ready_without_a_verifier(tmp_path):
    """The invariant: `_auth_status_payload` has one caller and it always passes a verifier.
    This exists so a second caller that forgets cannot resurrect the bug."""
    from csa_google_gmail_calendar.mcp._tools import auth as auth_tools

    token_path = tmp_path / "token.json"
    token_path.write_text(
        '{"token": "at", "refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"expiry": "2999-01-01T00:00:00Z", "scopes": []}')
    out = auth_tools._auth_status_payload(str(token_path), [], None, verify=None)
    assert out["status"] == "cached", out
    assert "NOT checked" in out["detail"]
