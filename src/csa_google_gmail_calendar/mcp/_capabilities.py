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
    # --- Gmail reads (task 11, `_tools/mail_read.py`) - policy.MAIL_READ ---
    "search_messages": "mail.read",
    "get_message": "mail.read",
    "get_thread": "mail.read",
    "list_threads": "mail.read",
    "get_attachment": "mail.read",
    "list_labels": "mail.read",
    "list_drafts": "mail.read",
    "get_draft": "mail.read",
    # --- Gmail organising/composing (task 11, `_tools/mail_write.py`) - policy.MAIL_WRITE ---
    "create_draft": "mail.write",
    "update_draft": "mail.write",
    "delete_draft": "mail.write",  # irreversible, but MAIL_WRITE per policy._GATES's own note
    "modify_message_labels": "mail.write",
    "modify_thread_labels": "mail.write",
    "archive_email": "mail.write",
    "archive_thread": "mail.write",
    "mark_read": "mail.write",
    "mark_unread": "mail.write",
    "create_label": "mail.write",
    "trash_email": "mail.write",
    "trash_thread": "mail.write",
    "untrash_email": "mail.write",
    "untrash_thread": "mail.write",
    "mark_spam": "mail.write",
    "unmark_spam": "mail.write",
    # --- Gmail sending (task 11, `_tools/mail_send.py`) - policy.MAIL_SEND ---
    "send_message": "mail.send",
    "send_draft": "mail.send",
    "reply": "mail.send",
    "reply_all": "mail.send",
    "forward": "mail.send",
    # --- Calendar reads (task 12, `_tools/calendar_read.py`) - policy.CALENDAR_READ ---
    "list_calendars": "calendar.read",
    "list_events": "calendar.read",
    "get_event": "calendar.read",
    "find_free_time": "calendar.read",  # calls Backend.query_freebusy, gated CALENDAR_READ
    # --- Calendar writes (task 12, `_tools/calendar_write.py`) - policy.CALENDAR_WRITE ---
    "create_event": "calendar.write",
    # tool name differs from the Backend method it calls (like archive_email->archive_message
    # above): "reschedule_event", not "update_event" - fix round 1 (CINO 2026-09-26), ADR-001:
    # a tool name is a claim made to a reader who cannot check it, and this tool moves an
    # event's time only, nothing else about it.
    "reschedule_event": "calendar.write",
    "respond_to_event": "calendar.write",
    # --- Calendar delete (task 12, `_tools/calendar_write.py`) - policy.CALENDAR_DELETE,
    # OFF by default (see policy.DEFAULT_ENABLED) ---
    "delete_event": "calendar.delete",
}


def reachable_capabilities() -> frozenset[str]:
    """Capabilities some tool in this server can actually require."""
    return frozenset(c for c in TOOL_CAPABILITIES.values() if c is not None)
