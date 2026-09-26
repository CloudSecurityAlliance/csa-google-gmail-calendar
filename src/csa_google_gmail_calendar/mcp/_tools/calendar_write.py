"""The Calendar writing tier: `create_event`, `reschedule_event`, `respond_to_event` - gated
`policy.CALENDAR_WRITE` - and `delete_event`, gated `policy.CALENDAR_DELETE` (OFF by default;
see `policy.DEFAULT_ENABLED`). 4 tools.

**`reschedule_event` is named for exactly what it does, and nothing more (fix round 1, CINO
2026-09-26).** It wraps `calendar.Calendar.reschedule`, which moves an event's `start`/`end`
and touches nothing else - no summary, location, description, or attendee list. The tool was
first named `update_event`, which ADR-001's own rule (already applied to `delete_email`/
`trash_email` elsewhere in this project - "a tool name is a claim made to a reader who cannot
check it") says is the wrong name for it: a reader who wants to fix a meeting's title would
reach for `update_event` on the strength of the name alone, find no argument for it, and either
waste a turn or - worse - call it with the times it already has, believing something changed.
`reschedule_event` claims only what the tool does. A general event patch (summary/location/
description/attendees) is a real gap this surface has, stated as a negative capability in the
tool's own docstring rather than hidden - and is deliberately NOT built here: a field-level
patch has its own concurrency story (which fields conflict, whether a stale `etag` should
block one field's write when only a different field changed elsewhere) that this task did not
plan for and `calendar.Calendar` does not yet implement. Inventing it as a side effect of a
rename would be exactly the kind of scope creep the negative-capability discipline exists to
avoid; the honest narrow tool, honestly named, is the right shipping state, and a general
`update_event` is free to arrive later as its own decision.

**`respond_to_event` is ONE tool with a constrained `response` argument** (ADR-016: a tool is
an operation plus constrained arguments), never three separate `accept_event`/`decline_event`/
`tentative_event` tools that would each describe the same operation.

**`create_event`/`reschedule_event` default to notifying attendees (`send_updates="all"`);
`respond_to_event` defaults to notifying nobody (`send_updates="none"`).** This is not an
inconsistency - see each tool's own docstring for why. In one sentence: creating or
rescheduling changes a fact an attendee must act on (a new commitment, a changed time);
responding to an existing invitation changes nothing about the meeting itself, only the
responder's own status on it.

`create`/`reschedule`'s own validation (`end` not before `start`) lives in `calendar.Calendar`,
not here - this module supplies only the MCP-facing argument shapes (`start`/`end` as bare
strings rather than `{"dateTime": ...}` nodes) and delegates the rest.
"""
from __future__ import annotations

from typing import Any, Literal

from mcp.server import MCPServer

from ... import policy as policy_mod
from ...backend import Backend
from ...calendar import Calendar
from ._base import DESTRUCTIVE, WRITE, tool


def _boundary_node(value: str) -> dict[str, str]:
    """A bare RFC3339 timestamp (`"2026-10-01T09:00:00Z"`) or bare `YYYY-MM-DD` date
    (`"2026-10-01"`) into the `{"dateTime": ...}`/`{"date": ...}` node `Calendar.create`/
    `Calendar.reschedule` (via `backend._event_boundary`) expect - detected by the presence of
    a `"T"` separator, which a full timestamp always has and a bare date never does, matching
    Google's own discovery-document distinction between a timed event's `dateTime` and an
    all-day event's `date`."""
    return {"dateTime": value} if "T" in value else {"date": value}


