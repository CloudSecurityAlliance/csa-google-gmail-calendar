"""Which capability each MCP tool can require - so the server stops advertising authority its
tool surface cannot exercise.

The bug this fixes, found in the sibling project (`csa-google-workspace`) by a model that read
`describe_configuration`, planned work on the strength of it, and only then discovered the
tools did not exist: a hand-written policy can enable a capability no registered tool ever
uses, or - the direction that bites harder - can be missing a tool that a capability was meant
to reach. `policy._GATES` answers "what does this *Backend method* cost?". This answers "what
can this *server* actually reach?". Both are needed, because the MCP layer deliberately exposes
only part of the library (task 11/12 register a curated subset of `Backend`'s methods, not
every one of them, and `flavour` narrows the registered set further still).

**Fail closed both ways.** `tests/test_mcp_capabilities.py` asserts:
  1. every tool `create_server` registers appears as a key here (a tool that arrives
     undeclared would silently widen what the server claims), and
  2. every key here corresponds to a tool that is actually registered (a declaration with no
     tool is the csa-google-workspace bug above: it tells a model a capability is reachable
     when it is not).

A tool added in a later task and left out of this map is therefore caught immediately by
direction 1, in CI, rather than discovered by a model mid-session.
"""
from __future__ import annotations

# Tool name -> the capability a call to it can require, or None for a tool that needs none.
#
# `None` here means "does not gate on a capability at all" (auth-lifecycle tools, reachable
# regardless of which mail/calendar capabilities this deployment enables - see
# `_tools/auth.py`), which is a different statement from a *read* tool once mail/calendar tools
# exist: those still declare their own read capability (`MAIL_READ`/`CALENDAR_READ`), because
# `policy._GATES` gates every Backend method, reads included (see that module's fix-round-2
# note on why a read used to be `Gate(None)` and no longer is).
TOOL_CAPABILITIES: dict[str, str | None] = {
    # auth lifecycle: `authenticate`/`auth_status`/`logout` operate on the local token cache
    # (and, for `authenticate`, Google's OAuth endpoint) directly - never through
    # `PolicyBackend`/`Backend`, so no capability gates them. They are also the one part of
    # this server's surface that must stay reachable under `CSA_GGC_CAPABILITIES=none`: a
    # deployment with every mail/calendar capability disabled must still be able to log in.
    "authenticate": None,
    "auth_status": None,
    "logout": None,
}


def reachable_capabilities() -> frozenset[str]:
    """Capabilities some tool in this server can actually require."""
    return frozenset(c for c in TOOL_CAPABILITIES.values() if c is not None)
