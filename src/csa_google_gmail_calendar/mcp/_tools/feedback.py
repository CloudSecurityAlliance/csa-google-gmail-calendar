"""`report_a_problem` - everything a filable issue needs, assembled before anyone has to ask,
adapted from `../csa-google-workspace/src/csa_google_workspace/mcp/_tools/feedback.py`'s own
`report_a_problem` (this project's own module docstring below restates its reasoning rather
than re-deriving it, because the reasoning does not change crossing from Drive/Sheets/Slides to
Gmail/Calendar - only WHAT counts as an id does).

A bug report about an MCP server is usually missing the same handful of things: which version,
which Python, which OS, what was enabled. Each one costs a round trip, and the round trip is the
expensive part - by the time the answer arrives the reporter has moved on or upgraded, and the
report is unreproducible. So the server assembles them itself.

**It reports shape, never content, and no ids.** No message id, thread id, draft id, event id,
label id, calendar id, email address, or token. A Gmail message id or a Calendar event id is a
working reference into somebody's real mailbox or calendar - pasting one into a public tracker
is not a diagnostic detail, it is a disclosure. `describe_configuration` is right to list ids
and paths where it does (its audience is the person deciding what to touch on their own
machine); this tool's audience is a public issue tracker, and the bar here is stricter on
purpose - same discipline as that tool's own docstring states for the opposite direction.

No network call. Checking PyPI for a newer release from inside a stdio server would be a
surprising thing for a diagnostic tool to do, and would fail on exactly the offline or
restricted machines most likely to need one; the checklist says how to check instead.
"""
from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
import urllib.parse
from typing import Any

from mcp.server import MCPServer

from ... import __version__
from .._config import Settings
from ._base import LOCAL_READ, tool

ISSUES_URL = "https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/issues"

CHECKLIST = [
    "Say what you expected and what happened instead, including the exact tool name.",
    "Check whether this version is current: "
    "https://pypi.org/project/csa-google-gmail-calendar/ - a fixed bug in an old install is "
    "the most common report.",
    "If the MCP server failed to start, include its stderr: most clients keep it in their "
    "logs, and it is where a configuration error is explained in full.",
    "Do not paste message ids, thread ids, event ids, calendar ids, email addresses, subject "
    "lines, or tokens. Describe what happened instead ('replying to a message in a long "
    "thread sent to the wrong recipient') - an id or address in a public issue is a working "
    "reference into a real mailbox or calendar.",
]


def _mcp_sdk_version() -> str | None:
    try:
        return importlib.metadata.version("mcp")
    except Exception:  # noqa: BLE001 - a version string is a courtesy, never worth crashing on
        return None


def register_feedback_tools(app: MCPServer, settings: Settings, flavour: str) -> None:

    @tool(app, annotations=LOCAL_READ)
    def report_a_problem() -> dict[str, Any]:
        """Assemble a bug report for this server: version, OS, Python, and the active
        capabilities/flavour - no ids, no addresses, no credentials.

        Use this when the user says something is broken, wrong, or missing, or asks how to
        report it. Show them the `report` field and `new_issue_url`, and check the
        `checklist` before they send it - it says, among other things, not to paste a message
        id, an event id, or an email address into what they are about to file.

        Everything about WHAT went wrong is the user's to describe in their own words - a
        message id or an event id would make the report reproducible only at the cost of
        putting a real reference to someone's mailbox or calendar into a public tracker."""
        authorized = os.path.exists(settings.token_path)
        capabilities = sorted(settings.policy.enabled)

        report = "\n".join([
            "### Environment",
            "",
            "```",
            f"{'Server version'.ljust(20)}{__version__}",
            f"{'Python'.ljust(20)}{sys.version.split()[0]}",
            f"{'OS'.ljust(20)}{platform.system()} {platform.release()}",
            f"{'Architecture'.ljust(20)}{platform.machine()}",
            f"{'MCP SDK'.ljust(20)}{_mcp_sdk_version() or 'unknown'}",
            f"{'Flavour'.ljust(20)}{flavour}",
            f"{'Capabilities'.ljust(20)}{', '.join(capabilities) or 'none'}",
            f"{'Authorized'.ljust(20)}{authorized}",
            "```",
            "",
            "### What happened",
            "",
            "<!-- What you did, what you expected, what happened instead. Include the tool",
            "     name. Do not paste message/thread/event/calendar ids or email addresses. -->",
        ])

        title = f"[{__version__}] "
        query = urllib.parse.urlencode({"title": title, "body": report})
        return {
            "report": report,
            "issues_url": ISSUES_URL,
            "new_issue_url": f"{ISSUES_URL}/new?{query}",
            "server_version": __version__,
            "python_version": sys.version.split()[0],
            "os": f"{platform.system()} {platform.release()}",
            "architecture": platform.machine(),
            "mcp_sdk_version": _mcp_sdk_version(),
            "flavour": flavour,
            "capabilities_enabled": capabilities,
            "authorized": authorized,
            "checklist": list(CHECKLIST),
        }