def register_calendar_write_tools(app: MCPServer, backend: Backend,
                                  policy_obj: policy_mod.Policy) -> None:
    if policy_obj.allows("create_event"):
        @tool(app, annotations=WRITE)
        def create_event(summary: str, start: str, end: str, calendar_id: str = "primary",
                         description: str | None = None, location: str | None = None,
                         attendees: list[str] | None = None,
                         send_updates: str = "all") -> dict[str, object]:
            """Create a new event on a calendar.

            `start`/`end` each take either a full RFC3339 timestamp for a timed event
            (`"2026-10-01T09:00:00Z"`) or a bare `"YYYY-MM-DD"` date for an all-day event -
            which one is used is detected automatically from whether a time component is
            present. `end` before `start` is refused; `end` equal to `start` is allowed (a
            zero-duration timed event is a legitimate way to pin a single instant - a deadline
            marker, a reminder).

            `attendees` is a plain list of email addresses; each becomes an invitee.

            **Notifies attendees by default (`send_updates="all"`) - an invitation nobody is
            told about is not an invitation.** Pass `send_updates="none"` only for a
            deliberately quiet entry with no real attendees to notify (a personal reminder, for
            instance). `respond_to_event` is the one Calendar tool in this server that defaults
            the OTHER way, because responding to an existing invitation changes no fact an
            attendee must act on, while creating one does.

            Use `reschedule_event` afterward to move it, or `respond_to_event` to RSVP to
            someone ELSE's invitation rather than creating your own."""
            body: dict[str, Any] = {"summary": summary, "start": _boundary_node(start),
                                    "end": _boundary_node(end)}
            if description is not None:
                body["description"] = description
            if location is not None:
                body["location"] = location
            if attendees:
                body["attendees"] = [{"email": address} for address in attendees]
            return Calendar(backend).create(calendar_id=calendar_id, body=body,
                                            send_updates=send_updates)

    if policy_obj.allows("update_event"):  # Backend method name; the TOOL is reschedule_event
        @tool(app, annotations=WRITE)
        def reschedule_event(event_id: str, start: str, end: str, calendar_id: str = "primary",
                             send_updates: str = "all") -> dict[str, object]:
            """Move an event to a new start/end time. This is ALL this tool does - it cannot
            change a summary, location, description, or attendee list; there is no argument for
            any of those, and no other tool in this server that edits them either. If you want
            to correct a meeting's title or add an attendee, this is not the tool and none
            currently exists for it - say so rather than calling this with the time unchanged
            and assuming something happened.

            Everything about the event other than `start`/`end` is left exactly as it was: the
            patch sent to Google carries only those two fields, so a change someone else made
            to another field since you last read the event is never silently overwritten. A
            concurrent edit to the event itself (by anyone, to any field) between this tool's
            own read and its write is refused as a conflict rather than silently lost - this
            tool always reads the event fresh immediately before writing, under that read's own
            etag.

            `start`/`end` accept the same formats as `create_event`'s; `end` before `start` is
            refused.

            **Notifies attendees by default (`send_updates="all"`) - rescheduling changes the
            one fact an attendee most needs to act on correctly, and silence would mean someone
            shows up at the old time.** Pass `send_updates="none"` explicitly only for a
            deliberately quiet correction (fixing a typo'd time before anyone has seen the
            invite, for instance). This tool does not refuse outright when the event has
            attendees - organisers reschedule meetings with attendees on them constantly, and
            that is ordinary, legitimate use, not the kind of on-someone-else's-behalf action
            `respond_to_event` exists to block."""
            return Calendar(backend).reschedule(
                calendar_id=calendar_id, event_id=event_id, start=_boundary_node(start),
                end=_boundary_node(end), send_updates=send_updates)

    if policy_obj.allows("respond_to_event"):
        @tool(app, annotations=WRITE)
        def respond_to_event(event_id: str,
                             response: Literal["accepted", "declined", "tentative"],
                             calendar_id: str = "primary",
                             comment: str | None = None) -> dict[str, object]:
            """RSVP to an event YOU were invited to, as yourself - never on another attendee's
            behalf. `response` is exactly one of `"accepted"`, `"declined"`, `"tentative"`
            (ADR-016: one tool, one constrained argument - not three tools each saying the same
            thing).

            This refuses in two different ways, for two different facts about the event - tell
            them apart and act accordingly:

              - **The event has NO attendees at all.** It is not an invitation in the first
                place - an ordinary entry on a calendar, with nothing to accept or decline. Use
                `delete_event` instead if you no longer want it there; retrying with a different
                `response` will refuse identically.
              - **The event HAS attendees, but you are not among them.** You genuinely were not
                invited, and responding on someone else's behalf is not something this server
                will do. There is no argument or retry that makes this one succeed - only
                whoever owns the invite list adding you as an attendee first.

            **Sends NO notification (`send_updates="none"`), unlike `create_event`/
            `reschedule_event`'s default of `"all"`.** Responding to an invitation is not an edit to
            the meeting itself - nobody's copy of "when is this" changes, so nobody needs mail
            about your answer."""
            return Calendar(backend).respond(calendar_id=calendar_id, event_id=event_id,
                                             response=response, comment=comment)

    if policy_obj.allows("delete_event"):
        @tool(app, annotations=DESTRUCTIVE)
        def delete_event(event_id: str, calendar_id: str = "primary",
                         send_updates: str = "all") -> dict[str, object]:
            """Permanently DESTROY an event. Unlike Gmail's trash/untrash pair, Google's
            Calendar API has no trash or undelete for a deleted event - once this succeeds, the
            event is gone.

            Gated behind the `calendar.delete` capability, which is OFF by default: a deployment
            that has not deliberately enabled it will not see this tool at all, rather than
            seeing it and being refused.

            Notifies attendees by default (`send_updates="all"`) - the same reasoning as
            `create_event`: attendees need to know a meeting on their calendar no longer
            exists. Pass `send_updates="none"` only when certain nobody else needs telling (an
            event with no real attendees, for instance)."""
            return backend.delete_event(calendar_id=calendar_id, event_id=event_id,
                                        send_updates=send_updates)
