"""`demonstration_plan` - an ordered sequence exercising the tools THIS deployment has
registered, for a model (or a person) to carry out by calling the named tools itself, in order.

**It returns the plan rather than running it**, `csa-google-workspace`'s own precedent
(`src/csa_google_workspace/mcp/_tools/demo.py`) and for the identical reason: handing back an
ordered plan makes the model call the real tools and narrate as it goes, which is a better
demonstration (a conversation, not a black box) and a better test (it exercises whether the
tool descriptions are good enough to use correctly from a standing start - something no unit
test can check).

## The one rule that overrides everything else here

**Every send in this plan targets the authenticated user's own address, never an address from
an argument.** `whoami` is how the plan learns that address; every step whose arguments would
otherwise need a recipient uses the literal placeholder string `"<the authenticated user>"` -
never a real address baked into this module, and never a value read from anywhere this plan's
own caller could influence. `reply`/`reply_all` need no such placeholder at all: this plan
first sends a message TO that placeholder address (`send_message`), so the original message's
own From/To/Cc - what `reply`/`reply_all` resolve their recipients from - already contains
nothing but that same address. `forward`, which has no implicit recipient (`to` is a required
argument on that tool, unlike `reply`), uses the placeholder explicitly for the same reason.
`tests/test_demo.py` pins this: every step whose tool takes a `to`/`cc`/`bcc` argument that is
present in this plan's own `arguments` dict must be exactly the placeholder, for every
registered tool, not only the three the brief's own illustrative test names.

**A step that NEEDS `whoami` (or any other step's output) is dropped, not merely warned about,
when that dependency is not available.** `send_message`/`create_event` need the resolved
address; `forward` needs both that and the original message; `reply`/`reply_all`/the
organising/disposal steps need the original message; `send_draft` needs a draft id from
`create_draft`. Each catalogue entry below declares its own `requires` - the OTHER tool names
whose output it depends on - and `demonstration_plan` walks the catalogue in order, including a
step only when its own tool is registered AND every tool it requires was ALREADY included
earlier in this same pass. A deployment configured `CSA_GGC_CAPABILITIES=mail.send` (which
registers `send_message`/`forward`/`reply`/`reply_all`/`send_draft` but not `whoami` or
`create_draft`, both gated by capabilities `mail.send` does not enable) therefore ends up with
NONE of those steps in its plan - not a plan carrying an unresolvable placeholder for a model to
guess at, which is the exact failure this whole file exists to prevent on a send tool.
Dependency-dropped steps are reported in `skipped`, distinct from `unavailable` (registered vs.
not) - see `tests/test_demo.py`'s dedicated `mail.send`-only test.

## Safe to run twice, and what it cleans up

Every id this plan creates comes from an EARLIER STEP IN THE SAME RUN - never a stale id left
over from a previous run - so nothing here can collide with what a prior run left behind, except
the one thing this server currently has no way to remove: a created LABEL (there is no
`delete_label` tool - a known, documented gap, see `README.md`'s structural absences and
`TODO.md`). This plan's own label name is timestamped at the moment the plan is GENERATED
(`run_id`, computed once per call, not once per process), so a second run creates a second,
differently-named label rather than colliding with (or silently reusing) the first.

**Cleanup is real, not merely demonstrated.** The trash/untrash pair on the self-sent message
and its thread stays in the catalogue because it demonstrates BOTH tools working - it restores
the thread, it does not clean it up. A separate, FINAL set of steps at the end of the mail
section trashes everything this run actually created: the self-sent thread (covers the
`send_message`/`reply`/`reply_all` messages together), the `forward` copy (its own, separate
thread), and the message `send_draft` sent (also its own thread) - three `trash_*` calls with no
`untrash` after them. `mail_cleanup_possible` reports whether those three tools are even
registered (they need `mail.write`, which a `mail.send`-only deployment does NOT enable even
though it can still send) - if false, `advice` says explicitly that every message this plan
sends will remain in the mailbox, uncleanable through this server. The scratch calendar event is
deleted if `calendar.delete` is enabled (`cleanup_possible`) and flagged as needing manual
deletion otherwise. The one thing that is NEVER cleaned up, under any capability set, is the
label - stated plainly in `advice`, not left for someone to discover by counting labels after a
few runs.

## `get_attachment`, and the fixture it needs

The `send_message` step attaches a small local file (a placeholder path under
`CSA_GGC_ATTACH_DIR`), so the demo CREATES its own fixture instead of hoping the real mailbox
happens to have one lying around. A `get_message` step reads that same self-sent message back
to learn the attachment's id and filename, and `get_attachment` downloads it - into
`CSA_GGC_DOWNLOAD_DIR`, a DIFFERENT directory from `CSA_GGC_ATTACH_DIR` (the two must not
overlap; see `_attachments.py`'s module docstring for why). If `CSA_GGC_ATTACH_DIR` is not
configured, `send_message` itself would refuse (naming that variable) before any of this
runs; if `CSA_GGC_DOWNLOAD_DIR` is not configured, `get_attachment` refuses the same way,
naming that variable instead - `advice` says so, so the executor can skip straight to the
next step rather than getting stuck on a refusal it could have predicted.

## What is not demonstrated, and why - `_NOT_DEMONSTRATED`

Three tools are deliberately absent from `_catalogue`, each with its own one-line reason kept in
`_NOT_DEMONSTRATED` rather than only in prose here: `authenticate` (useful on demand, not as a
forced step - `auth_status`, which IS exercised, already says whether it is needed),
`logout` (would end the session's own credential mid-demo - the one action a demonstration must
never take on somebody's behalf), and `demonstration_plan` itself (naming itself in its own plan
is circular). `tests/test_demo.py` asserts every OTHER registered tool appears in `_catalogue`,
so a tool added later and simply forgotten here fails that test rather than silently widening
this set - the same shape as `scopes.py`'s `UNRANKED`, `policy.MAIL_DELETE`'s allow-list, and
`test_mcp_capabilities.py`'s `_EXPECTED_OPEN_WORLD_HINT` map.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, NamedTuple, TypedDict

from mcp.server import MCPServer

from ._base import LOCAL_READ, tool

SELF = "<the authenticated user>"

# Tools deliberately absent from `_catalogue`, each with its own reason - see the module
# docstring's "What is not demonstrated" section. `tests/test_demo.py` asserts every OTHER
# registered tool is named in `_catalogue`, so this dict is the one place a future omission has
# to be written down rather than silently growing.
_NOT_DEMONSTRATED: dict[str, str] = {
    "authenticate": "useful on demand, not as a forced step - auth_status (which IS "
                    "exercised) already says whether it is needed.",
    "logout": "would end the session's own credential mid-demo - the one action a "
             "demonstration must never take on somebody's behalf.",
    "demonstration_plan": "naming itself in its own plan is circular.",
}


class DemoStep(TypedDict):
    step: int
    tool: str
    arguments: dict[str, Any]
    why: str


class _CatalogueEntry(NamedTuple):
    """One candidate step. `requires` names OTHER TOOLS (not steps - a tool can appear more
    than once in the catalogue, and any one successful inclusion satisfies every dependent)
    whose output this step's placeholders assume exists. `demonstration_plan` only includes an
    entry once its own tool is registered AND every name in `requires` was already included
    earlier in the same pass - see the module docstring's dependency-awareness section."""
    tool: str
    arguments: dict[str, Any]
    why: str
    requires: frozenset[str] = frozenset()


