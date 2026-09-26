"""Console-script entry point: one command, two modes.

    csa-google-gmail-calendar            # run the stdio server (never prompts)
    csa-google-gmail-calendar login      # interactive consent, in a real terminal

Deliberately hand-rolled argument handling rather than an argparse subparser tree - there is
exactly one verb, and the parsing must not be the interesting part of this file. Adapted from
`../csa-google-workspace/src/csa_google_workspace/mcp/cli.py`; the `configure`/`demo`/`describe`
subcommands that project has are not built yet here - they depend on `_desktop.py`,
`demo.py`, and `describe_configuration` (task 13), and are added when those land.

## `_LazyApiBackend`: one `ApiBackend` per thread, each built lazily

Task 11 wires a real `Backend` into the default run path for the first time (tasks before it
only ever registered auth tools, which never read `backend` at all).

**Correction (2026-09-26): this used to build and share ONE `ApiBackend` instance across every
thread, and the reasoning that justified it was wrong on both halves.** `googleapiclient`
clients are NOT thread-safe - `../csa-google-workspace`'s own
`WorkspaceProvider` isolates one `Workspace` per `threading.local()` for exactly this reason,
and that project's `SECURITY.md` forbids sharing one across threads outright; the previous
version of this docstring claimed the opposite precedent, which was simply false. And
`ApiBackend.from_credentials` (`backend.py`) is not "wrapped per call" the way the false
premise claimed - `__init__` stores `gmail_service`/`calendar_service` once, for the object's
whole lifetime, identical in shape to the `Workspace` the sibling isolates per thread. Sharing
one `ApiBackend` under concurrent `execute()` calls (the MCP SDK dispatches sync tool handlers
onto worker threads, via `anyio.to_thread.run_sync` - concurrent calls on different threads are
routine, not a hypothetical) risks a shared `httplib2` transport handing message A's response
body back as the answer to a request about message B: a confidentiality failure, not a crash,
on a server whose whole purpose is handling someone's mail.

**Must still not resolve at process start.** `auth.load_cached_credentials` raises `AuthError`
when no credential is cached yet, or an expired one has no refresh token - the ordinary state
of a freshly-installed server before its first `login`/`authenticate`. Resolving eagerly here
would turn that ordinary state into a startup crash, which an MCP client reports as an opaque
"server failed to start" with no readable remedy - exactly what `_config.py`'s own docstring
rules out ("nothing resolves eagerly here"). Deferring the same credential load until a tool is
actually invoked turns it back into an ordinary `ToolError` through `_base._errors`'s existing
`exc.AuthError` branch, which already tells the caller to call `authenticate`.

**The fix: `threading.local()`, one `ApiBackend` per thread, each built on that thread's own
first call** - mirroring `WorkspaceProvider` exactly rather than inventing a different shape
for the identical problem. No lock: each thread only ever touches its own slot, so there is
nothing to race, and serialising every call behind one lock was rejected - that would defeat
the SDK's whole reason for dispatching onto worker threads in the first place, trading a
correctness bug for a throughput regression. The
per-thread cost is one extra discovery-document load and one extra credential read per worker
thread the SDK happens to use, not per call - cheap next to getting the sibling's isolation
property back.
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
from ._flavours import flavour_from_env
from .server import create_server


class _LazyApiBackend:
    """One `ApiBackend` per thread, each built lazily on that thread's own first `Backend`
    method call - see the module docstring for why sharing a single instance across threads is
    a confidentiality risk, not merely a style choice, and why this mirrors
    `../csa-google-workspace`'s `WorkspaceProvider` rather than caching one instance globally.
    Satisfies the `Backend` Protocol structurally via `__getattr__`, the same pattern
    `policy.PolicyBackend` already uses for the identical reason (a plain attribute/method
    proxy, not a formal subclass)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._local = threading.local()

    def _resolve(self) -> ApiBackend:
        inner = getattr(self._local, "inner", None)
        if inner is None:
            creds = auth_mod.load_cached_credentials(
                self._settings.token_path, self._settings.required_scopes)
            inner = ApiBackend.from_credentials(creds)
            self._local.inner = inner
        return inner

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
  CSA_GGC_FLAVOUR        core|google|full - which tools are REGISTERED, not which refuse
                         (default full - no restriction beyond CSA_GGC_CAPABILITIES). See
                         `describe_configuration`'s own output for what the active flavour
                         hides and why.
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
    # discovery-client construction it enables) happens on each worker thread's OWN first tool
    # call, not here and not shared across threads - see `_LazyApiBackend`'s own docstring
    # above for why a single shared instance is a confidentiality risk, not just a style
    # choice. `attach_policy` reads
    # `CSA_GGC_ATTACH_DIR` from the real process environment (`_attachments.from_env`,
    # like `settings.token_path` above it) rather than from the `env` mapping this function
    # was handed, matching `_config.py`'s own note on why `token_path` does the same.
    backend = PolicyBackend(_LazyApiBackend(settings), policy)
    attach_policy = attachment_policy_from_env()
    flavour = flavour_from_env(env)
    create_server(backend, policy, flavour=flavour, attach_policy=attach_policy).run(
        transport="stdio")
    return 0
