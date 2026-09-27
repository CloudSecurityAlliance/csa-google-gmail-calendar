"""`_environment.py`: the facts a bug report carries, and whether this copy is current (#27).

`report_a_problem` used to report the installed version and never ask whether it was the latest,
so a careful report could be written in detail against something fixed three releases ago.

**No test here touches the real network.** The one function that can is stubbed at every call
site, and one test exists purely to prove the default path never reaches for it.
"""
import json
import urllib.error

import pytest

from csa_google_gmail_calendar.mcp import _environment as env


class TestInstalledVia:
    """The field exists because the routes fail differently - an isolated tool venv upgrades
    cleanly, a shared environment can have another project's pin holding the version down, and
    an editable checkout may match no release at all. That changes how a maintainer reads the
    report, which is the whole point of carrying it."""

    @pytest.mark.parametrize("path,expected", [
        ("/Users/x/.local/pipx/venvs/pkg/lib/python3.12/site-packages/pkg", "pipx"),
        ("/Users/x/.local/share/uv/tools/pkg/lib/python3.12/site-packages/pkg", "uv tool"),
        ("C:/Users/x/AppData/Roaming/uv/tools/pkg/Lib/site-packages/pkg", "uv tool"),
        ("/Users/x/project/src/pkg", "editable checkout or source tree"),
    ])
    def test_it_reads_the_route_from_the_path(self, path, expected, monkeypatch):
        monkeypatch.setattr(env.os.path, "abspath", lambda _: path)
        monkeypatch.setattr(env.os.path, "dirname", lambda _: path)
        assert env._installed_via() == expected

    def test_uv_must_be_adjacent_to_tools_not_merely_present(self, monkeypatch):
        """`/home/uv/projects/tools/...` is an ordinary path. Two independent `in` checks would
        call it a uv tool install; adjacency is what makes the test mean something."""
        path = "/home/uv/projects/tools/pkg/lib/python3.12/site-packages/pkg"
        monkeypatch.setattr(env.os.path, "abspath", lambda _: path)
        monkeypatch.setattr(env.os.path, "dirname", lambda _: path)
        assert env._installed_via() != "uv tool"

    @pytest.mark.parametrize("same_prefix,expected", [
        (True, "pip (shared environment)"),
        (False, "pip (venv)"),
    ])
    def test_a_site_packages_install_distinguishes_shared_from_venv(
            self, same_prefix, expected, monkeypatch):
        path = "/usr/lib/python3.12/site-packages/pkg"
        monkeypatch.setattr(env.os.path, "abspath", lambda _: path)
        monkeypatch.setattr(env.os.path, "dirname", lambda _: path)
        monkeypatch.setattr(env.sys, "base_prefix", "/usr" if same_prefix else "/other")
        monkeypatch.setattr(env.sys, "prefix", "/usr")
        assert env._installed_via() == expected


class TestUpgradeCommand:
    """"You are out of date" is half an answer. The half that saves time is which of four
    commands to run."""

    @pytest.mark.parametrize("route,fragment", [
        ("pipx", "pipx upgrade"),
        ("uv tool", "uv tool upgrade"),
        ("pip (venv)", "pip install --upgrade"),
        ("pip (shared environment)", "pip install --upgrade"),
    ])
    def test_each_route_gets_its_own_command(self, route, fragment):
        assert fragment in env._upgrade_command(route)

    def test_a_working_tree_is_told_to_pull_not_to_install(self):
        """Telling somebody to `pip install --upgrade` over their own checkout is wrong advice
        and possibly destructive to whatever is uncommitted in it."""
        assert "git pull" in env._upgrade_command("editable checkout or source tree")


class TestVersionComparison:
    @pytest.mark.parametrize("value,expected", [
        ("0.4.0", (0, 4, 0)),
        ("1.10.2", (1, 10, 2)),
    ])
    def test_it_reads_a_plain_numeric_version(self, value, expected):
        assert env._as_tuple(value) == expected

    @pytest.mark.parametrize("value", ["1.0.0rc1", "not-a-version", "", None, "1.0.0+local"])
    def test_anything_else_is_unknown_rather_than_a_guess(self, value):
        """A pre-release or a local segment must not be reported as "you are behind" when it may
        be the opposite. Unknown is the honest answer and the safe one."""
        assert env._as_tuple(value) is None


