"""Diagnostics go to stderr, and the client persists them. We only choose the level.

Ported from `../csa-google-workspace/src/csa_google_workspace/mcp/_logging.py`, renamed to this
project's env var and logger name. The reasoning is that project's, carried across intact:

A stdio MCP server that writes to stderr already has structured, per-session, timestamped logs
on disk, written by the MCP client (Claude Code: `~/Library/Caches/claude-cli-nodejs/<project>/
mcp-logs-<server>/<timestamp>.jsonl`; Claude Desktop: `~/Library/Logs/Claude/
mcp-server-<name>.log`) - both capture our stderr verbatim, and both are the *parent* capturing
the *child*, so they survive the case a log is most wanted for: failing to start, crashing
mid-call, hanging. A file this process writes is missing exactly then. So there is no log
directory, no rotation, no retention, no session ids, no file permissions here - all of it is
the client's.

## Two rules this module exists to hold

**1. Only an application configures logging.** This is called from `cli.main` and nowhere else.
A *library* that attaches handlers hijacks its embedder's logging - this project is a library
first, and `Backend`/`PolicyBackend` used directly by an embedder must leave their configuration
alone. That is why the handler goes on the `csa_google_gmail_calendar` logger rather than the
root one, and why `propagate` is switched off.

**2. Raising the level raises detail about the OPERATION, never about the CONTENT.** Our stderr
lands in a cache directory we do not control, under the client's retention, invisible to us. A
debug log of a message body or event summary would be a persistence step for an injection
payload, somewhere nobody is watching. More verbosity means more about which call, which
message/event id, what was refused, what Google returned. Never more of what the mail or the
calendar said.
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Mapping

LEVEL_VAR = "CSA_GGC_LOG_LEVEL"
DEFAULT_LEVEL = "WARNING"

# Python's five, and only Python's five - see the module docstring on `mcp`'s own `ctx.log`
# channel, which is a different thing with a different (syslog) vocabulary and a different
# owner (the client, via `logging/setLevel`).
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_ROOT = "csa_google_gmail_calendar"


def level_from_env(env: Mapping[str, str]) -> str:
    """`CSA_GGC_LOG_LEVEL`, defaulting to WARNING - and refusing to guess.

    An unrecognised value is an error rather than a silent fallback. The failure mode of
    guessing is somebody who set `LOG_LEVEL=verbose` to diagnose a problem, saw no extra
    output, and concluded the tool has nothing more to say.
    """
    raw = (env.get(LEVEL_VAR) or "").strip().upper()
    if not raw:
        return DEFAULT_LEVEL
    if raw not in LEVELS:
        raise ValueError(
            f"{LEVEL_VAR}={raw!r} is not a log level. Use one of: {', '.join(LEVELS)}. "
            f"Default is {DEFAULT_LEVEL} - quiet unless something is wrong.")
    return raw


def configure(env: Mapping[str, str]) -> str:
    """Attach one stderr handler to this package's logger. Returns the level applied.

    **stderr, explicitly.** Not `basicConfig`, which touches the root logger, and never
    stdout - under stdio that IS the JSON-RPC channel, and one stray line corrupts the session
    in a way that looks like a hung server from the client end.

    Idempotent: called twice, it replaces rather than doubling.
    """
    level = level_from_env(env)
    logger = logging.getLogger(_ROOT)

    for existing in [h for h in logger.handlers if getattr(h, "_csa_ggc", False)]:
        logger.removeHandler(existing)

    handler = logging.StreamHandler(sys.stderr)
    handler._csa_ggc = True                       # type: ignore[attr-defined]
    # No timestamp: the client stamps every line as it captures it.
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return level
