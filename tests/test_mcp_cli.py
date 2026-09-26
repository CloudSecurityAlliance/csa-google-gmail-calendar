"""`cli.main`'s argument dispatch. The stdio run path and the interactive `login` path are
stubbed out - actually starting either would block on a socket or a browser, which is exactly
what a unit test must not do (see `_login.py`/`_auth_flow.py`'s own docstrings on why that
code only runs from a real terminal)."""
from csa_google_gmail_calendar import __version__
from csa_google_gmail_calendar.mcp import cli


def test_help_is_printed_and_exits_zero(capsys):
    assert cli.main(["--help"], env={}) == 0
    assert "usage: csa-google-gmail-calendar" in capsys.readouterr().err


def test_version_is_printed_and_exits_zero(capsys):
    assert cli.main(["--version"], env={}) == 0
    assert capsys.readouterr().err.strip() == __version__


def test_an_unknown_argument_is_refused_with_exit_code_two(capsys):
    assert cli.main(["--bogus"], env={}) == 2
    assert "unknown argument" in capsys.readouterr().err


def test_login_dispatches_to_the_login_module(monkeypatch):
    calls = []
    monkeypatch.setattr("csa_google_gmail_calendar.mcp._login.login",
                        lambda settings, env, *, force=False: calls.append(force) or 0)
    assert cli.main(["login"], env={}) == 0
    assert calls == [False]


def test_login_force_is_recognised(monkeypatch):
    calls = []
    monkeypatch.setattr("csa_google_gmail_calendar.mcp._login.login",
                        lambda settings, env, *, force=False: calls.append(force) or 0)
    assert cli.main(["login", "--force"], env={}) == 0
    assert calls == [True]


def test_login_rejects_an_unrecognised_extra_argument(capsys):
    assert cli.main(["login", "--bogus"], env={}) == 2
    assert "unknown argument" in capsys.readouterr().err


def test_the_default_path_builds_a_server_and_runs_it_over_stdio(monkeypatch):
    ran = {}

    class _FakeServer:
        def run(self, transport):
            ran["transport"] = transport

    monkeypatch.setattr("csa_google_gmail_calendar.mcp.cli.create_server",
                        lambda backend, policy, attach_policy=None: _FakeServer())
    assert cli.main([], env={}) == 0
    assert ran == {"transport": "stdio"}


def test_the_default_path_never_loads_credentials_before_a_tool_is_called(monkeypatch):
    """`_LazyApiBackend` must not resolve at startup - a missing/expired credential is the
    ordinary state of a freshly-installed server and must surface as a tool error on first
    use, not a startup crash. Failing `auth.load_cached_credentials` here, and never calling
    it, proves the default path never touches it before a tool actually runs."""
    ran = {}

    class _FakeServer:
        def run(self, transport):
            ran["transport"] = transport

    def _boom(*a, **kw):
        raise AssertionError("credentials must not be loaded before a tool is called")

    monkeypatch.setattr("csa_google_gmail_calendar.auth.load_cached_credentials", _boom)
    monkeypatch.setattr("csa_google_gmail_calendar.mcp.cli.create_server",
                        lambda backend, policy, attach_policy=None: _FakeServer())
    assert cli.main([], env={}) == 0
    assert ran == {"transport": "stdio"}
