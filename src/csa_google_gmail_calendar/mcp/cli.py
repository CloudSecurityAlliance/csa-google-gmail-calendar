"""Console-script entry point: one command, two modes.

    csa-google-gmail-calendar            # run the stdio server (never prompts)
    csa-google-gmail-calendar login      # interactive consent, in a real terminal

Deliberately hand-rolled argument handling rather than an argparse subparser tree - there is
exactly one verb, and the parsing must not be the interesting part of this file. Adapted from
`../csa-google-workspace/src/csa_google_workspace/mcp/cli.py`; the `configure`/`demo`/`describe`
subcommands that project has are not built yet here - they depend on `_desktop.py`,
`demo.py`, and `describe_configuration` (task 13), and are added when those land.

## `_LazyApiBackend`: built once, but not at startup

Task 11 wires a real `Backend` into the default run path for the first time (tasks before it
only ever registered auth tools, which never read `backend` at all). Two constraints, in
tension, shape `_LazyApiBackend` below:

**Must not resolve at process start.** `auth.load_cached_credentials` raises `AuthError` when
no credential is cached yet, or an expired one has no refresh token - the ordinary state of a
freshly-installed server before its first `login`/`authenticate`. Resolving eagerly here would
turn that ordinary state into a startup crash, which an MCP client reports as an opaque "server
failed to start" with no readable remedy - exactly what `_config.py`'s own docstring already
rules out ("nothing resolves eagerly here"). Deferring the same credential load until a tool
is actually invoked turns it back into an ordinary `ToolError` through `_base._errors`'s
existing `exc.AuthError` branch, which already tells the caller to call `authenticate`.

**Built once, not once per thread or per call.** `_config.py`'s own docstring already settles
this: "this project's `Backend` is constructed once by `cli.py` and passed into `create_server`
directly - there is no lazy per-thread provider indirection to build." `ApiBackend` holds one
`discovery.build()`-constructed Gmail service and one Calendar service for its whole lifetime
(`backend.py`'s `from_credentials`), not a fresh one per call, so building a SECOND instance
per thread would not buy back any safety `from_credentials` itself does not already have -
it would only mean N discovery-document loads and N credential reads instead of one.

Both constraints together: resolve lazily, on the FIRST call from any thread, then cache and
reuse that one instance for every call after - a memoizing wrapper around
`ApiBackend.from_credentials`, not a decision this project has not already made. The
`threading.Lock` below exists only to keep two concurrent first calls (the MCP SDK runs sync
tool handlers on a worker thread, so this is a real possibility, not a hypothetical) from
racing into building two separate instances; every call after the first never touches the lock.

**Residual risk, named rather than silently accepted.** `googleapiclient`'s discovery
`Resource` objects are commonly documented as unsafe to share across threads under concurrent
`execute()` calls (the underlying `httplib2`/`google-auth-httplib2` transport can be stateful
per instance). `_config.py`'s design already accepts a single shared instance over a
per-thread one; this class carries that same acceptance forward rather than re-litigating it,
but the risk is real and is flagged in the task 11 report for whoever owns that trade-off to
revisit - concurrent Gmail/Calendar tool calls under load are exactly the condition that would
surface it, and this project has no test that exercises concurrent calls to notice a problem.
"""
from __future__ import annotations

import os
import sys
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from .. import __version__
from .. import auth as auth_mod
from .._attachments import from_env as attachment_policy_from_env
from ..backend import ApiBackend
from ..policy import PolicyBackend
from . import _logging
from ._config import Settings, policy_from_env, settings_from_env, startup_warnings
from .server import create_server


class _LazyApiBackend:
    """Defers `ApiBackend.from_credentials` (and the credential load it requires) until the
    first `Backend` method is actually called - see the module docstring for why this must be
    both lazy AND built only once. Satisfies the `Backend` Protocol structurally via
    `__getattr__`, the same pattern `policy.PolicyBackend` already uses for the identical
    reason (a plain attribute/method proxy, not a formal subclass)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._inner: ApiBackend | None = None
        self._lock = threading.Lock()

    def _resolve(self) -> ApiBackend:
        if self._inner is None:
            with self._lock:
                if self._inner is None:  # double-checked: only the first caller builds it
                    creds = auth_mod.load_cached_credentials(
                        self._settings.token_path, self._settings.required_scopes)
                    self._inner = ApiBackend.from_credentials(creds)
        return self._inner

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._resolve(), name)

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

    # `PolicyBackend` wrapping `_LazyApiBackend`: the credential load (and the Gmail/Calendar
    # discovery-client construction it enables) happens on the FIRST tool call, not here - see
    # `_LazyApiBackend`'s own docstring above for why. `attach_policy` reads
    # `CSA_GGC_ATTACH_DIR` from the real process environment (`_attachments.from_env`,
    # like `settings.token_path` above it) rather than from the `env` mapping this function
    # was handed, matching `_config.py`'s own note on why `token_path` does the same.
    backend = PolicyBackend(_LazyApiBackend(settings), policy)
    attach_policy = attachment_policy_from_env()
    create_server(backend, policy, attach_policy=attach_policy).run(transport="stdio")
    return 0
