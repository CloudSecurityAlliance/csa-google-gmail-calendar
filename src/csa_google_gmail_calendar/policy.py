"""Capabilities, and the seam that enforces them.

The seam is `PolicyBackend`, which wraps a `Backend` and refuses BEFORE delegating. This is
the rule csa-zendesk states as "the library is callable without the server": controls live at
the Backend, never in the MCP layer, so an embedder using the library directly gets the same
refusals a model does.

Default posture (spec §3): reads and reversible writes on; anything that changes who has
access or persists after the agent stops, off. One rule, statable in a sentence, surviving
new methods being added.

**Fail closed.** `_GATES` must name every `Backend` method. An unlisted name is refused rather
than delegated, so adding a method to the protocol without deciding its gate is a loud failure
at first call, not a silent hole. `tests/test_policy.py` asserts the coverage so it fails in CI
instead.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .exceptions import PolicyError

MAIL_READ = "mail.read"
MAIL_WRITE = "mail.write"        # draft CRUD, label application, trash/untrash, spam
MAIL_SEND = "mail.send"          # outbound - Google gives it its own scope
# The 3 permanent-destroy Gmail methods whose ONLY scope is bare `https://mail.google.com/`:
# messages.delete, messages.batchDelete, threads.delete. Trash is not among them - trashing
# needs only gmail.modify, which is why it lives in MAIL_WRITE instead. No Backend method in
# this plan implements any of the three (see Task list), so nothing gates on MAIL_DELETE yet -
# it is defined and reserved rather than wired up, and stays out of DEFAULT_ENABLED so a future
# task that adds one of these three does not accidentally inherit an "on" default it never
# argued for.
MAIL_DELETE = "mail.delete"
CALENDAR_READ = "calendar.read"
CALENDAR_WRITE = "calendar.write"
CALENDAR_DELETE = "calendar.delete"                                     # OFF by default

ALL_CAPABILITIES: tuple[str, ...] = (
    MAIL_READ, MAIL_WRITE, MAIL_SEND, MAIL_DELETE,
    CALENDAR_READ, CALENDAR_WRITE, CALENDAR_DELETE,
)

# Everything except the two that destroy. `mail.delete` is off per spec §3; `calendar.delete`
# is OUR line, not Google's — no scope separates events.delete from events.patch, so the CI
# scope test cannot verify this one and skips it by name.
DEFAULT_ENABLED: frozenset[str] = frozenset(ALL_CAPABILITIES) - {MAIL_DELETE, CALENDAR_DELETE}

IRREVERSIBLE: frozenset[str] = frozenset({MAIL_SEND, MAIL_DELETE, CALENDAR_DELETE})


class Gate:
    """What a Backend method costs. `capability=None` names a method this project has decided
    needs no capability check at all (nothing currently uses it - see the fix-round-2 note on
    `_GATES` below for why every read is gated by name instead)."""
    __slots__ = ("capability",)

    def __init__(self, capability: str | None) -> None:
        self.capability = capability


# Fix round 2 (auth.py task-9-report.md): reads used to be `Gate(None)` - allowed regardless of
# which capabilities were enabled, on the theory that a read is inherently low-risk. That
# stopped being true the moment `auth.scopes_for` started tying an actual OAuth SCOPE to
# MAIL_READ/CALENDAR_READ: `CSA_GGC_CAPABILITIES=mail.send` (excluding `mail.read`) advertised
# every mail-read tool at the policy layer while consenting to none of the scopes those tools
# need, so a call that "policy allows" reached Google and 403'd anyway - a worse failure than a
# clean, actionable `PolicyError` naming the capability to enable. Gating every read by its own
# capability closes that gap and costs nothing in the default deployment: `MAIL_READ` and
# `CALENDAR_READ` are both in `DEFAULT_ENABLED`, so nobody who has not deliberately narrowed
# their capability set sees any change in behaviour.
_GATES: dict[str, Gate] = {
    # --- mail reads ---
    "search_messages": Gate(MAIL_READ), "get_message": Gate(MAIL_READ),
    "get_thread": Gate(MAIL_READ), "list_threads": Gate(MAIL_READ),
    "get_attachment": Gate(MAIL_READ), "list_labels": Gate(MAIL_READ),
    "list_drafts": Gate(MAIL_READ), "get_draft": Gate(MAIL_READ),
    "list_history": Gate(MAIL_READ), "get_profile": Gate(MAIL_READ),
    # --- mail reversible writes ---
    "create_draft": Gate(MAIL_WRITE), "update_draft": Gate(MAIL_WRITE),
    # DEVIATION from the brief and from spec §3's own table (which puts this behind
    # MAIL_DELETE): the spec's own posture rule is "reads and reversible writes on; anything
    # that changes who has access or persists after the agent stops, off". Deleting an unsent
    # draft changes nobody's access and persists nothing once gone - by that rule it is ON,
    # not off. The table's placement is Google's fault line, not ours: it puts `drafts.delete`
    # behind `mail.delete` only because Google itself gates it no tighter than composing
    # (`gmail.compose`), and the spec's derivation section (§3) inherited that boundary as a
    # floor rather than re-deriving it from the posture rule. Keeping it behind MAIL_DELETE
    # would mean a person has to enable permanent destruction of RECEIVED mail just to tidy a
    # draft they wrote themselves - a gate that only pushes people to open a larger gate is
    # worse than no gate at all. `delete_draft` IS irreversible (Google's own docs: "Immediately
    # and permanently deletes... Does not simply trash it"), and that fact is not silently
    # dropped - it is handled by telling the model (spec §6), and a later task annotates this
    # tool DESTRUCTIVE and says in its description that it destroys rather than trashes. (CINO,
    # 2026-09-25.)
    "delete_draft": Gate(MAIL_WRITE),
    "modify_message_labels": Gate(MAIL_WRITE), "modify_thread_labels": Gate(MAIL_WRITE),
    "archive_message": Gate(MAIL_WRITE), "archive_thread": Gate(MAIL_WRITE),
    "mark_read": Gate(MAIL_WRITE), "mark_unread": Gate(MAIL_WRITE),
    "trash_message": Gate(MAIL_WRITE), "trash_thread": Gate(MAIL_WRITE),
    "untrash_message": Gate(MAIL_WRITE), "untrash_thread": Gate(MAIL_WRITE),
    "mark_spam": Gate(MAIL_WRITE), "unmark_spam": Gate(MAIL_WRITE),
    "create_label": Gate(MAIL_WRITE),
    # --- outbound ---
    # `send_draft` is MAIL_SEND, not MAIL_WRITE: Google gates it at compose level, and spec §3
    # overrides that deliberately because both it and `send_message` put mail on the wire.
    "send_message": Gate(MAIL_SEND), "send_draft": Gate(MAIL_SEND),
    "reply_message": Gate(MAIL_SEND), "reply_all_message": Gate(MAIL_SEND),
    "forward_message": Gate(MAIL_SEND),
    # --- calendar ---
    "list_calendars": Gate(CALENDAR_READ), "get_calendar": Gate(CALENDAR_READ),
    "list_events": Gate(CALENDAR_READ), "get_event": Gate(CALENDAR_READ),
    "query_freebusy": Gate(CALENDAR_READ),
    "create_event": Gate(CALENDAR_WRITE), "update_event": Gate(CALENDAR_WRITE),
    "respond_to_event": Gate(CALENDAR_WRITE),
    "delete_event": Gate(CALENDAR_DELETE),
}


class Policy:
    def __init__(self, enabled: frozenset[str] = DEFAULT_ENABLED) -> None:
        unknown = set(enabled) - set(ALL_CAPABILITIES)
        if unknown:
            raise ValueError(f"unknown capability/capabilities: {sorted(unknown)}")
        self.enabled = frozenset(enabled)

    def allows(self, method: str) -> bool:
        gate = _GATES.get(method)
        if gate is None:
            return False          # fail closed: an ungated method is one nobody decided about
        return gate.capability is None or gate.capability in self.enabled

    def require(self, method: str) -> None:
        gate = _GATES.get(method)
        if gate is None:
            raise PolicyError(
                f"{method!r} has no gate, so this server will not call it. This is a bug: "
                f"every Backend method must be declared in policy._GATES.")
        if gate.capability is None or gate.capability in self.enabled:
            return
        capability = gate.capability
        # Two different reasons a capability can be refused, and they must not share wording:
        # one is that the SHIPPED DEFAULT already excludes it (MAIL_DELETE, CALENDAR_DELETE -
        # "off by default" is simply true of them); the other is that this DEPLOYMENT'S OWN
        # CONFIGURATION narrowed away something that ships on (MAIL_SEND is irreversible but
        # is in DEFAULT_ENABLED - see the ruling on that in policy.py's module docstring
        # history). Before this fix both cases got "off by default", which told an operator
        # who had deliberately narrowed MAIL_SEND that the refusal came from the shipped
        # default rather than from their own configuration - wrong, and confusing to debug.
        if capability not in DEFAULT_ENABLED:
            extra = (" This capability is off by default because the action it permits "
                      "cannot be undone.")
        elif capability in IRREVERSIBLE:
            extra = (" This capability is on by default; it has been disabled by this "
                      "deployment's own configuration, not by the shipped default.")
        else:
            extra = ""
        raise PolicyError(
            f"{method} needs the {capability!r} capability, which is not enabled. "
            f"Enabled: {sorted(self.enabled) or 'none'}. To enable it, set "
            f"CSA_GGC_CAPABILITIES to a comma-separated list including {capability!r}."
            + extra)


class PolicyBackend:
    """Refuses before delegating. Attribute access is intercepted so a Backend method added
    later is gated by construction rather than by somebody remembering to wrap it.

    **`_inner` is deliberately reachable, and that is not a bug.** `pb._inner` is a plain
    instance attribute; `__getattribute__` resolves it the normal way and `__getattr__` (which
    is where the gating lives) is never consulted, so `pb._inner.send_message(...)` runs
    completely unpoliced. This class enforces a boundary on the *call path* through
    `PolicyBackend` - it is a seam, not a sandbox around the object.

    Hiding `_inner` (name-mangling, a closure instead of an attribute, `__slots__` without
    exposing it) was considered and rejected. It would buy back no real authority: anyone
    holding a `PolicyBackend` instance is running in the same process, with the same source
    available, and could construct an unpoliced backend directly (`ApiBackend(...)`) instead
    of reaching around this wrapper - hiding the attribute does not remove that path, it only
    makes this class LOOK like an isolation boundary when it is not one. That is worse than
    the current state: a future contributor who "fixes" the visible attribute would be
    papering over a gap that is still there, and the appearance of a stronger guarantee is
    itself a security regression, since it invites relying on containment this class was
    never built to provide.

    The threat this project actually models is a *model* calling *tools* through this
    library - and a tool call cannot do arbitrary Python attribute access to reach `_inner`.
    Code that already has a Python reference to the object and chooses to read `_inner` is
    not the model; it is whoever embeds this library, and that party could bypass the policy
    entirely by never constructing a `PolicyBackend` in the first place.
    """

    def __init__(self, inner: Any, policy: Policy) -> None:
        self._inner = inner
        self._policy = policy

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # Dunders and our own private attributes never forward to `inner`. This also makes
        # attribute lookups on a not-yet-`__init__`-ed instance (e.g. `self._inner` read from
        # inside this same method before `__init__` has run) fail with a plain AttributeError
        # instead of recursing back into `__getattr__` looking for `_inner`.
        if name.startswith("_"):
            raise AttributeError(name)
        inner_attr = getattr(self._inner, name)
        if not callable(inner_attr):
            return inner_attr

        def gated(*args: Any, **kwargs: Any) -> Any:
            self._policy.require(name)
            return inner_attr(*args, **kwargs)

        gated.__name__ = name
        return gated
