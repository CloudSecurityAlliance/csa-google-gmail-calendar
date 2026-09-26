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
an argument.** `whoami` (step 1) is how the plan learns that address; every step whose
arguments would otherwise need a recipient uses the literal placeholder string
`"<the authenticated user>"` - never a real address baked into this module, and never a value
read from anywhere this plan's own caller could influence. `reply`/`reply_all` need no such
placeholder at all: this plan first sends a message TO that placeholder address (step 17,
`send_message`), so the original message's own From/To/Cc - what `reply`/`reply_all` resolve
their recipients from - already contains nothing but that same address. `forward`, which has no
implicit recipient (`to` is a required argument on that tool, unlike `reply`), uses the
placeholder explicitly for the same reason. `tests/test_demo.py` pins this: every step whose
tool takes a `to`/`cc`/`bcc` argument that is present in this plan's own `arguments` dict must
be exactly the placeholder, for every registered tool, not only the three the brief's own
illustrative test names.

## Safe to run twice, and what it cleans up

Every id this plan creates comes from an EARLIER STEP IN THE SAME RUN - never a stale id left
over from a previous run - so nothing here can collide with what a prior run left behind, except
the one thing this server currently has no way to remove: a created LABEL (there is no
`delete_label` tool - a known, documented gap, see `README.md`'s structural absences and
`TODO.md`). This plan's own label name is timestamped at the moment the plan is GENERATED
(`_run_id`, computed once, not per call - see the function body), so a second run creates a
second, differently-named label rather than colliding with (or silently reusing) the first.
Everything else this plan creates is either genuinely idempotent to repeat (sending a message,
creating a draft) or is cleaned up within the SAME run: the scratch draft is deleted, the
scratch calendar event is deleted if `calendar.delete` is enabled (and flagged as needing manual
deletion, in `unavailable`, if it is not), and the messages/threads this plan sends are trashed
at the end (trash, not permanent delete - there is no permanent-mail-delete tool either, and
trash is the disposal primitive this server actually has).

## `get_attachment`, and the fixture it needs

Fix round 1 (coordinator review, CINO 2026-09-26): the send_message step now attaches a small
local file (a placeholder path under `CSA_GGC_ATTACH_DIR`, the same variable that governs both
outgoing attachments and where a downloaded one is written), so the demo CREATES its own
fixture instead of hoping the real mailbox happens to have one lying around. A `get_message`
step reads that same self-sent message back to learn the attachment's id and filename, and
`get_attachment` downloads it. If `CSA_GGC_ATTACH_DIR` is not configured, `send_message` itself
would refuse (naming that variable) before any of this runs - the plan's own `advice` field
says so, so the executor can skip straight to the next step rather than getting stuck on a
refusal it could have predicted.

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
from typing import Any, TypedDict

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


