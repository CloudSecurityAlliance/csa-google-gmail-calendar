"""Console-script entry point: one command, two modes.

    csa-google-gmail-calendar            # run the stdio server (never prompts)
    csa-google-gmail-calendar login      # interactive consent, in a real terminal

Deliberately hand-rolled argument handling rather than an argparse subparser tree - there is
exactly one verb, and the parsing must not be the interesting part of this file. Adapted from
`../csa-google-workspace/src/csa_google_workspace/mcp/cli.py`; the `configure`/`demo`/`describe`
subcommands that project has are not built yet here - they depend on `_desktop.py`,
`demo.py`, and `describe_configuration` (task 13), and are added when those land.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence

from .. import __version__
from . import _logging
from ._config import policy_from_env, settings_from_env, startup_warnings
from .server import create_server

USAGE = """usage: csa-google-gmail-calendar [login [--force]]

  (no argument)   run the MCP server over stdio, for an MCP client to launch
  login           authorize with Google in a browser and cache the token
  login --force   authorize again even if a cached token looks usable
  --version       print the installed version and exit

Clients that support MCP URL elicitation (Claude Code) can authorize in-session via the
`authenticate` tool instead, with no terminal step.

environment:
  CSA_GGC_TOKEN_PATH     token cache path (default ~/.csa_google_gmail_calendar/token.json)
  CSA_GGC_CAPABILITIES   which capabilities this deployment enables - the complete list, not a
                         delta. Unset means the shipped default: every capability except
                         mail.delete and calendar.delete (both irreversible and off by
                         default). Tokens: any capability name, plus `default`, `all`, `none`.
                           default,mail.delete           the usual set, plus permanent delete
                           mail.read,calendar.read       exactly these two
                           none                          nothing enabled
  CSA_GGC_CLIENT_SECRETS OAuth client secrets JSON (`login`/`authenticate` only; defaults to
                         ~/.csa_google_gmail_calendar/client_secret.json if that exists)
  CSA_GGC_ATTACH_DIR     directory outgoing mail may attach files from (unset: attachments off)
  CSA_GGC_LOG_LEVEL      DEBUG|INFO|WARNING|ERROR|CRITICAL (default WARNING)
"""


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    # Before anything else that might log, and here rather than in the library: only an
    # application configures logging (see `_logging.py`).
    _logging.configure(env)

    if argv and argv[0] in ("-h", "--help", "help"):
        print(USAGE, file=sys.stderr)      # stderr: stdout belongs to JSON-RPC
        return 0
    if argv and argv[0] in ("--version", "-V", "version"):
        print(__version__, file=sys.stderr)
        return 0
    if argv and argv[0] == "login":
        from ._login import login  # imported here so the server path never loads it
        rest = argv[1:]
        force = bool(rest) and rest[0] in ("--force", "-f", "--reauth")
        if rest and not force:
            print(f"unknown argument: {rest[0]}\n\n{USAGE}", file=sys.stderr)
            return 2
        policy = policy_from_env(env)
        settings = settings_from_env(env, policy)
        return login(settings, env, force=force)
    if argv:
        print(f"unknown argument: {argv[0]}\n\n{USAGE}", file=sys.stderr)
        return 2

    policy = policy_from_env(env)
    settings = settings_from_env(env, policy)
    # stderr, never stdout: stdout is the JSON-RPC channel and a single stray byte on it
    # corrupts the session.
    for line in startup_warnings(settings):
        print(f"csa-google-gmail-calendar: {line}", file=sys.stderr)

    # `backend=None`: only the auth-lifecycle tools are registered as of task 10, and none of
    # them reads `backend`. Tasks 11/12 replace this with a real `PolicyBackend`-wrapped
    # `ApiBackend`, resolved lazily (never at startup - a missing/expired credential must
    # surface as a readable tool error, not an opaque "server failed to start").
    create_server(None, policy).run(transport="stdio")
    return 0