def _step(tool: str, arguments: dict[str, Any], why: str,
         *, requires: tuple[str, ...] = ()) -> _CatalogueEntry:
    return _CatalogueEntry(tool, arguments, why, frozenset(requires))


def _catalogue(run_id: str) -> list[_CatalogueEntry]:
    """The full candidate step list, in the order a walkthrough should attempt them. Not every
    registered tool needs to appear (see the module docstring on `_NOT_DEMONSTRATED`);
    `demonstration_plan` filters this against what is ACTUALLY registered and against each
    entry's own `requires`, so a narrower policy or flavour simply sees a shorter plan, never a
    step naming a tool that does not exist or referring to a placeholder no earlier step could
    have produced (`tests/test_demo.py` pins both).

    `run_id` disambiguates only the one thing this plan creates that nothing can delete again
    (a label - see the module docstring); every other placeholder value below is deliberately a
    fixed, readable string, since nothing about repeatability depends on it."""
    label_name = f"csa-demo-{run_id}"
    tomorrow = "<tomorrow, 09:00, RFC3339, e.g. 2026-10-01T09:00:00Z>"
    tomorrow_end = "<tomorrow, 09:30, RFC3339>"
    rescheduled_start = "<tomorrow, 10:00, RFC3339>"
    rescheduled_end = "<tomorrow, 10:30, RFC3339>"
    window_start = "<today, 00:00, RFC3339>"
    window_end = "<the day after tomorrow, 00:00, RFC3339>"

    return [
        _step("auth_status", {},
             "Confirm a usable credential is cached before touching anything else."),
        _step("whoami", {},
             f"Learn the address every send below targets - substitute it for every "
             f"{SELF!r} placeholder in this plan."),
        _step("describe_configuration", {},
             "See which capabilities, scopes and flavour this deployment has enabled, and "
             "what it is hiding, before assuming a step below will work."),
        _step("list_labels", {}, "See what labels already exist."),
        _step("list_calendars", {}, "See which calendars this account can reach."),
        _step("search_messages", {"query": "in:inbox", "limit": 5},
             "Read tier: confirm search works against the real mailbox."),
        _step("list_threads", {"limit": 5}, "Read tier: list a handful of real conversations."),
        _step("get_thread", {"thread_id": "<a thread id from the list_threads result above>"},
             "Read one conversation's summaries.", requires=("list_threads",)),
        _step("get_message",
             {"message_id": "<a message id from the search_messages result above>"},
             "Read one message's full content.", requires=("search_messages",)),
        _step("list_drafts", {}, "Preview whatever drafts already exist."),
        _step("create_draft", {"to": [SELF], "subject": "CSA demo draft (safe to delete)",
                               "body": "Created by csa-google-gmail-calendar's "
                                       "demonstration_plan. Safe to delete."},
             "Compose tier: a rehearsal draft, self-addressed."),
        _step("get_draft", {"draft_id": "<the draft id from create_draft above>"},
             "Confirm the draft's own content.", requires=("create_draft",)),
        _step("update_draft", {"draft_id": "<the same draft id>", "to": [SELF],
                               "subject": "CSA demo draft (edited, safe to delete)",
                               "body": "Edited by the demonstration plan."},
             "Compose tier: edit it before sending or discarding.", requires=("create_draft",)),
        _step("delete_draft", {"draft_id": "<the same draft id>"},
             "Disposal of the rehearsal draft - permanent, but it is this plan's own "
             "throwaway content, never anything the user wrote.", requires=("create_draft",)),
        _step("create_draft", {"to": [SELF], "subject": "CSA demo send_draft (safe to delete)",
                               "body": "A second draft, created only to demonstrate "
                                       "send_draft."},
             "A fresh draft to demonstrate sending AS a draft, distinct from send_message "
             "below."),
        _step("send_draft", {"draft_id": "<the draft id from the create_draft immediately "
                                        "above>"},
             "Send an existing draft as-is - self-addressed, irreversible once sent.",
             requires=("create_draft",)),
        _step("send_message",
             {"to": [SELF], "subject": "CSA demo message (safe to delete)",
              "body": "Sent by csa-google-gmail-calendar's demonstration_plan, to itself.",
              "attachments": ["<a small local file already present under CSA_GGC_ATTACH_DIR "
                             "- if that variable is not configured, send_message will refuse "
                             "naming it; drop the attachments argument and skip the "
                             "get_attachment step below instead>"]},
             "Sending tier: a brand-new self-addressed message, carrying a small attachment "
             "so get_attachment below has something real to fetch. Its id and thread id feed "
             "every organising/disposal step below.", requires=("whoami",)),
        _step("get_message", {"message_id": "<the same message id from send_message above>"},
             "Read the self-sent message back to learn its attachment's id and filename - "
             "get_message never inlines attachment bytes, only the metadata get_attachment "
             "needs.", requires=("send_message",)),
        _step("get_attachment",
             {"message_id": "<the same message id from send_message above>",
              "attachment_id": "<the attachment_id from the get_message result above>",
              "filename": "<the matching filename from the same result>"},
             "Download the attachment just sent - the one attachment operation Gmail's API "
             "has; there is no upload tool because there is no attachments.upload endpoint to "
             "call.", requires=("send_message", "get_message")),
        _step("reply", {"message_id": "<the message id from send_message above>",
                       "body": "Demo reply - self-addressed because the original was."},
             "Sending tier: reply resolves its own recipient from the original message's "
             "From, which is the address send_message above just used - never a to argument "
             "here.", requires=("send_message",)),
        _step("reply_all", {"message_id": "<the same message id from send_message above>",
                            "body": "Demo reply-all - still self-addressed for the same "
                                    "reason."},
             "Sending tier: reply_all resolves recipients the same way reply does.",
             requires=("send_message",)),
        _step("forward", {"message_id": "<the same message id from send_message above>",
                         "to": [SELF],
                         "body": "Demo forward - self-addressed, unlike reply/reply_all this "
                                 "tool has no implicit recipient."},
             "Sending tier: forward, explicitly self-addressed since it takes a required to.",
             requires=("whoami", "send_message")),
        _step("create_label", {"name": label_name},
             "Organising tier: a label unique to this run (see the module docstring on why)."),
        _step("modify_message_labels",
             {"message_id": "<the message id from send_message above>",
              "add": ["<the label id from create_label above>"]},
             "Apply the new label to one message.", requires=("send_message", "create_label")),
        _step("modify_thread_labels",
             {"thread_id": "<the thread id from send_message's result above>",
              "add": ["<the same label id>"]},
             "Apply it to every message in that thread at once.",
             requires=("send_message", "create_label")),
        _step("mark_unread", {"message_id": "<the message id from send_message above>"},
             "Organising tier.", requires=("send_message",)),
        _step("mark_read", {"message_id": "<the same message id>"}, "Reverse of mark_unread.",
             requires=("send_message",)),
        _step("archive_email", {"message_id": "<the same message id>"},
             "Remove it from the Inbox view without trashing it.", requires=("send_message",)),
        _step("archive_thread", {"thread_id": "<the thread id from send_message's result "
                                             "above>"},
             "The same, applied to the whole thread.", requires=("send_message",)),
        _step("mark_spam", {"message_id": "<the same message id>"}, "Disposal tier.",
             requires=("send_message",)),
        _step("unmark_spam", {"message_id": "<the same message id>"},
             "Reverse of mark_spam - restores it.", requires=("send_message",)),
        _step("trash_email", {"message_id": "<the same message id>"},
             "Disposal tier: reversible - untrash_email is next.", requires=("send_message",)),
        _step("untrash_email", {"message_id": "<the same message id>"},
             "Demonstrates trash_email is reversible - restores the message, it does not "
             "clean it up (final cleanup is below).", requires=("send_message",)),
        _step("trash_thread", {"thread_id": "<the thread id from send_message's result "
                                           "above>"},
             "Disposal tier, thread level - reversible - untrash_thread is next.",
             requires=("send_message",)),
        _step("untrash_thread", {"thread_id": "<the same thread id>"},
             "Demonstrates trash_thread is reversible - restores the thread, it does not "
             "clean it up (final cleanup is below).", requires=("send_message",)),
        # --- Final cleanup: trashes what this run actually created. Not an "untrash" pair -
        # these are the last word on each thread, deliberately. See the module docstring's
        # "Safe to run twice" section for what this covers and what it does not (the label). --
        _step("trash_thread", {"thread_id": "<the thread id from send_message's result "
                                           "above>"},
             "FINAL CLEANUP: trash the self-sent thread for good - covers the send_message, "
             "reply and reply_all messages together.", requires=("send_message",)),
        _step("trash_email", {"message_id": "<the message id from forward's own result above>"},
             "FINAL CLEANUP: forward started its own, separate thread - trash it too.",
             requires=("forward",)),
        _step("trash_email",
             {"message_id": "<the message id from send_draft's own result above>"},
             "FINAL CLEANUP: send_draft also started its own, separate thread.",
             requires=("send_draft",)),
        _step("get_profile", {},
             "Keeping-up tier: the full mailbox summary, including the current history_id."),
        _step("list_history", {"start_history_id": "<the history_id from get_profile above>"},
             "Keeping-up tier: expect an empty (or near-empty) result immediately after "
             "get_profile - that means nothing changed in between, not that tracking is "
             "broken.", requires=("get_profile",)),
        _step("list_events", {"calendar_id": "primary", "limit": 5},
             "Calendar read tier: list a few real events."),
        _step("create_event",
             {"summary": f"CSA demo scratch event (safe to delete) {run_id}",
              "start": tomorrow, "end": tomorrow_end, "attendees": [SELF],
              "send_updates": "none"},
             "Calendar write tier: a scratch event with no attendee but the authenticated "
             "user themselves - never a stranger's address - and no notification, since there "
             "is nobody else to notify.", requires=("whoami",)),
        _step("get_event", {"event_id": "<the event id from create_event above>"},
             "Read the scratch event back.", requires=("create_event",)),
        _step("find_free_time",
             {"time_min": window_start, "time_max": window_end, "calendar_ids": ["primary"]},
             "Calendar read tier: when is this account free in the next two days."),
        _step("reschedule_event",
             {"event_id": "<the same event id>", "start": rescheduled_start,
              "end": rescheduled_end, "send_updates": "none"},
             "Move the scratch event - reschedule_event changes only start/end, nothing else.",
             requires=("create_event",)),
        _step("respond_to_event",
             {"event_id": "<the same event id>", "response": "accepted",
              "comment": "Demo self-RSVP"},
             "RSVP to the scratch event as its own sole attendee - never on anyone else's "
             "behalf.", requires=("create_event",)),
        _step("delete_event", {"event_id": "<the same event id>", "send_updates": "none"},
             "Cleanup: permanently remove the scratch event. Only present when "
             "calendar.delete is enabled - see cleanup_possible below if it is not.",
             requires=("create_event",)),
        _step("report_a_problem", {},
             "If anything above looked wrong, assemble a filable report with no ids or "
             "credentials."),
    ]


