"""Calendar operations built on the `Backend` seam.

**There is no accept endpoint, and this module does not reinvent one.** Responding to an
invitation is a read-modify-write against the `attendees` array (find the attendee marked
`"self": true`, patch only that field, carry the etag). That logic now lives in
`Backend.respond_to_event` (`FakeBackend` and `ApiBackend` both implement it, identically,
per the ruling on task-8-brief.md) rather than here, because the refusal to answer on
someone else's behalf is a *control*, and this project's rule is that controls live at the
Backend seam so the library refuses without the server. `Calendar.respond()` below is a thin
wrapper: validate `response`, delegate the rest.

The three methods that *are* this module's own work — `create`, `reschedule`, `find_free` —
share one theme: catch what would be a confusing round trip to Google before it happens, and
turn `query_freebusy`'s busy intervals into the answer people actually asked for.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .backend import _event_boundary, _parse_rfc3339

RESPONSES = ("accepted", "declined", "tentative")


class Calendar:
    def __init__(self, backend: Any) -> None:
        self._b = backend

    def respond(self, calendar_id: str, event_id: str, response: str,
                comment: str | None = None) -> dict[str, Any]:
        """Validate `response`, then delegate the RSVP itself to the Backend.

        Everything this method used to do by hand — reading the event, finding the `self`
        attendee, refusing when there is no attendee list or no `self` entry in it, writing
        back only `attendees` under the read's etag — is `Backend.respond_to_event`'s job now.
        Duplicating that here would give two implementations of the same refusal that could
        silently drift apart; this wrapper exists only to keep `response` validation (a
        library-level concern, not a Google-account concern) out of the Backend seam.
        """
        if response not in RESPONSES:
            raise ValueError(
                f"{response!r} is not a response. Valid values: {', '.join(RESPONSES)}.")
        return self._b.respond_to_event(calendar_id=calendar_id, event_id=event_id,
                                        response=response, comment=comment)

    def create(self, *, calendar_id: str, body: dict[str, Any],
              send_updates: str = "all") -> dict[str, Any]:
        """Create an event, after checking the one thing Google's own `events.insert` will
        happily accept and then hand you back a nonsensical event for: a `start` after `end`.

        Both boundaries are parsed with `_event_boundary` (which itself calls
        `_parse_rfc3339`, the one RFC3339 parser this project has — see its docstring on why a
        second one would silently reintroduce the naive-string-comparison bug Task 3 found).
        Using `_event_boundary` rather than calling `_parse_rfc3339` on `body["start"]`
        directly also means an all-day event (`{"date": "2026-10-01"}`, no `dateTime`) is
        handled the same way a timed event is, rather than raising on a `KeyError` for a field
        that was never going to be there.

        `end == start` is NOT rejected. A zero-duration timed event is a legitimate way to
        pin a single instant on a calendar (a deadline marker, a reminder), and Google accepts
        it; only `end < start` — an event that finishes before it starts — is nonsensical
        regardless of what the event represents, so only that is refused here.
        """
        start = body.get("start")
        end = body.get("end")
        if not start or not end:
            raise ValueError("an event needs both a start and an end.")
        start_dt = _event_boundary(start, context="new event start")
        end_dt = _event_boundary(end, context="new event end")
        if start_dt is None or end_dt is None:
            raise ValueError("start and end each need a 'dateTime' or a 'date'.")
        if end_dt < start_dt:
            raise ValueError(f"end ({end!r}) is before start ({start!r}).")
        return self._b.create_event(calendar_id=calendar_id, body=body, send_updates=send_updates)

    def reschedule(self, *, calendar_id: str, event_id: str, start: dict[str, Any],
                  end: dict[str, Any], send_updates: str = "all") -> dict[str, Any]:
        """Move an event's time without touching anything else about it.

        The patch body is `{"start": start, "end": end}` only — never the whole event — for
        the same reason `respond_to_event` sends only `attendees`: `update_event`'s patch
        semantics mean any field NOT in the body is left alone, so a full-event write would
        silently resend (and so overwrite, under a stale local copy) a summary, description or
        attendee list someone else edited since this caller last read the event. The etag from
        a fresh read is carried as `If-Match`, so a concurrent edit is a refusal
        (`ConflictError`), not a silent overwrite.

        **Attendees are notified by default (`send_updates="all"`), unlike `respond()`'s
        `"none"`.** The two are not the same kind of write. Responding is not an edit to the
        meeting — nobody's copy of "when is this" changes, so nobody needs mail. Rescheduling
        *is* an edit to the one fact attendees most need to act on correctly, so silence would
        be the defect: someone shows up at the old time. This method also does not refuse
        outright when the event has attendees — organisers reschedule meetings with attendees
        on them constantly, and that is an ordinary, legitimate action, not the kind of
        on-someone-else's-behalf control `respond_to_event` exists to block. A caller who
        genuinely wants a quiet reschedule (e.g. correcting a typo before anyone has seen the
        invite) can still pass `send_updates="none"` explicitly.
        """
        start_dt = _event_boundary(start, context="reschedule start")
        end_dt = _event_boundary(end, context="reschedule end")
        if start_dt is None or end_dt is None:
            raise ValueError("start and end each need a 'dateTime' or a 'date'.")
        if end_dt < start_dt:
            raise ValueError(f"end ({end!r}) is before start ({start!r}).")
        event = self._b.get_event(calendar_id=calendar_id, event_id=event_id)
        return self._b.update_event(calendar_id=calendar_id, event_id=event_id,
                                    body={"start": start, "end": end},
                                    etag=event.get("etag"), send_updates=send_updates)

    def find_free(self, *, time_min: str, time_max: str,
                 calendar_ids: list[str]) -> dict[str, Any]:
        """When are we free — the question people actually ask — rather than
        `query_freebusy`'s answer to a different one ("when are we busy").

        Returning busy intervals and letting the caller (a human, or a model reading the tool
        result) subtract them from the window is exactly the arithmetic that goes wrong:
        overlapping busy blocks across several calendars double-count, and an off-by-one at a
        shared boundary either invents a gap that doesn't exist or drops one that does. This
        method does that arithmetic once, here, so nobody downstream has to:

        1. Every busy interval, from every READABLE calendar queried, is clipped to
           `[time_min, time_max)` first — a block that starts before the window or ends after
           it must not be allowed to carve a gap outside the window into existence, or leave a
           phantom sliver of "free" time before/after the block where the window doesn't reach
           anyway.
        2. Clipped intervals are merged across ALL readable calendars queried, not
           per-calendar, before inversion — a slot free on calendar A but busy on calendar B is
           busy for the meeting this is being asked about. Merging with `<=` (not `<`) at the
           boundary means two blocks that touch exactly end-to-end (one ends at 10:00, the
           next starts at 10:00) merge into one interval instead of leaving a
           technically-correct but useless zero-length gap between them.
        3. The merged busy intervals are inverted into the gaps between them, inside the
           window.

        **"Could not read this calendar" is not "this calendar is free" (fix round 1, CINO
        2026-09-25 — a real defect, not a `FakeBackend` artefact).** Google's real
        `freebusy.query` reports an inaccessible, missing, or permission-denied calendar in a
        per-calendar `errors` array, separate from `busy`, and `ApiBackend.query_freebusy`
        passes that response through verbatim. The first version of this method read only
        `cal.get("busy", [])` and so treated an `errors` entry exactly like an empty, genuinely
        free calendar — silence rendered as availability, the same failure shape this project
        has hit repeatedly elsewhere (a truncated MIME walk read as an empty message, an
        unlisted scope silently discarded, `mark_spam` not moving the message). The felt
        consequence: propose 2pm Tuesday because Dana's calendar could not be read, and two
        meetings collide.

        Fixed by changing the return shape, not just the logic, because a bare `list[dict]`
        cannot keep the promise that empty means "no free time" once some calendars might be
        unreadable — the structure has to say so itself:

            {"free": [{"start": ..., "end": ...}, ...],
             "unreadable_calendars": [{"id": ..., "reason": ...}, ...]}

        A calendar that errors is named in `unreadable_calendars` and its `errors` entry is
        never read as busy-or-free — it contributes NO interval either way, and the gaps in
        `free` are computed only from the calendars that could be read. One bad id among ten
        does not blank an otherwise useful answer; a caller (or a model) that sees
        `unreadable_calendars` non-empty can say "I could not check Dana's calendar" instead of
        proposing a time as though it had seen it.

        **Edge cases, resolved:**

        - No busy blocks at all, on any readable calendar -> `free` is one gap covering the
          whole window, `unreadable_calendars` empty. This IS unambiguously "no free time was
          found" now — the case that used to be confusable with "we learned nothing" is
          instead named directly in `unreadable_calendars`.
        - Busy covering the entire window -> `free` is `[]`. Distinguishable from "we could not
          check" because `unreadable_calendars` is `[]` too in that case, and non-empty when a
          calendar genuinely could not be read.
        - Adjacent busy blocks that touch exactly -> merged in step 2, so no zero-length gap
          is ever emitted between them.
        - A busy block extending beyond the window on either side -> clipped to the window in
          step 1, so it cannot produce a gap outside `[time_min, time_max)`.

        Every boundary in `free` is normalised to UTC and rendered with a trailing `Z`, not
        `+00:00` (`isoformat()`'s own default) — Google renders its own UTC timestamps with
        `Z`, every other timestamp this server hands back is `Z`, and a lone `+00:00` would
        invite a model reading the result to treat it as a different, unfamiliar format.
        """
        window_start = _parse_rfc3339(time_min, context="time_min")
        window_end = _parse_rfc3339(time_max, context="time_max")
        if window_end <= window_start:
            raise ValueError(f"time_max ({time_max!r}) is not after time_min ({time_min!r}).")
        result = self._b.query_freebusy(time_min=time_min, time_max=time_max,
                                        calendar_ids=calendar_ids)
        calendars = result.get("calendars", {})
        intervals: list[tuple[datetime, datetime]] = []
        unreadable: list[dict[str, str]] = []
        for calendar_id in calendar_ids:
            cal = calendars.get(calendar_id, {})
            errors = cal.get("errors")
            if errors:
                # An errors entry means we learned NOTHING about this calendar - not that it
                # is free. Its busy list (if any) is deliberately never read: Google does not
                # promise one is even present alongside errors, and treating it as data would
                # revive exactly the bug this fix closes.
                reason = errors[0].get("reason", "unknown") if isinstance(errors[0], dict) else "unknown"
                unreadable.append({"id": calendar_id, "reason": reason})
                continue
            for index, busy in enumerate(cal.get("busy", [])):
                where = f"calendar {calendar_id!r} busy interval {index}"
                start = _parse_rfc3339(busy["start"], context=f"{where} start")
                end = _parse_rfc3339(busy["end"], context=f"{where} end")
                # Clip to the window (edge case: a block extending beyond it either side).
                start = max(start, window_start)
                end = min(end, window_end)
                if end > start:  # drops a block that clips down to nothing, or was backwards
                    intervals.append((start, end))
        intervals.sort(key=lambda iv: iv[0])
        merged: list[list[datetime]] = []
        for start, end in intervals:
            # `<=`, not `<`: two blocks that touch exactly must merge, or the gap between them
            # would be zero-length (edge case: adjacent busy blocks that touch exactly).
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        def _utc_z(moment: datetime) -> str:
            # A busy interval can arrive in any offset a calendar happens to use;
            # `isoformat()` renders whichever tzinfo the underlying datetime carries (and, once
            # normalised to UTC, spells it "+00:00" - Python's own default, not Google's).
            # Substituted for "Z" here to match how every other timestamp this server returns
            # is rendered, rather than leaving a "+00:00" outlier a model might read as a
            # different format.
            return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        free: list[dict[str, str]] = []
        cursor = window_start
        for start, end in merged:
            if start > cursor:
                free.append({"start": _utc_z(cursor), "end": _utc_z(start)})
            cursor = max(cursor, end)
        if cursor < window_end:
            free.append({"start": _utc_z(cursor), "end": _utc_z(window_end)})
        return {"free": free, "unreadable_calendars": unreadable}
