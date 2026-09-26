"""`describe_configuration`, `report_a_problem`, and the flavour machinery behind them
(`_flavours.py`). `demonstration_plan` has its own file (`test_demo.py`)."""
import pytest

from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.mcp import _flavours, create_server


def _call(server, name, **kw):
    return server._tool_manager.get_tool(name).fn(**kw)


# --- describe_configuration -----------------------------------------------------------------

def test_describe_configuration_reports_enabled_capabilities():
    server = create_server(backend=None,
                           policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "describe_configuration")
    assert out["capabilities_enabled"] == ["mail.read"]
    assert set(policy.ALL_CAPABILITIES) <= set(out["capabilities_available"])


def test_describe_configuration_reports_required_scopes_matching_auth_scopes_for():
    from csa_google_gmail_calendar import auth
    server = create_server(backend=None,
                           policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "describe_configuration")
    assert out["required_scopes"] == auth.scopes_for(frozenset({policy.MAIL_READ}))


def test_describe_configuration_reports_no_granted_scopes_for_an_unreadable_token_file(
        tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    token_path.write_text("not valid json")
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "describe_configuration")
    assert out["granted_scopes"] is None
    assert out["scopes_sufficient"] is None


def test_describe_configuration_reports_no_granted_scopes_when_nothing_is_cached(
        tmp_path, monkeypatch):
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(tmp_path / "token.json"))
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "describe_configuration")
    assert out["granted_scopes"] is None
    assert out["scopes_sufficient"] is None


def test_describe_configuration_reports_granted_scopes_from_a_cached_token(
        tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    server = create_server(backend=None, policy=policy.Policy(frozenset({policy.MAIL_READ})))
    out = _call(server, "describe_configuration")
    assert out["granted_scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert out["scopes_sufficient"] is True


def test_describe_configuration_reports_scopes_insufficient_when_short(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", str(token_path))
    token_path.write_text(
        '{"refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')
    server = create_server(
        backend=None, policy=policy.Policy(frozenset({policy.MAIL_READ, policy.CALENDAR_READ})))
    out = _call(server, "describe_configuration")
    assert out["scopes_sufficient"] is False


def test_describe_configuration_never_returns_a_token_path_or_client_secrets():
    """The stricter bar `config.py`'s own module docstring states: this tool's output must not
    contain what a plain refusal would not already disclose, and a refusal never discloses a
    token path."""
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "describe_configuration")
    blob = repr(out)
    assert "token_path" not in blob
    assert "client_secret" not in blob


def test_describe_configuration_reports_the_attachment_directory_path_only(tmp_path):
    from csa_google_gmail_calendar._attachments import AttachmentPolicy
    attach_dir = tmp_path / "attachments"
    attach_dir.mkdir()
    (attach_dir / "secret.txt").write_text("do not leak this")
    server = create_server(backend=None, policy=policy.Policy(),
                           attach_policy=AttachmentPolicy(str(attach_dir)))
    out = _call(server, "describe_configuration")
    assert out["attachment_directory"] == str(attach_dir.resolve())
    assert "do not leak this" not in repr(out)


def test_describe_configuration_reports_no_attachment_directory_when_unset():
    server = create_server(backend=None, policy=policy.Policy(), attach_policy=None)
    out = _call(server, "describe_configuration")
    assert out["attachment_directory"] is None


def test_describe_configuration_reports_the_active_flavour():
    server = create_server(backend=None, policy=policy.Policy(frozenset(policy.ALL_CAPABILITIES)),
                           flavour="core")
    out = _call(server, "describe_configuration")
    assert out["flavour"] == "core"


def test_describe_configuration_reports_what_the_flavour_hides():
    server = create_server(backend=None, policy=policy.Policy(frozenset(policy.ALL_CAPABILITIES)),
                           flavour="core")
    out = _call(server, "describe_configuration")
    assert "create_event" in out["hidden_by_flavour"]
    assert "create_event" not in out["registered_tools"]


def test_describe_configuration_reports_what_capability_gating_hides():
    server = create_server(backend=None, policy=policy.Policy(frozenset()))
    out = _call(server, "describe_configuration")
    assert "send_message" in out["hidden_by_capability"]


def test_describe_configuration_is_itself_always_registered_regardless_of_flavour():
    server = create_server(backend=None, policy=policy.Policy(frozenset()), flavour="core")
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "describe_configuration" in names


# --- report_a_problem ------------------------------------------------------------------------

def test_report_a_problem_contains_no_ids_or_addresses():
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "report_a_problem")
    blob = repr(out)
    assert "@" not in blob            # no email address
    assert "token" not in blob.lower() or "token_path" not in blob


def test_report_a_problem_names_the_server_version():
    from csa_google_gmail_calendar import __version__
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "report_a_problem")
    assert out["server_version"] == __version__
    assert __version__ in out["report"]


