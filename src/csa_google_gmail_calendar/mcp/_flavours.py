"""`CSA_GGC_FLAVOUR=core|google|full` - which tools are **registered**, not which refuse.

Same mechanism `csa-google-workspace` uses: a flavour is a registration-time filter applied
*after* every `register_*_tools` call and *after* `policy.Policy` has already decided which
tools a call to `register_*_tools` even attempts to add (`server.py` composes the two - policy
first, flavour second, both absences rather than refusals). This module owns only "which names
does this flavour keep", never "should this deployment be allowed to reach a capability at all"
- that question is `policy.Policy`'s, unchanged by anything here.

## The three flavours, and what each means

**`full`** (the default) - no restriction. Every tool the current `Policy` and the current
capability set would register stays registered.

**`core`** - spec §5's 31-tool minimum, "what email needs to work". Calendar carries none of
it; a `core` deployment is Gmail-only, by the spec's own derivation, not an oversight here.

**`google`** - the tools Google's own official surface (`gmailmcp.googleapis.com` +
`calendarmcp.googleapis.com`) publishes, captured in `research/captures/2026-09-01-*.json` and
checkable against those files directly (spec §5, "Flavours"). **This is a conservative,
literal-name intersection, not a semantic mapping**: a tool here is included only when this
project's OWN name for an operation is spelled identically to the capture's name for the same
operation. Google's captured surface does not expand `users.messages.modify` into distinct
`archive_email`/`trash_email`/`mark_spam` tools the way this project does (ADR-001), does not
send mail at all, and names its calendar move `update_event` where this project's is
`reschedule_event` (see that tool's own docstring on why the rename happened) and its free-time
finder `suggest_time` where this project's is `find_free_time`. None of those match by name, so
none of them appear under `google` even though an equivalent OPERATION is published - the safe
direction, since under-representing what `google` covers is a completeness gap a reader can
notice and go verify against the capture, where over-representing it would be a false claim of
alignment. Documented here, in `describe_configuration`'s own output, and in the README, so
nobody has to rediscover it by diffing tool lists by hand.

## What is exempt from every flavour

`authenticate`/`auth_status`/`logout` (no deployment can be locked out of its own login by a
flavour choice) and the configuration surface this task adds
(`describe_configuration`/`demonstration_plan`/`report_a_problem`/`whoami`) - none of the four
is in spec §5's 31 or in Google's published surface, all four are meta/introspection tools with
no mail or calendar authority of their own, and an operator who has narrowed a deployment to
`core` still needs to see what is configured, run the demo, report a problem, and learn the
account's own address. `ALWAYS_REGISTERED` names this set once so the reasoning is not repeated,
or drifted, at each call site.

**The name over-promises by one member (fix round, final whole-branch review, CINO
2026-09-26).** Despite its name, membership here is NOT "always registered, full stop" - it is
"exempt from FLAVOUR filtering specifically", which is all this module ever decides (see this
docstring's own opening line). Six of the seven members are ALSO gated `None` in
`TOOL_CAPABILITIES` (`_capabilities.py`), so for them the two properties coincide and the name
reads as literally true. `whoami` is the exception: it is capability-gated (the same
`policy.MAIL_READ` gate as `get_profile`, whose Backend method it calls - see
`_tools/mail_read.py`), so narrowing `CSA_GGC_CAPABILITIES` to exclude `mail.read` makes
`whoami` disappear at registration time, flavour untouched. Documented here rather than
renamed, to avoid a wider, purely-cosmetic diff across `_tools/__init__.py`,
`test_config_tools.py` and this module's own call sites for a distinction the docstring above
already draws correctly - only the constant's NAME reads wider than its actual guarantee.
"""
from __future__ import annotations

from collections.abc import Mapping

from ._capabilities import TOOL_CAPABILITIES

FLAVOUR_VAR = "CSA_GGC_FLAVOUR"
DEFAULT_FLAVOUR = "full"

KNOWN_FLAVOURS: frozenset[str] = frozenset({"core", "google", "full"})

ALWAYS_REGISTERED: frozenset[str] = frozenset({
    "authenticate", "auth_status", "logout",
    "describe_configuration", "demonstration_plan", "report_a_problem", "whoami",
})