def register_demo_tools(app: MCPServer) -> None:
    @tool(app, annotations=LOCAL_READ)
    def demonstration_plan() -> dict[str, Any]:
        """An ordered plan for demonstrating everything THIS deployment's registered tools can
        do, entirely against the authenticated user's own mailbox and a scratch calendar event.

        Use this when somebody asks for a demo, a walkthrough, or "show me what you can do with
        my mail and calendar". Returns the steps; YOU carry them out by calling the tools
        named, in order, substituting each placeholder (anything in angle brackets) with a real
        value - `<the authenticated user>` from `whoami`'s own result, everything else from an
        earlier step's own result. Work out each tool's other arguments from its own
        description - that is part of what this exercise proves.

        **It sends real mail and creates a real calendar event - always to/on the
        authenticated user's own account, never anyone else's.** Say so before starting.
        `unavailable` lists tools this deployment's policy or flavour has not registered;
        `skipped` lists tools that ARE registered but whose own prerequisite (an earlier step's
        output, e.g. `whoami`) is not available, so no step here ever carries an address or id
        nobody could have resolved - read both before starting, so you can say up front what
        will not run and why. If `cleanup_possible` is false, the scratch calendar event will
        need deleting by hand; if `mail_cleanup_possible` is false, every message this plan
        sends will remain in the mailbox with no way to trash it through this server - say so
        BEFORE creating anything, not after.

        Every run is independent and safe to repeat - see this module's own docstring for what
        that guarantee does and does not cover (a demo-created LABEL is the one thing this
        server cannot yet delete again)."""
        # Timestamp for a human to read at a glance, plus a short random suffix so two calls
        # made in immediate succession (a test, or a person re-running the plan right away)
        # cannot collide on the one artefact this server cannot delete again - see the module
        # docstring on the label. The timestamp alone is not enough: wall-clock resolution on
        # some platforms is coarser than two Python calls back to back.
        run_id = (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                 + "-" + uuid.uuid4().hex[:8])
        registered = frozenset(t.name for t in app._tool_manager.list_tools())

        steps: list[DemoStep] = []
        unavailable: list[str] = []
        skipped: list[dict[str, str]] = []
        included_tools: set[str] = set()
        # `_catalogue` never yields "demonstration_plan" itself (see its own docstring) - there
        # is no runtime check for it here because the catalogue is this module's own static
        # data, not caller input; a step naming it would be a bug in `_catalogue`, caught
        # immediately by `tests/test_demo.py::test_demonstration_plan_does_not_include_itself`
        # rather than defended against at call time for a case that cannot occur.
        for entry in _catalogue(run_id):
            if entry.tool not in registered:
                if entry.tool not in unavailable:
                    unavailable.append(entry.tool)
                continue
            missing = sorted(entry.requires - included_tools)
            if missing:
                skipped.append({
                    "tool": entry.tool,
                    "reason": f"needs {', '.join(missing)}, which this configuration does not "
                             f"make available (registered but earlier steps needing it were "
                             f"themselves dropped, or it is not registered at all)",
                })
                continue
            steps.append({"step": len(steps) + 1, "tool": entry.tool,
                         "arguments": entry.arguments, "why": entry.why})
            included_tools.add(entry.tool)

        mail_cleanup_possible = "trash_email" in registered and "trash_thread" in registered
        calendar_cleanup_possible = "delete_event" in registered

        return {
            "steps": steps,
            "unavailable": unavailable,
            "skipped": skipped,
            # Filtered to registered tools only: an entry for a tool this deployment never
            # registered at all would confuse "not demonstrated" (a deliberate choice) with
            # "not registered" (already covered by `unavailable`).
            "not_demonstrated": {name: reason for name, reason in _NOT_DEMONSTRATED.items()
                                if name in registered},
            "cleanup_possible": calendar_cleanup_possible,
            "mail_cleanup_possible": mail_cleanup_possible,
            "advice": (
                f"Every {SELF!r} placeholder must become the address whoami's OWN result "
                f"returns for THIS run, never a hard-coded or user-supplied address. If "
                f"whoami is in unavailable or skipped, do not attempt any step that sends "
                f"mail or creates a calendar event - this plan already omits them for exactly "
                f"that reason, but say so if asked why fewer steps ran than expected. If "
                f"cleanup_possible is false, tell the user the scratch calendar event needs "
                f"deleting by hand before creating it. If mail_cleanup_possible is false, tell "
                f"them before sending anything that every message this plan sends will remain "
                f"in their mailbox - mail.write (not just mail.send) is what trash_email/"
                f"trash_thread need, and this deployment does not have it enabled. The label "
                f"this plan creates (csa-demo-{run_id}) is NEVER deleted, under any "
                f"configuration - there is no delete_label tool in this server yet; mention "
                f"that if asked to leave no trace. See skipped/not_demonstrated for every "
                f"tool this run does not call, and why."),
        }