def _catalogue(run_id: str) -> list[tuple[str, dict[str, Any], str]]:
    """(tool, arguments, why) triples, in the order a walkthrough should attempt them. Not
    every registered tool needs to appear (see the module docstring on `get_attachment`/
    `authenticate`/`logout`); `demonstration_plan` filters this against what is ACTUALLY
    registered, so a narrower policy or flavour simply sees a shorter plan, never a step naming
    a tool that does not exist (`tests/test_demo.py` pins this).

    `run_id` disambiguates only the one thing this plan creates that nothing can delete again
    (a label - see the module docstring); every other placeholder value below is deliberately
    a fixed, readable string, since nothing about repeatability depends on it."""
    label_name = f"csa-demo-{run_id}"
    tomorrow = "<tomorrow, 09:00, RFC3339, e.g. 2026-10-01T09:00:00Z>"
    tomorrow_end = "<tomorrow, 09:30, RFC3339>"
    rescheduled_start = "<tomorrow, 10:00, RFC3339>"
    rescheduled_end = "<tomorrow, 10:30, RFC3339>"
    window_start = "<today, 00:00, RFC3339>"
    window_end = "<the day after tomorrow, 00:00, RFC3339>"

    return [
        ("auth_status", {},
         "Confirm a usable credential is cached before touching anything else."),
        ("whoami", {},
         f"Learn the address every send below targets - substitute it for every "
         f"{SELF!r} placeholder in this plan."),
        ("describe_configuration", {},
         "See which capabilities, scopes and flavour this deployment has enabled, and what "
         "it is hiding, before assuming a step below will work."),
        ("list_labels", {}, "See what labels already exist."),
        ("list_calendars", {}, "See which calendars this account can reach."),
        ("search_messages", {"query": "in:inbox", "limit": 5},
         "Read tier: confirm search works against the real mailbox."),
        ("list_threads", {"limit": 5}, "Read tier: list a handful of real conversations."),
        ("get_thread", {"thread_id": "<a thread id from the list_threads result above>"},
         "Read one conversation's summaries."),
        ("get_message", {"message_id": "<a message id from the search_messages result above>"},
         "Read one message's full content."),
        ("list_drafts", {}, "Preview whatever drafts already exist."),
        ("create_draft", {"to": [SELF], "subject": "CSA demo draft (safe to delete)",
                          "body": "Created by csa-google-gmail-calendar's demonstration_plan. "
                                  "Safe to delete."},
         "Compose tier: a rehearsal draft, self-addressed."),
        ("get_draft", {"draft_id": "<the draft id from create_draft above>"},
         "Confirm the draft's own content."),
        ("update_draft", {"draft_id": "<the same draft id>", "to": [SELF],
                          "subject": "CSA demo draft (edited, safe to delete)",
                          "body": "Edited by the demonstration plan."},
         "Compose tier: edit it before sending or discarding."),
        ("delete_draft", {"draft_id": "<the same draft id>"},
         "Disposal of the rehearsal draft - permanent, but it is this plan's own throwaway "
         "content, never anything the user wrote."),
        ("create_draft", {"to": [SELF], "subject": "CSA demo send_draft (safe to delete)",
                          "body": "A second draft, created only to demonstrate send_draft."},
         "A fresh draft to demonstrate sending AS a draft, distinct from send_message below."),
        ("send_draft", {"draft_id": "<the draft id from the create_draft immediately above>"},
         "Send an existing draft as-is - self-addressed, irreversible once sent."),
        ("send_message", {"to": [SELF], "subject": "CSA demo message (safe to delete)",
                          "body": "Sent by csa-google-gmail-calendar's demonstration_plan, to "
                                  "itself.",
                          "attachments": ["<a small local file already present under "
                                         "CSA_GGC_ATTACH_DIR - if that variable is not "
                                         "configured, send_message will refuse naming it; "
                                         "drop the attachments argument and skip the "
                                         "get_attachment step below instead>"]},
         "Sending tier: a brand-new self-addressed message, carrying a small attachment so "
         "get_attachment below has something real to fetch. Its id and thread id feed every "
         "organising/disposal step below."),
        ("get_message", {"message_id": "<the same message id from send_message above>"},
         "Read the self-sent message back to learn its attachment's id and filename - "
         "get_message never inlines attachment bytes, only the metadata get_attachment needs."),
        ("get_attachment",
         {"message_id": "<the same message id from send_message above>",
          "attachment_id": "<the attachment_id from the get_message result above>",
          "filename": "<the matching filename from the same result>"},
         "Download the attachment just sent - the one attachment operation Gmail's API has; "
         "there is no upload tool because there is no attachments.upload endpoint to call."),
        ("reply", {"message_id": "<the message id from send_message above>",
                  "body": "Demo reply - self-addressed because the original was."},
         "Sending tier: reply resolves its own recipient from the original message's From, "
         "which is the address send_message above just used - never a to argument here."),
        ("reply_all", {"message_id": "<the same message id from send_message above>",
                       "body": "Demo reply-all - still self-addressed for the same reason."},
         "Sending tier: reply_all resolves recipients the same way reply does."),
        ("forward", {"message_id": "<the same message id from send_message above>",
                    "to": [SELF],
                    "body": "Demo forward - self-addressed, unlike reply/reply_all this tool "
                            "has no implicit recipient."},
         "Sending tier: forward, explicitly self-addressed since it takes a required to."),
        ("create_label", {"name": label_name},
         "Organising tier: a label unique to this run (see the module docstring on why)."),
        ("modify_message_labels",
         {"message_id": "<the message id from send_message above>",
          "add": ["<the label id from create_label above>"]},
         "Apply the new label to one message."),
        ("modify_thread_labels",
         {"thread_id": "<the thread id from send_message's result above>",
          "add": ["<the same label id>"]},
         "Apply it to every message in that thread at once."),
        ("mark_unread", {"message_id": "<the message id from send_message above>"},
         "Organising tier."),
        ("mark_read", {"message_id": "<the same message id>"}, "Reverse of mark_unread."),
        ("archive_email", {"message_id": "<the same message id>"},
         "Remove it from the Inbox view without trashing it."),
        ("archive_thread", {"thread_id": "<the thread id from send_message's result above>"},
         "The same, applied to the whole thread."),
        ("mark_spam", {"message_id": "<the same message id>"}, "Disposal tier."),
        ("unmark_spam", {"message_id": "<the same message id>"},
         "Reverse of mark_spam - restores it."),
        ("trash_email", {"message_id": "<the same message id>"},
         "Disposal tier: reversible - untrash_email is next."),
        ("untrash_email", {"message_id": "<the same message id>"},
         "Demonstrates trash_email is reversible."),
        ("trash_thread", {"thread_id": "<the thread id from send_message's result above>"},
         "Disposal tier, thread level - reversible - untrash_thread is next."),
        ("untrash_thread", {"thread_id": "<the same thread id>"},
         "Demonstrates trash_thread is reversible."),
        ("get_profile", {},
         "Keeping-up tier: the full mailbox summary, including the current history_id."),
        ("list_history", {"start_history_id": "<the history_id from get_profile above>"},
         "Keeping-up tier: expect an empty (or near-empty) result immediately after "
         "get_profile - that means nothing changed in between, not that tracking is broken."),
        ("list_events", {"calendar_id": "primary", "limit": 5},
         "Calendar read tier: list a few real events."),
        ("create_event",
         {"summary": f"CSA demo scratch event (safe to delete) {run_id}",
          "start": tomorrow, "end": tomorrow_end, "attendees": [SELF], "send_updates": "none"},
         "Calendar write tier: a scratch event with no attendee but the authenticated user "
         "themselves - never a stranger's address - and no notification, since there is "
         "nobody else to notify."),
        ("get_event", {"event_id": "<the event id from create_event above>"},
         "Read the scratch event back."),
        ("find_free_time",
         {"time_min": window_start, "time_max": window_end, "calendar_ids": ["primary"]},
         "Calendar read tier: when is this account free in the next two days."),
        ("reschedule_event",
         {"event_id": "<the same event id>", "start": rescheduled_start,
          "end": rescheduled_end, "send_updates": "none"},
         "Move the scratch event - reschedule_event changes only start/end, nothing else."),
        ("respond_to_event",
         {"event_id": "<the same event id>", "response": "accepted",
          "comment": "Demo self-RSVP"},
         "RSVP to the scratch event as its own sole attendee - never on anyone else's behalf."),
        ("delete_event", {"event_id": "<the same event id>", "send_updates": "none"},
         "Cleanup: permanently remove the scratch event. Only present when calendar.delete "
         "is enabled - see unavailable/cleanup_possible below if it is not."),
        ("report_a_problem", {},
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
        `unavailable` lists tools this deployment's policy or flavour has not registered, so you
        can say up front what will be skipped. If `cleanup_possible` is false, the scratch
        calendar event will need deleting by hand (this deployment has `calendar.delete` off);
        say that before creating it, not after.

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
        # `_catalogue` never yields "demonstration_plan" itself (see its own docstring) - there
        # is no runtime check for it here because the catalogue is this module's own static
        # data, not caller input; a step naming it would be a bug in `_catalogue`, caught
        # immediately by `tests/test_demo.py::test_demonstration_plan_does_not_include_itself`
        # rather than defended against at call time for a case that cannot occur.
        for tool_name, arguments, why in _catalogue(run_id):
            if tool_name not in registered:
                unavailable.append(tool_name)
                continue
            steps.append({"step": len(steps) + 1, "tool": tool_name,
                         "arguments": arguments, "why": why})

        return {
            "steps": steps,
            "unavailable": unavailable,
            # Filtered to registered tools only: an entry for a tool this deployment never
            # registered at all would confuse "not demonstrated" (a deliberate choice) with
            # "not registered" (already covered by `unavailable`).
            "not_demonstrated": {name: reason for name, reason in _NOT_DEMONSTRATED.items()
                                if name in registered},
            "cleanup_possible": "delete_event" in registered,
            "advice": (
                f"Every {SELF!r} placeholder must become the address whoami's OWN result "
                f"returns for THIS run, never a hard-coded or user-supplied address. If "
                f"cleanup_possible is false, tell the user the scratch calendar event needs "
                f"deleting by hand before creating it. The label this plan creates "
                f"(csa-demo-{run_id}) is not deleted at the end - there is no delete_label "
                f"tool in this server yet; mention that if asked to leave no trace. If "
                f"CSA_GGC_ATTACH_DIR is not configured, send_message's attachments argument "
                f"will be refused - drop it and skip the get_attachment step; every other step "
                f"is unaffected. See not_demonstrated for the tools this plan intentionally "
                f"never calls, and why."),
        }
