"""The MCP server skeleton: `create_server`'s shape, the error ladder, unknown-argument
refusal, and the three auth-lifecycle tools.
"""
import anyio
import pytest

from csa_google_gmail_calendar import __version__, policy
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


def test_auth_status_reports_ready_when_every_required_scope_is_present(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    server = create_server(backend=None, policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "auth_status")
    assert out["status"] == "ready"


def test_auth_status_makes_no_network_call(tmp_path, monkeypatch):
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
    # An expired access token with no refresh performed is still `ready` (a refresh happens
    # transparently on the next real call, not here) - the assertion that matters is that
    # `_boom` above was never invoked, which pytest would have raised through by now.
    assert out["status"] == "ready"
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
