"""Environment -> Settings, for the parts of the MCP server that `create_server`'s own
parameters (`backend`, `policy`, `flavour`, `attach_policy`) don't cover: where the OAuth
client-secrets file lives, and what to tell the `authenticate`/`login` paths to request.

Deliberately free of any `mcp` SDK import, so it is testable without the optional extra - same
rule as `../csa-google-workspace/src/csa_google_workspace/mcp/_config.py`, which this is a
much smaller version of. That project's `Settings` also owns allowlists, profiles and export
directories; none of that exists here.

**Correction (2026-09-26): this docstring used to claim `Backend` needed no per-thread
provider, "because nothing here holds a non-thread-safe client the way `googleapiclient` does
at the `Workspace` layer." That was backwards.**
`googleapiclient` clients are NOT thread-safe, full stop - the sibling's `WorkspaceProvider`
isolates one `Workspace` per `threading.local()` for exactly that reason, and its own
`SECURITY.md` forbids sharing one across threads. `ApiBackend.from_credentials` (`backend.py`)
is not "wrapped per call" either: `__init__` stores its Gmail/Calendar discovery clients once,
for the object's whole lifetime, the same shape as the `Workspace` the sibling isolates.
`cli.py`'s `_LazyApiBackend` mirrors `WorkspaceProvider` instead - one `ApiBackend` per thread,
each built lazily on that thread's own first call - and its own docstring carries the full
reasoning and the confidentiality risk a shared instance would create.

**Nothing resolves eagerly here either.** `settings_from_env` only reads environment variables
and checks whether a file exists; it never touches the network and never raises for a missing
credential. A missing OAuth client is reported later, when `login` or `authenticate` actually
need one - a fail-fast here would make an unconfigured server refuse to start, and an MCP
client reports that as an opaque "server failed to start" rather than the readable remedy a
tool error carries.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .. import auth
from ..policy import ALL_CAPABILITIES, DEFAULT_ENABLED, IRREVERSIBLE, Policy

CLIENT_SECRETS_VAR = "CSA_GGC_CLIENT_SECRETS"
CAPABILITIES_VAR = "CSA_GGC_CAPABILITIES"
DEFAULT_CLIENT_SECRETS_PATH = "~/.csa_google_gmail_calendar/client_secret.json"


@dataclass(frozen=True)
class Settings:
    """What the auth-lifecycle tools and the `login`/`configure` CLI paths need beyond
    `backend`/`policy`/`flavour`/`attach_policy`, which `create_server`'s own parameters cover.

    `client_secrets` is **optional and never needed to start**: reading and refreshing a cached
    token requires no client file, because the token carries its own client_id and secret
    (`auth.load_cached_credentials`). It is used only by `authenticate` and `login`, which have
    to construct a fresh consent URL and therefore do need the client. Absent, both report how
    to obtain one; everything else works unchanged.
    """
    token_path: str
    client_secrets: str | None
    policy: Policy

    @property
    def required_scopes(self) -> list[str]:
        """The OAuth scopes `policy.enabled` needs - what `authenticate`/`login` request and
        what `auth_status` checks a cached token against."""
        return auth.scopes_for(self.policy.enabled)


def settings_from_env(env: Mapping[str, str], policy: Policy) -> Settings:
    """Build `Settings` from the environment and the `Policy` `create_server` was given.

    `policy` is a parameter rather than re-derived from `env` here, so there is exactly one
    place a deployment's capability set is decided (`cli.py`, which builds the `Policy` once
    and passes it to both `create_server` and this function) - re-parsing
    `CSA_GGC_CAPABILITIES` a second time here could disagree with it. `policy_from_env` below
    is that one place.

    `token_path` reads `auth.token_path_default()`, which reads `CSA_GGC_TOKEN_PATH` from the
    real process environment directly rather than from `env` - a pre-existing, reviewed
    function (task 9) that this project does not change without saying so. A caller passing a
    synthetic `env` mapping to test this function will not see it reflected in `token_path`;
    everything else here does honour `env`.
    """
    explicit = env.get(CLIENT_SECRETS_VAR)
    default = os.path.expanduser(DEFAULT_CLIENT_SECRETS_PATH)
    return Settings(
        token_path=auth.token_path_default(),
        client_secrets=explicit or (default if os.path.exists(default) else None),
        policy=policy,
    )


def policy_from_env(env: Mapping[str, str]) -> Policy:
    """`CSA_GGC_CAPABILITIES` - the complete list of capabilities this server may exercise.

    Absolute, not a delta: reading the line tells you everything that is permitted, without
    also knowing what the defaults were on the day it was written. The token `default` expands
    to the built-in set, so a delta is still expressible and still self-describing:

        CSA_GGC_CAPABILITIES=default,mail.delete       # the usual set, plus permanent delete
        CSA_GGC_CAPABILITIES=mail.read,calendar.read   # exactly these two
        CSA_GGC_CAPABILITIES=none                      # nothing enabled

    Unset returns `Policy()`, i.e. `policy.DEFAULT_ENABLED`.
    """
    raw = (env.get(CAPABILITIES_VAR) or "").strip()
    if not raw:
        return Policy()
    entries = [e.strip() for e in raw.replace(";", ",").split(",") if e.strip()]
    if entries == ["none"]:
        return Policy(enabled=frozenset())
    enabled: set[str] = set()
    unknown: list[str] = []
    for entry in entries:
        if entry == "default":
            enabled |= set(DEFAULT_ENABLED)
        elif entry == "all":
            enabled |= set(ALL_CAPABILITIES)
        elif entry in ALL_CAPABILITIES:
            enabled.add(entry)
        else:
            unknown.append(entry)
    if unknown:
        # Fail loudly rather than silently running with a smaller policy than intended: a
        # typo'd capability name would otherwise read as "configured" and behave as "off".
        raise ValueError(
            f"{CAPABILITIES_VAR} contains unknown value(s): {', '.join(unknown)}. Known "
            f"capabilities: {', '.join(ALL_CAPABILITIES)}. Also accepted: 'default', 'all', "
            f"'none'.")
    return Policy(enabled=frozenset(enabled))


def startup_warnings(settings: Settings) -> list[str]:
    """What to tell the operator on stderr before the first tool call.

    An unconfigured server *starts* by design (a startup crash reaches the user as an opaque
    "server failed to start"), so anything they need to know has to be said here or in a tool
    error.
    """
    out: list[str] = []
    enabled = sorted(settings.policy.enabled)
    irreversible = sorted(IRREVERSIBLE & settings.policy.enabled)
    out.append(f"capabilities enabled: {', '.join(enabled) or 'none'}")
    if irreversible:
        out.append(f"  of those, {len(irreversible)} cannot be undone through this server: "
                   f"{', '.join(irreversible)}.")
    if not settings.client_secrets:
        out.append(
            f"no OAuth client secrets configured ({CLIENT_SECRETS_VAR} is unset, and no file "
            f"exists at {DEFAULT_CLIENT_SECRETS_PATH}) - the `authenticate` tool and `login` "
            f"command will not work until one is configured. A cached token still works "
            f"without it.")
    return out