def test_report_a_problem_new_issue_url_points_at_the_public_repo():
    server = create_server(backend=None, policy=policy.Policy())
    out = _call(server, "report_a_problem")
    assert out["issues_url"] in out["new_issue_url"]
    assert out["issues_url"].startswith("https://github.com/CloudSecurityAlliance/")


def test_report_a_problem_reports_capabilities_and_flavour():
    server = create_server(backend=None, policy=policy.Policy(frozenset({policy.MAIL_READ})),
                           flavour="core")
    out = _call(server, "report_a_problem")
    assert out["capabilities_enabled"] == ["mail.read"]
    assert out["flavour"] == "core"


def test_report_a_problem_is_always_registered_regardless_of_flavour():
    server = create_server(backend=None, policy=policy.Policy(frozenset()), flavour="google")
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "report_a_problem" in names


def test_mcp_sdk_version_returns_none_when_it_cannot_be_determined(monkeypatch):
    from csa_google_gmail_calendar.mcp._tools import feedback

    def _boom(name):
        raise Exception("no metadata")

    monkeypatch.setattr(feedback.importlib.metadata, "version", _boom)
    assert feedback._mcp_sdk_version() is None


# --- _flavours.py ------------------------------------------------------------------------------

def test_core_flavour_is_exactly_the_31_tool_spec_minimum_plus_always_registered():
    server = create_server(backend=None, policy=policy.Policy(frozenset(policy.ALL_CAPABILITIES)),
                           flavour="core")
    names = {t.name for t in server._tool_manager.list_tools()}
    assert names == _flavours.CORE_TOOLS | _flavours.ALWAYS_REGISTERED


def test_google_flavour_never_exceeds_the_captured_tool_names():
    server = create_server(backend=None, policy=policy.Policy(frozenset(policy.ALL_CAPABILITIES)),
                           flavour="google")
    names = {t.name for t in server._tool_manager.list_tools()}
    assert names <= _flavours.GOOGLE_TOOLS | _flavours.ALWAYS_REGISTERED


def test_full_flavour_restricts_nothing_beyond_policy():
    unrestricted = create_server(backend=None,
                                 policy=policy.Policy(frozenset(policy.ALL_CAPABILITIES)))
    explicit_full = create_server(backend=None,
                                  policy=policy.Policy(frozenset(policy.ALL_CAPABILITIES)),
                                  flavour="full")
    names_a = {t.name for t in unrestricted._tool_manager.list_tools()}
    names_b = {t.name for t in explicit_full._tool_manager.list_tools()}
    assert names_a == names_b


def test_calendar_delete_off_by_default_takes_precedence_under_every_flavour():
    """A flavour restricts registration further; it never widens what `Policy` already
    excluded. `delete_event` (`calendar.delete`, off by default) must not reappear just
    because a flavour's own allow-list happens to name it."""
    server = create_server(backend=None, policy=policy.Policy(), flavour="google")
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "delete_event" not in names


def test_an_unrecognised_flavour_raises():
    with pytest.raises(ValueError, match="CSA_GGC_FLAVOUR|flavour"):
        create_server(backend=None, policy=policy.Policy(), flavour="bogus")


@pytest.mark.parametrize("raw,expected", [
    ("core", "core"),
    ("CORE", "core"),
    (" google ", "google"),
    ("", "full"),
])
def test_flavour_from_env_normalises_case_and_whitespace(raw, expected):
    assert _flavours.flavour_from_env({"CSA_GGC_FLAVOUR": raw} if raw else {}) == expected


def test_flavour_from_env_unset_is_full():
    assert _flavours.flavour_from_env({}) == "full"


def test_flavour_from_env_refuses_an_unknown_value():
    with pytest.raises(ValueError, match="CSA_GGC_FLAVOUR"):
        _flavours.flavour_from_env({"CSA_GGC_FLAVOUR": "bogus"})


def test_base_for_full_is_the_entire_declared_tool_universe():
    """`_base_for` is only reached with "core"/"google" through `allowed_tool_names` (which
    short-circuits "full" before calling it) - exercised directly here so its own defensive
    default is not dead, untested code."""
    from csa_google_gmail_calendar.mcp._capabilities import TOOL_CAPABILITIES
    assert _flavours._base_for("full") == frozenset(TOOL_CAPABILITIES)
