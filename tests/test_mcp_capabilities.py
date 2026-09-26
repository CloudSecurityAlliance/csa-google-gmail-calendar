"""Fail-closed both ways (`_capabilities.py`'s own docstring): every registered tool is
declared, and every declaration corresponds to a registered tool.

As of task 10 only the auth-lifecycle tools exist (`authenticate`, `auth_status`, `logout`),
none of which is gated on a capability - see `_capabilities.py`. Tasks 11/12 extend
`_TOOL_TO_GATED_METHOD` below as they register capability-gated tools; the fail-closed tests
themselves are written generically so they do not need touching when that happens.
"""
import pytest

from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.mcp import create_server
from csa_google_gmail_calendar.mcp._capabilities import TOOL_CAPABILITIES

# Tool name -> the Backend method it calls, for tools whose declared capability
# (`TOOL_CAPABILITIES`) must match that method's gate (`policy._GATES`). Populated as
# capability-gated tools are registered (tasks 11/12); empty today because the auth tools
# never call a gated Backend method at all - see `_capabilities.py`'s own note on why they are
# `None`.
#
# OBLIGATION ON WHOEVER EXTENDS THIS (fix round 1, task 10 review): unlike the two tests above
# it, `test_declared_capability_matches_the_backend_gate` is NOT structurally generic - it can
# only check pairs that are IN this map. A task 11/12 author who registers a new
# capability-gated tool, gets its `TOOL_CAPABILITIES` entry wrong, AND forgets to add a row
# here gets a green suite anyway: the test has nothing to check it against. Every new
# capability-gated tool this project adds must get a row here, in the same change that
# registers it - carried forward into task 11's brief as well.
_TOOL_TO_GATED_METHOD: dict[str, str] = {}


def _tool_names(server) -> set[str]:
    return {t.name for t in server._tool_manager.list_tools()}


def test_every_registered_tool_is_declared():
    """Fail-closed. A tool that arrives undeclared silently widens what the server claims."""
    server = create_server(backend=None, policy=policy.Policy())
    undeclared = _tool_names(server) - set(TOOL_CAPABILITIES)
    assert not undeclared, f"undeclared tools: {sorted(undeclared)}"


def test_every_declaration_corresponds_to_a_real_tool():
    """The reverse. A declaration with no tool tells a model a capability is reachable when it
    is not - the bug csa-google-workspace found by having a model read `describe_configuration`
    and plan work on the strength of it."""
    server = create_server(backend=None, policy=policy.Policy())
    phantom = set(TOOL_CAPABILITIES) - _tool_names(server)
    assert not phantom, f"declared but not registered: {sorted(phantom)}"


def test_declared_capability_matches_the_backend_gate():
    """The F1 inconsistency in the sibling project: a hand-written map disagreed with the gate
    it was supposed to mirror, and the inconsistency propagated into tool descriptions and a
    demonstration plan. Generic over `_TOOL_TO_GATED_METHOD` rather than a hard-coded pair
    list, so tasks 11/12 extend the map instead of writing a new test."""
    for tool_name, method in _TOOL_TO_GATED_METHOD.items():
        assert TOOL_CAPABILITIES[tool_name] == policy._GATES[method].capability, tool_name


def test_no_tool_is_named_delete_email():
    """ADR-style rule this project states for itself: a tool name is a claim made to a reader
    who cannot check it. `trash_email`/`untrash_email` exist (task 11); a bare, permanent
    `delete_email` does not, because Google's own delete is a 3-scope, off-by-default
    capability (`policy.MAIL_DELETE`) this project has chosen not to name that plainly."""
    server = create_server(backend=None, policy=policy.Policy())
    assert "delete_email" not in _tool_names(server)


# Tool name -> the `open_world_hint` it must declare. Defaults to `True` (the general rule:
# every result here is either Google-authored content, or - for `authenticate`/`logout` - a
# fact that still involves Google's own OAuth endpoint). `auth_status` is the one deliberate
# `False`: it makes no network call (`test_auth_status_makes_no_network_call` in
# test_mcp_server_shape.py) and returns only this server's own computed state about a local
# token file - never Google-authored content. `True` there would not be conservative, it would
# be inaccurate, and a hint that is uniformly `True` across every tool carries no information at
# all (fix round 1, task 10 review). An expected-value map, not an exemption list with a skip:
# a map says what EVERY tool claims, so the next divergence is a visible mismatch rather than a
# silent extra exemption nobody notices growing.
_EXPECTED_OPEN_WORLD_HINT: dict[str, bool] = {
    "auth_status": False,
}


def test_every_tool_declares_the_expected_open_world_hint():
    server = create_server(backend=None, policy=policy.Policy())
    for t in server._tool_manager.list_tools():
        expected = _EXPECTED_OPEN_WORLD_HINT.get(t.name, True)
        assert t.annotations is not None, t.name
        assert t.annotations.open_world_hint is expected, t.name


def test_disabled_capabilities_still_expose_the_auth_lifecycle_tools():
    """The auth tools are reachable regardless of `policy.enabled` - a deployment with every
    mail/calendar capability disabled must still be able to log in, check on, or revoke its own
    credential (`_capabilities.py`). The general "disabled capability -> absent tool" behaviour
    itself is exercised where a real capability-gated tool exists to demonstrate it: task 12's
    `test_delete_event_is_absent_when_calendar_delete_is_off` /
    `test_delete_event_appears_when_the_capability_is_enabled`."""
    no_capabilities = policy.Policy(frozenset())
    names = _tool_names(create_server(backend=None, policy=no_capabilities))
    assert {"authenticate", "auth_status", "logout"} <= names


@pytest.mark.parametrize("capability", sorted(policy.ALL_CAPABILITIES))
def test_reachable_capabilities_are_a_subset_of_all_capabilities(capability):
    from csa_google_gmail_calendar.mcp._capabilities import reachable_capabilities
    assert reachable_capabilities() <= frozenset(policy.ALL_CAPABILITIES)