class TestLatestOnPyPI:
    """Every failure is `None` and none is raised. This adds a courtesy line to a bug report; an
    airgapped machine must lose that line, never the report."""

    def test_it_reads_the_version(self, monkeypatch):
        monkeypatch.setattr(env.urllib.request, "urlopen",
                            lambda *a, **kw: _FakeResponse({"info": {"version": "9.9.9"}}))
        assert env.latest_on_pypi() == "9.9.9"

    @pytest.mark.parametrize("boom", [
        urllib.error.URLError("offline"),
        OSError("connection reset"),
        ValueError("not json"),
    ])
    def test_a_failure_is_none_not_an_exception(self, boom, monkeypatch):
        def raise_it(*a, **kw):
            raise boom
        monkeypatch.setattr(env.urllib.request, "urlopen", raise_it)
        assert env.latest_on_pypi() is None

    @pytest.mark.parametrize("body", [{}, {"info": {}}, {"info": {"version": ""}},
                                      {"info": {"version": 3}}])
    def test_an_unexpected_shape_is_none(self, body, monkeypatch):
        """The index's JSON is somebody else's contract. A shape change must degrade, not crash."""
        monkeypatch.setattr(env.urllib.request, "urlopen",
                            lambda *a, **kw: _FakeResponse(body))
        assert env.latest_on_pypi() is None


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestDescribeEnvironment:
    def test_it_makes_no_network_call_unless_asked(self, monkeypatch):
        """The load-bearing invariant. A stdio server must not reach the network because it
        booted, so every caller except `report_a_problem` gets the offline answer - and the
        sentinel below is what stops that becoming true only by accident."""
        def boom(*a, **kw):
            raise AssertionError("describe_environment() reached the network by default")
        monkeypatch.setattr(env.urllib.request, "urlopen", boom)

        got = env.describe_environment()

        assert got.latest_version is None
        assert got.is_outdated is None

    def test_being_behind_is_reported_with_the_command_to_fix_it(self, monkeypatch):
        monkeypatch.setattr(env, "latest_on_pypi", lambda: "999.0.0")
        got = env.describe_environment(check_pypi=True)
        assert got.is_outdated is True
        assert any("Upgrade and retry before filing" in n for n in got.notes)
        assert any(got.upgrade_command in n for n in got.notes)

    def test_being_current_says_so_without_a_note(self, monkeypatch):
        monkeypatch.setattr(env, "latest_on_pypi", lambda: env.__version__)
        got = env.describe_environment(check_pypi=True)
        assert got.is_outdated is False
        assert not any("Upgrade and retry" in n for n in got.notes)

    def test_an_unreachable_index_is_unknown_and_says_so(self, monkeypatch):
        """`is_outdated` stays None rather than False. False would read as "you are current",
        which is a claim this could not make."""
        monkeypatch.setattr(env, "latest_on_pypi", lambda: None)
        got = env.describe_environment(check_pypi=True)
        assert got.is_outdated is None
        assert any("Could not reach PyPI" in n for n in got.notes)

    def test_an_unparseable_index_version_is_also_unknown(self, monkeypatch):
        monkeypatch.setattr(env, "latest_on_pypi", lambda: "2.0.0rc1")
        assert env.describe_environment(check_pypi=True).is_outdated is None

    def test_a_shared_environment_gets_its_own_note(self, monkeypatch):
        monkeypatch.setattr(env, "_installed_via", lambda: "pip (shared environment)")
        monkeypatch.setattr(env, "latest_on_pypi", lambda: None)
        got = env.describe_environment(check_pypi=True)
        assert any("another project's pin" in n for n in got.notes)


class TestReportAProblemCarriesIt:
    """Until #27 **no test called this tool at all** - the suite only ever checked that its name
    appeared in the registration lists. A tool nobody invokes is a tool nobody has seen work, and
    adding a network call to it was what finally made that visible.

    The index is stubbed in every case here; none of these reaches PyPI.
    """

    @staticmethod
    def _call(monkeypatch, latest):
        import asyncio

        from csa_google_gmail_calendar import policy as policy_mod
        from csa_google_gmail_calendar.mcp import _environment
        from csa_google_gmail_calendar.mcp.server import create_server

        monkeypatch.setattr(_environment, "latest_on_pypi", lambda: latest)
        # `backend=None`: report_a_problem reaches no backend at all, which is the same shape
        # the other server-level tests here use.
        server = create_server(backend=None, policy=policy_mod.Policy())
        result = asyncio.run(server.call_tool("report_a_problem", {}))
        return result.structured_content

    def test_an_out_of_date_copy_is_flagged_in_the_report_and_the_payload(self, monkeypatch):
        out = self._call(monkeypatch, "999.0.0")
        assert out["is_outdated"] is True
        assert out["latest_version"] == "999.0.0"
        assert "OUT OF DATE" in out["report"]
        assert out["upgrade_command"]
        assert any("Upgrade and retry before filing" in n for n in out["notes"])

    def test_a_current_copy_says_latest(self, monkeypatch):
        out = self._call(monkeypatch, env.__version__)
        assert out["is_outdated"] is False
        assert "(latest)" in out["report"]

    def test_an_unreachable_index_says_so_rather_than_implying_current(self, monkeypatch):
        """`(could not check PyPI)` rather than silence. A reader who sees nothing assumes they
        are current, which is the one thing this could not establish."""
        out = self._call(monkeypatch, None)
        assert out["is_outdated"] is None
        assert "could not check PyPI" in out["report"]

    def test_the_report_carries_the_install_route(self, monkeypatch):
        out = self._call(monkeypatch, None)
        assert out["installed_via"]
        assert out["installed_via"] in out["report"]

    def test_it_still_leaks_nothing(self, monkeypatch):
        """The tool's whole premise: a filable report with no ids, addresses or credentials.
        Worth re-asserting now that it carries more environment than it used to."""
        out = self._call(monkeypatch, None)
        blob = out["report"].lower()
        assert "token" not in blob
        assert "@" not in blob
        assert "secret" not in blob
