"""`describe_configuration` - task 13's answer to the bug found in the sibling project
(`csa-google-workspace`): a model read that tool, planned work on the strength of it, and only
then discovered the tools it named did not exist. See `_capabilities.py`'s own module docstring
for the fuller account and the fail-closed tests that now guard against it here.

## What this reports, and why each field is there

**Enabled capabilities** (`policy.enabled`) and **registered tools** - the two halves of "what
can this deployment actually reach right now", read straight off the live `Policy` and the live
`MCPServer._tool_manager` rather than recomputed by a second, hand-maintained copy that could
drift from either.

**Required scopes** and **granted scopes** are deliberately two different lists.
`required_scopes` is what the ENABLED capabilities need (`Settings.required_scopes`, already the
one source of truth `authenticate`/`auth_status` both read); `granted_scopes` is what a cached
credential, if one exists, actually CARRIES - read directly off the token file's own `scopes`
list, never assumed equal to what is required. They can differ in both directions: a credential
issued before a capability was enabled is short (`scopes_sufficient=False`, the same state
`auth_status` calls `scope_short`); one issued under a broader `Policy` that was later narrowed
still carries scopes this deployment no longer asks for.

**The flavour, and what it hides.** `hidden_by_capability` names tools absent because their
gate is not in `policy.enabled` - independent of flavour, the same absence a capability-disabled
deployment always has. `hidden_by_flavour` names tools that WOULD be registered under the
current capability set but the active flavour additionally removes (`_flavours.py`) - this is
the field spec §5 means by "a flavour says what it is hiding". Reported as two separate lists
rather than one merged "everything missing" list because they answer different operator
questions ("enable a capability" versus "change `CSA_GGC_FLAVOUR`") and merging them would lose
that distinction.

**The attachment/download directories - path, never contents.** `CSA_GGC_ATTACH_DIR`'s and
`CSA_GGC_DOWNLOAD_DIR`'s configured paths are reported because a refusal from
`send_message`/`get_attachment` respectively would already disclose them (`_attachments.py`'s
own refusal messages echo the configured root); nothing about a file UNDER either directory -
not a name, not a byte - is read or reported here. The two are always reported separately and
are never the same directory - `_attachments.check_directories_disjoint` refuses at server
construction if they are ever configured to overlap (see that module's docstring for why one
directory cannot safely serve both directions).

## What this deliberately never says

No token path, no token contents, no client-secrets path or contents. `auth_status` already
owns the token-path/credential-freshness question and has its own, separately-decided
disclosure bar (it names `token_path` because a local file PATH is not the credential); this
tool's bar is stricter on purpose, because its output is the one a model is most likely to
quote back into a plan, or an operator to paste whole into a support ticket or a public issue -
see `report_a_problem`'s own docstring for the same discipline applied to a report instead of a
snapshot.
"""
from __future__ import annotations

import os
from typing import Any

from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials
from mcp.server import MCPServer

from ... import auth
from ..._attachments import AttachmentPolicy, DownloadPolicy
from ...policy import ALL_CAPABILITIES
from .._capabilities import TOOL_CAPABILITIES
from .._config import Settings
from .._flavours import GOOGLE_TOOLS, hidden_by_flavour
from ._base import LOCAL_READ, tool


def _granted_scopes(token_path: str) -> list[str] | None:
    """`None` when no credential is cached yet - distinct from `[]`, which would claim a
    credential exists and was granted nothing. No network call: `Credentials.from_authorized_user_file`
    is a local JSON read, the same primitive `auth_status`'s own no-network check uses.

    `OSError` alongside `ValueError`/`GoogleAuthError`: `os.path.exists` returning `True` above
    does not guarantee the file is still readable a moment later (permission changed, deleted
    in a race, or simply unreadable) - an uncaught `OSError` would propagate as a raw traceback
    that names `token_path`, exactly the value this tool's own docstring promises never to
    return."""
    if not os.path.exists(token_path):
        return None
    try:
        creds = Credentials.from_authorized_user_file(token_path)
    except (ValueError, GoogleAuthError, OSError):
        return None
    return sorted(creds.scopes or [])


def register_config_tools(app: MCPServer, settings: Settings, flavour: str,
                          attach_policy: AttachmentPolicy | None,
                          download_policy: DownloadPolicy | None = None) -> None:
    @tool(app, annotations=LOCAL_READ)
    def describe_configuration() -> dict[str, Any]:
        """What this deployment has enabled, what it is hiding and why, and whether its cached
        credential currently covers what it claims to enable. Call this before planning work
        that assumes a particular tool or scope exists, and before telling a user this server
        "cannot" do something - a capability that is merely disabled says so here, with the
        environment variable that would turn it on.

        `hidden_by_capability` lists tools absent because their capability is not in
        `capabilities_enabled` - enable it via `CSA_GGC_CAPABILITIES` to get them back.
        `hidden_by_flavour` lists tools this deployment's `CSA_GGC_FLAVOUR` additionally
        removes on top of that - a tool listed here would exist under `full`. Neither list
        means "this server cannot do this"; both mean "not configured to, right now".

        `granted_scopes` is `null` when no credential is cached yet - call `auth_status` for
        the fuller three-state answer (`no_credential`/`scope_short`/`ready`) `authenticate`
        acts on; this field is a scope-level summary, not a replacement for that tool.

        Never returns a token path, token contents, or a client-secrets path - only scope
        identifiers (not secrets) and the attachment/download directories' configured PATHs,
        which a refusal from `send_message`/`get_attachment` would disclose anyway."""
        policy = settings.policy
        registered = frozenset(t.name for t in app._tool_manager.list_tools())
        cap_allowed = frozenset(name for name, cap in TOOL_CAPABILITIES.items()
                                if cap is None or cap in policy.enabled)
        required = settings.required_scopes
        granted = _granted_scopes(settings.token_path)
        scopes_sufficient = (None if granted is None
                            else not auth.needs_reconsent(granted, required))
        attachment_directory = (str(attach_policy.root)
                                if attach_policy is not None and attach_policy.root is not None
                                else None)
        download_directory = (str(download_policy.root)
                              if download_policy is not None and download_policy.root is not None
                              else None)
        return {
            "flavour": flavour,
            "flavour_note": (
                "'core' is the 31-tool minimum spec §5 derives as what email needs to work "
                "(Gmail only, no Calendar). 'google' is a conservative, literal tool-name "
                "match against what Google's own gmailmcp/calendarmcp servers publish "
                "(research/captures/2026-09-01-*.json) - it under-represents rather than "
                "over-claims alignment where this project's tool names differ from theirs "
                "for the same operation (see _flavours.py). 'full' (the default) restricts "
                "nothing beyond capabilities."),
            "capabilities_enabled": sorted(policy.enabled),
            "capabilities_available": sorted(ALL_CAPABILITIES),
            "required_scopes": required,
            "granted_scopes": granted,
            "scopes_sufficient": scopes_sufficient,
            "attachment_directory": attachment_directory,
            "download_directory": download_directory,
            "registered_tools": sorted(registered),
            "hidden_by_capability": sorted(set(TOOL_CAPABILITIES) - cap_allowed),
            "hidden_by_flavour": sorted(hidden_by_flavour(flavour, cap_allowed)),
            "google_flavour_tool_names": sorted(GOOGLE_TOOLS),
        }