# spec §5, "The minimum set: what email needs to work" - 31 tools in six tiers. Kept as one
# frozenset (not split by tier) because nothing downstream needs the tier boundary, only
# membership; the tier comments are for a human checking this against the spec table, not for
# any code here.
CORE_TOOLS: frozenset[str] = frozenset({
    # Reading (6)
    "search_messages", "get_message", "get_thread", "list_threads", "get_attachment",
    "list_labels",
    # Composing (6)
    "create_draft", "update_draft", "get_draft", "list_drafts", "delete_draft", "send_draft",
    # Sending (4)
    "send_message", "reply", "reply_all", "forward",
    # Organising (7)
    "archive_email", "archive_thread", "modify_message_labels", "modify_thread_labels",
    "mark_read", "mark_unread", "create_label",
    # Disposal (6)
    "trash_email", "trash_thread", "untrash_email", "untrash_thread", "mark_spam",
    "unmark_spam",
    # Keeping up (2)
    "list_history", "get_profile",
})
# An import-time sanity check against a typo silently shrinking/growing the set, not a
# security control - equivalent precedent already in this codebase (mail.py's own).
assert len(CORE_TOOLS) == 31, "spec §5 names exactly 31 tools in the core minimum set"  # nosec B101

# The names captured live from Google's own servers on 2026-09-01 (see the module docstring's
# "google" section for why membership here is a literal spelling match, not a semantic one).
_GMAIL_CAPTURE: frozenset[str] = frozenset({
    "apply_sensitive_message_label", "apply_sensitive_thread_label", "create_draft",
    "create_label", "get_draft", "get_message", "get_thread", "label_message", "label_thread",
    "list_drafts", "list_labels", "mark_message_spam", "mark_thread_spam", "search_threads",
    "trash_message", "trash_thread", "unlabel_message", "unlabel_thread", "unmark_message_spam",
    "unmark_thread_spam", "untrash_message", "untrash_thread", "update_message_labels",
})
_CALENDAR_CAPTURE: frozenset[str] = frozenset({
    "create_event", "delete_event", "get_event", "list_calendars", "list_events",
    "respond_to_event", "search_events", "suggest_time", "update_event",
})
GOOGLE_TOOLS: frozenset[str] = frozenset(TOOL_CAPABILITIES) & (_GMAIL_CAPTURE | _CALENDAR_CAPTURE)


def flavour_from_env(env: Mapping[str, str]) -> str:
    """`CSA_GGC_FLAVOUR` - unset means `full`. Raises for anything not in `KNOWN_FLAVOURS`,
    the same "fail loudly on a typo" rule `_config.policy_from_env` applies to
    `CSA_GGC_CAPABILITIES` - a misspelled flavour that silently behaved like `full` would be a
    much quieter failure than one that stops the server from starting."""
    raw = (env.get(FLAVOUR_VAR) or DEFAULT_FLAVOUR).strip().lower()
    if raw not in KNOWN_FLAVOURS:
        raise ValueError(
            f"{FLAVOUR_VAR} contains unknown value {raw!r}. Known flavours: "
            f"{', '.join(sorted(KNOWN_FLAVOURS))}.")
    return raw


def _base_for(flavour: str) -> frozenset[str]:
    if flavour == "core":
        return CORE_TOOLS
    if flavour == "google":
        return GOOGLE_TOOLS
    return frozenset(TOOL_CAPABILITIES)  # "full": every declared tool name is eligible


def allowed_tool_names(flavour: str, registered: frozenset[str]) -> frozenset[str]:
    """Which of the ALREADY-REGISTERED tool names (i.e. already filtered by `Policy` - see the
    module docstring) this flavour keeps. Raises for an unrecognised flavour rather than
    silently behaving like `full` - see `flavour_from_env` for why that matters, restated here
    because `server.py` can also be called directly (tests do) with a flavour string that never
    passed through `flavour_from_env` at all."""
    if flavour not in KNOWN_FLAVOURS:
        raise ValueError(
            f"unknown flavour {flavour!r}. Known flavours: {', '.join(sorted(KNOWN_FLAVOURS))}.")
    if flavour == "full":
        return registered
    return (_base_for(flavour) | ALWAYS_REGISTERED) & registered


def hidden_by_flavour(flavour: str, registered: frozenset[str]) -> frozenset[str]:
    """The registered names this flavour additionally removes - what `describe_configuration`
    reports as "what this flavour is hiding" (spec §6: "what this deployment is hiding and
    why" has no home in a per-tool description, so it is said at the server level instead)."""
    return registered - allowed_tool_names(flavour, registered)
