"""Backend seam. `Backend` is the Protocol every capability in `policy._GATES` gates by name;
`FakeBackend` is the in-memory double the whole offline test suite runs against. `ApiBackend`
(Gmail + Calendar over `google-api-python-client`) is the real thing, satisfying the same
Protocol without either side needing to change.

Every method here is keyword-only. That is not house style for its own sake: `policy.Policy`
and `PolicyBackend` gate by method *name* alone and forward `*args, **kwargs` untouched, so a
positional call that happens to work today would silently stop working the moment a method
gained an argument in between two existing ones. Keyword-only removes that failure mode by
construction.

FakeBackend keeps its seed data as ten plain public attributes (`self.messages`, and so on)
rather than the leading-underscore, copy-on-construct fields the sibling project
(`csa-google-workspace`) uses. Tests here reach in and assert on them directly
(`fake.messages["m1"]["labelIds"]`) as an offline-suite convenience — see task-3-brief.md.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from google.auth.credentials import Credentials
from googleapiclient import discovery
from googleapiclient.errors import HttpError

from ._mime import upload_strategy
from .exceptions import (
    AccessError,
    ApiError,
    AuthError,
    ConflictError,
    CsaGoogleError,
    NotFoundError,
    UnsupportedOperation,
)


class Backend(Protocol):
    # --- mail: reads ---
    def search_messages(self, *, query: str, limit: int = 25,
                        page_token: str | None = None) -> dict[str, Any]: ...
    def get_message(self, *, message_id: str, fmt: str = "full") -> dict[str, Any]: ...
    def get_thread(self, *, thread_id: str, fmt: str = "full") -> dict[str, Any]: ...
    def list_threads(self, *, query: str | None = None, limit: int = 25,
                     page_token: str | None = None) -> dict[str, Any]: ...
    def get_attachment(self, *, message_id: str, attachment_id: str) -> dict[str, Any]: ...
    def list_labels(self) -> list[dict[str, Any]]: ...
    # Fix round 2 (CINO 2026-09-25): `list_drafts` used to return a bare list, with nowhere to
    # put a `nextPageToken` even though real `drafts.list` paginates the same way as
    # `messages.list`/`threads.list`/`events.list` - `ApiBackend` received the token over the
    # wire and dropped it at `result.get("drafts", [])`, discarding it, not merely failing to
    # expose something that never arrived. Changed to the same `dict` shape as `list_threads`
    # (`drafts` + `resultSizeEstimate` + `nextPageToken`, matching Gmail's own
    # `ListDraftsResponse` schema) while nothing in this codebase yet calls `list_drafts` (no
    # task past this one has landed), so there is no caller to migrate - the return-type change
    # is not additive in general, but is free right now and only gets more expensive to make
    # once something depends on the old shape.
    def list_drafts(self, *, limit: int = 25,
                    page_token: str | None = None) -> dict[str, Any]: ...
    def get_draft(self, *, draft_id: str) -> dict[str, Any]: ...
    def list_history(self, *, start_history_id: str) -> dict[str, Any]: ...
    def get_profile(self) -> dict[str, Any]: ...
    # --- mail: reversible writes ---
    def create_draft(self, *, raw: str) -> dict[str, Any]: ...
    def update_draft(self, *, draft_id: str, raw: str) -> dict[str, Any]: ...
    def delete_draft(self, *, draft_id: str) -> dict[str, Any]: ...
    def modify_message_labels(self, *, message_id: str, add: list[str] | None = None,
                              remove: list[str] | None = None) -> dict[str, Any]: ...
    def modify_thread_labels(self, *, thread_id: str, add: list[str] | None = None,
                             remove: list[str] | None = None) -> dict[str, Any]: ...
    def archive_message(self, *, message_id: str) -> dict[str, Any]: ...
    def archive_thread(self, *, thread_id: str) -> dict[str, Any]: ...
    def mark_read(self, *, message_id: str) -> dict[str, Any]: ...
    def mark_unread(self, *, message_id: str) -> dict[str, Any]: ...
    def trash_message(self, *, message_id: str) -> dict[str, Any]: ...
    def trash_thread(self, *, thread_id: str) -> dict[str, Any]: ...
    def untrash_message(self, *, message_id: str) -> dict[str, Any]: ...
    def untrash_thread(self, *, thread_id: str) -> dict[str, Any]: ...
    def mark_spam(self, *, message_id: str) -> dict[str, Any]: ...
    def unmark_spam(self, *, message_id: str) -> dict[str, Any]: ...
    def create_label(self, *, name: str) -> dict[str, Any]: ...
    # --- mail: outbound ---
    def send_message(self, *, raw: str) -> dict[str, Any]: ...
    def send_draft(self, *, draft_id: str) -> dict[str, Any]: ...
    def reply_message(self, *, raw: str, thread_id: str) -> dict[str, Any]: ...
    def reply_all_message(self, *, raw: str, thread_id: str) -> dict[str, Any]: ...
    def forward_message(self, *, raw: str) -> dict[str, Any]: ...
    # --- calendar ---
    def list_calendars(self) -> list[dict[str, Any]]: ...
    def get_calendar(self, *, calendar_id: str) -> dict[str, Any]: ...
    def list_events(self, *, calendar_id: str = "primary", time_min: str | None = None,
                    time_max: str | None = None, query: str | None = None,
                    limit: int = 25, page_token: str | None = None) -> dict[str, Any]: ...
    def get_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]: ...
    def query_freebusy(self, *, time_min: str, time_max: str,
                       calendar_ids: list[str]) -> dict[str, Any]: ...
    def create_event(self, *, calendar_id: str, body: dict[str, Any],
                     send_updates: str = "all") -> dict[str, Any]: ...
    def update_event(self, *, calendar_id: str, event_id: str, body: dict[str, Any],
                     send_updates: str = "all",
                     etag: str | None = None) -> dict[str, Any]: ...
    # `respond_to_event` owns the RSVP read-modify-write itself (find the caller's own
    # attendee, refuse when there isn't one, write back only the attendees field under the
    # etag it read) rather than delegating any of that to a caller - controls live at the
    # Backend seam, so the library refuses without the server. Task 8's `Calendar.respond()`
    # is a thin wrapper that validates `response` and delegates here unchanged.
    def respond_to_event(self, *, calendar_id: str, event_id: str, response: str,
                         comment: str | None = None) -> dict[str, Any]: ...
    def delete_event(self, *, calendar_id: str, event_id: str,
                     send_updates: str = "all") -> dict[str, Any]: ...


def _remove_labels(label_ids: list[str], remove: list[str] | None) -> None:
    for label in remove or ():
        if label in label_ids:
            label_ids.remove(label)


def _add_labels(label_ids: list[str], add: list[str] | None) -> None:
    for label in add or ():
        if label not in label_ids:
            label_ids.append(label)


def _parse_rfc3339(value: str, *, context: str = "") -> datetime:
    """RFC3339 to an aware datetime. Google's offset is mandatory, so a naive string
    comparison orders 09:00-07:00 below 15:00Z when it is in fact later (Fix round 1,
    CINO 2026-09-25 - demonstrated against `list_events`/`query_freebusy`).

    `datetime.fromisoformat` did not accept a trailing `Z` until 3.11 and our floor is 3.10,
    which is why the replacement below exists rather than relying on the parser alone.

    Deliberately not caught and swallowed (Fix round 2, CINO 2026-09-25): a malformed
    timestamp raising loudly beats the fake silently mis-filtering a bad fixture, which is
    exactly the lesson of the naive-string-comparison bug this method exists to fix. What Fix
    round 2 changes is only the *message* - `datetime.fromisoformat`'s own error says
    "Invalid isoformat string" and names neither the value nor where it came from, which is
    useless to a test author staring at twenty fixtures. `context` (an event id, a freebusy
    interval, or a bare query bound) is threaded in by every caller so the re-raised
    `ValueError` names both.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00") if value.endswith("Z") else value)
    except ValueError as exc:
        where = f" ({context})" if context else ""
        raise ValueError(f"not a valid RFC3339 timestamp: {value!r}{where}") from exc


def _event_boundary(node: dict[str, Any] | None, *, context: str = "") -> datetime | None:
    """An event's start or end as an aware datetime, or None if the node carries neither
    `dateTime` nor `date` (a malformed event, which is let through rather than crashing the
    whole listing over one bad fixture).

    All-day events carry `{"date": "2026-10-01"}` rather than a `dateTime`. Convention chosen
    (Fix round 1, CINO 2026-09-25): interpret the bare date as UTC midnight. Google's own
    all-day convention already makes an all-day event's `end.date` the day AFTER its last day,
    so a UTC-midnight boundary lines up with a timed event's boundary in the same overlap test
    without this fake re-deriving that exclusive-end convention itself. UTC rather than the
    calendar's own timezone is acceptable here specifically because this is a double: nothing
    in the ten seed stores carries a calendar's timezone, so there is no timezone to be correct
    *about* - picking one that isn't UTC would need a field this fake does not have anywhere
    else, for a distinction (which hour of a day-boundary edge case an all-day event resolves
    to) no test in this plan depends on.

    `context` is threaded through to `_parse_rfc3339` unchanged (Fix round 2) so a malformed
    `dateTime` or `date` names the event it came from, not just the bad string.
    """
    node = node or {}
    if "dateTime" in node:
        return _parse_rfc3339(node["dateTime"], context=context)
    if "date" in node:
        try:
            return datetime.fromisoformat(node["date"]).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            where = f" ({context})" if context else ""
            raise ValueError(f"not a valid RFC3339 date: {node['date']!r}{where}") from exc
    return None


def _message_haystack(message: dict[str, Any]) -> str:
    """Lower-cased text `search_messages`/`list_threads` match a query against.

    FakeBackend does not implement Gmail's search-operator grammar (`is:unread`, `from:`,
    `has:attachment`, ...) - that grammar lives on Google's servers, not in this library, and a
    fake that tried to reimplement it would be maintaining a second, divergent copy of it. This
    is a plain case-insensitive substring match over the snippet and header values, which is
    enough to exercise pagination and result-shaping in offline tests without pretending to be
    a search engine.
    """
    headers = message.get("payload", {}).get("headers", [])
    parts = [message.get("snippet", ""), *(h.get("value", "") for h in headers)]
    return " ".join(parts).lower()


class FakeBackend:
    """In-memory `Backend` double. Every seed store defaults to an empty container built here
    (never as a mutable default argument) so two instances never share state.

    Eleven stores, per the ruling on the brief's incomplete list of seven (CINO, 2026-09-25):
    `messages`, `threads`, `drafts`, `labels`, `events`, `calendars`, `attachments`, `freebusy`,
    `profile` (a single dict, not keyed by id), `sent` (a list every outbound method appends to),
    and `history` (task 13 - see `list_history`'s own docstring for why an eleventh store was
    added rather than leaving that method's only branch untestable).

    `messages` is the single source of truth for a message's labels and content; `threads`
    is only the registry of which thread ids exist plus any thread-level metadata seeded onto
    them - the messages *in* a thread are derived by filtering `messages` on `threadId`, rather
    than duplicated into a second store that could drift out of sync with the first.
    """

    def __init__(self, *, messages: dict[str, dict[str, Any]] | None = None,
                threads: dict[str, dict[str, Any]] | None = None,
                drafts: dict[str, dict[str, Any]] | None = None,
                labels: dict[str, dict[str, Any]] | None = None,
                events: dict[str, dict[str, Any]] | None = None,
                calendars: dict[str, dict[str, Any]] | None = None,
                attachments: dict[str, dict[str, Any]] | None = None,
                freebusy: dict[str, list[dict[str, Any]]] | None = None,
                profile: dict[str, Any] | None = None,
                sent: list[dict[str, Any]] | None = None,
                history: list[dict[str, Any]] | None = None) -> None:
        self.messages: dict[str, dict[str, Any]] = dict(messages) if messages else {}
        self.threads: dict[str, dict[str, Any]] = dict(threads) if threads else {}
        self.drafts: dict[str, dict[str, Any]] = dict(drafts) if drafts else {}
        self.labels: dict[str, dict[str, Any]] = dict(labels) if labels else {}
        self.events: dict[str, dict[str, Any]] = dict(events) if events else {}
        self.calendars: dict[str, dict[str, Any]] = dict(calendars) if calendars else {}
        self.attachments: dict[str, dict[str, Any]] = dict(attachments) if attachments else {}
        self.freebusy: dict[str, list[dict[str, Any]]] = dict(freebusy) if freebusy else {}
        self.profile: dict[str, Any] = dict(profile) if profile else {}
        self.sent: list[dict[str, Any]] = list(sent) if sent else []
        # Each record is a plain dict with at least an `"id"` key (a decimal historyId string,
        # matching Gmail's own `History.id`) - `list_history` below filters on it numerically.
        # Nothing in this fake APPENDS to this store automatically when a message changes: no
        # task in this plan wires every mutating method to also record a history entry (that
        # would touch every write method above and below this line for a task whose job is the
        # tool surface, not a mailbox-wide event log), so a caller who wants a non-trivial
        # `list_history` result seeds one directly via this constructor argument. See
        # `list_history`'s own docstring for what this does and does not make testable.
        self.history: list[dict[str, Any]] = [dict(h) for h in history] if history else []
        self._seq = 0  # one counter, shared across every id this fake mints

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq}"

    # --- mail: reads -----------------------------------------------------

    def search_messages(self, *, query: str, limit: int = 25,
                        page_token: str | None = None) -> dict[str, Any]:
        matches = [m for m in self.messages.values()
                  if not query or query.lower() in _message_haystack(m)]
        start = int(page_token) if page_token else 0
        page = matches[start:start + limit]
        result: dict[str, Any] = {
            "messages": [{"id": m["id"], "threadId": m.get("threadId", m["id"])} for m in page],
            "resultSizeEstimate": len(matches),
        }
        if start + limit < len(matches):
            result["nextPageToken"] = str(start + limit)
        return result

    def get_message(self, *, message_id: str, fmt: str = "full") -> dict[str, Any]:
        # `fmt` is accepted for interface parity with the real API (full/metadata/minimal) but
        # unused here: the fake always holds and returns whatever was seeded. A task that needs
        # format-dependent trimming can extend this without touching the signature.
        if message_id not in self.messages:
            raise NotFoundError(message_id)
        return copy.deepcopy(self.messages[message_id])

    def _thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        return [m for m in self.messages.values() if m.get("threadId") == thread_id]

    def get_thread(self, *, thread_id: str, fmt: str = "full") -> dict[str, Any]:
        if thread_id not in self.threads:
            raise NotFoundError(thread_id)
        thread = copy.deepcopy(self.threads[thread_id])
        thread["id"] = thread_id
        thread["messages"] = copy.deepcopy(self._thread_messages(thread_id))
        return thread

    def list_threads(self, *, query: str | None = None, limit: int = 25,
                     page_token: str | None = None) -> dict[str, Any]:
        matched = []
        for thread_id in self.threads:
            msgs = self._thread_messages(thread_id)
            haystack = " ".join(_message_haystack(m) for m in msgs)
            if query and query.lower() not in haystack:
                continue
            snippet = msgs[-1].get("snippet", "") if msgs else ""
            matched.append({"id": thread_id, "snippet": snippet})
        start = int(page_token) if page_token else 0
        page = matched[start:start + limit]
        result: dict[str, Any] = {"threads": page, "resultSizeEstimate": len(matched)}
        if start + limit < len(matched):
            result["nextPageToken"] = str(start + limit)
        return result

    def get_attachment(self, *, message_id: str, attachment_id: str) -> dict[str, Any]:
        if message_id not in self.messages:
            raise NotFoundError(message_id)
        if attachment_id not in self.attachments:
            raise NotFoundError(attachment_id)
        return copy.deepcopy(self.attachments[attachment_id])

    def list_labels(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(label) for label in self.labels.values()]

    def list_drafts(self, *, limit: int = 25, page_token: str | None = None) -> dict[str, Any]:
        # Same dict shape as list_threads, matching Gmail's own ListDraftsResponse schema
        # (Fix round 2, CINO 2026-09-25 - see the Protocol declaration's comment for why this
        # is a return-type change made now rather than a page_token bolted onto a bare list).
        drafts = list(self.drafts.values())
        offset = int(page_token) if page_token else 0
        page = drafts[offset:offset + limit]
        result: dict[str, Any] = {"drafts": [copy.deepcopy(d) for d in page],
                                  "resultSizeEstimate": len(drafts)}
        if offset + limit < len(drafts):
            result["nextPageToken"] = str(offset + limit)
        return result

    def get_draft(self, *, draft_id: str) -> dict[str, Any]:
        if draft_id not in self.drafts:
            raise NotFoundError(draft_id)
        return copy.deepcopy(self.drafts[draft_id])

    def list_history(self, *, start_history_id: str) -> dict[str, Any]:
        """Task 13 (deferred from task 3 - see that task's own report): a real `history` store
        now backs this, so "something changed" is a testable branch and not only "nothing did".

        Real `users.history.list` returns every `History` record with an `id` GREATER than
        `startHistoryId`, plus the mailbox's current `historyId`. This mirrors that: records are
        compared numerically (Gmail's own `historyId` is a decimal string, not lexically
        ordered - `"9" > "10"` as strings, never as the ids they represent), and the returned
        `historyId` is the highest id known, or `start_history_id` unchanged when nothing in the
        store is newer (the same "echo the ask back" answer the previous version of this method
        always gave, now the genuinely-correct answer for that one case rather than the only
        answer available).

        A non-numeric `start_history_id` is treated as `0` - the widest possible ask ("everything
        is new") - rather than raising, matching the read tolerance the rest of this fake extends
        to caller-supplied ids elsewhere (`get_message`/`get_thread` raise `NotFoundError` for an
        unknown id, never `ValueError` for a malformed one)."""
        try:
            start = int(start_history_id)
        except (TypeError, ValueError):
            start = 0
        matching = sorted((h for h in self.history if int(h.get("id", 0)) > start),
                          key=lambda h: int(h["id"]))
        latest = max((int(h.get("id", 0)) for h in self.history), default=start)
        return {"history": copy.deepcopy(matching), "historyId": str(max(latest, start))}

    def get_profile(self) -> dict[str, Any]:
        # Decision (item 6, CINO 2026-09-25): raise rather than return {}. The real
        # `users.getProfile` never returns an empty profile for an authenticated account, so a
        # fake that silently handed back {} would be modelling a state Google's API cannot
        # produce - exactly the "behaviour silently differs from the real API" trap called out
        # in the brief. A caller building reply-all recipient filtering from
        # `get_profile()["emailAddress"]` needs a loud failure when nobody seeded a profile,
        # not a quiet None that turns into "don't exclude anyone" and ships a bug.
        if not self.profile:
            raise NotFoundError("profile")
        return copy.deepcopy(self.profile)

    # --- mail: reversible writes ------------------------------------------

    def create_draft(self, *, raw: str) -> dict[str, Any]:
        draft_id = self._next_id("draft")
        draft = {"id": draft_id, "message": {"raw": raw}}
        self.drafts[draft_id] = draft
        return copy.deepcopy(draft)

    def update_draft(self, *, draft_id: str, raw: str) -> dict[str, Any]:
        if draft_id not in self.drafts:
            raise NotFoundError(draft_id)
        self.drafts[draft_id]["message"] = {"raw": raw}
        return copy.deepcopy(self.drafts[draft_id])

    def delete_draft(self, *, draft_id: str) -> dict[str, Any]:
        if draft_id not in self.drafts:
            raise NotFoundError(draft_id)
        del self.drafts[draft_id]
        return {}  # mirrors Google's empty 204 body for a successful delete

    def modify_message_labels(self, *, message_id: str, add: list[str] | None = None,
                              remove: list[str] | None = None) -> dict[str, Any]:
        if message_id not in self.messages:
            raise NotFoundError(message_id)
        message = self.messages[message_id]
        label_ids = message.setdefault("labelIds", [])
        # Removals before additions: a label named in both lists ends up present. Gmail's own
        # API does not define the combined case; "ensure this state" is the more useful reading
        # for a caller than "ensure the opposite."
        _remove_labels(label_ids, remove)
        _add_labels(label_ids, add)
        return copy.deepcopy(message)

    def modify_thread_labels(self, *, thread_id: str, add: list[str] | None = None,
                             remove: list[str] | None = None) -> dict[str, Any]:
        if thread_id not in self.threads:
            raise NotFoundError(thread_id)
        for message in self._thread_messages(thread_id):
            label_ids = message.setdefault("labelIds", [])
            _remove_labels(label_ids, remove)
            _add_labels(label_ids, add)
        return self.get_thread(thread_id=thread_id)

    def archive_message(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, remove=["INBOX"])

    def archive_thread(self, *, thread_id: str) -> dict[str, Any]:
        return self.modify_thread_labels(thread_id=thread_id, remove=["INBOX"])

    def mark_read(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, remove=["UNREAD"])

    def mark_unread(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, add=["UNREAD"])

    def trash_message(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, add=["TRASH"])

    def trash_thread(self, *, thread_id: str) -> dict[str, Any]:
        return self.modify_thread_labels(thread_id=thread_id, add=["TRASH"])

    def untrash_message(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, remove=["TRASH"])

    def untrash_thread(self, *, thread_id: str) -> dict[str, Any]:
        return self.modify_thread_labels(thread_id=thread_id, remove=["TRASH"])

    def mark_spam(self, *, message_id: str) -> dict[str, Any]:
        # Real Gmail moves a spammed message out of the inbox, and restores it on unmark; the
        # double follows that rather than doing the minimal single-label edit (Fix round 1,
        # CINO 2026-09-25).
        return self.modify_message_labels(message_id=message_id, add=["SPAM"], remove=["INBOX"])

    def unmark_spam(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, remove=["SPAM"], add=["INBOX"])

    def create_label(self, *, name: str) -> dict[str, Any]:
        label_id = self._next_id("Label_")
        label = {"id": label_id, "name": name, "type": "user",
                 "messageListVisibility": "show", "labelListVisibility": "labelShow"}
        self.labels[label_id] = label
        return copy.deepcopy(label)

    # --- mail: outbound ----------------------------------------------------

    def _send(self, *, raw: str, thread_id: str | None) -> dict[str, Any]:
        message_id = self._next_id("msg")
        # A fresh send with no thread_id starts its own thread, whose id equals the first
        # message's id - matching Gmail's own documented behaviour for a new conversation.
        resolved_thread_id = thread_id or message_id
        message = {"id": message_id, "threadId": resolved_thread_id,
                   "labelIds": ["SENT"], "raw": raw}
        self.messages[message_id] = message
        self.threads.setdefault(resolved_thread_id, {})
        self.sent.append(copy.deepcopy(message))
        # Real `messages.send`/`drafts.send` never return `raw` in the response body (Fix
        # round 2, CINO 2026-09-25): a caller reading `result["raw"]` would pass every offline
        # test against the old return value and fail against the real API - exactly the
        # divergence this pairing exists to catch. `self.sent` already carries the full
        # message, `raw` included, for whatever a test needs it for; the return value does not.
        return {k: v for k, v in copy.deepcopy(message).items() if k != "raw"}

    def send_message(self, *, raw: str) -> dict[str, Any]:
        return self._send(raw=raw, thread_id=None)

    def send_draft(self, *, draft_id: str) -> dict[str, Any]:
        if draft_id not in self.drafts:
            raise NotFoundError(draft_id)
        raw = self.drafts[draft_id]["message"].get("raw", "")
        del self.drafts[draft_id]
        return self._send(raw=raw, thread_id=None)

    def reply_message(self, *, raw: str, thread_id: str) -> dict[str, Any]:
        if thread_id not in self.threads:
            raise NotFoundError(thread_id)
        return self._send(raw=raw, thread_id=thread_id)

    def reply_all_message(self, *, raw: str, thread_id: str) -> dict[str, Any]:
        # At the storage layer a reply and a reply-all are identical - both append one more
        # sent message to an existing thread. What differs is the To/Cc header set inside
        # `raw`, and composing that (including using get_profile() to avoid replying to
        # oneself) is a concern of the layer that builds `raw`, not of this Backend.
        if thread_id not in self.threads:
            raise NotFoundError(thread_id)
        return self._send(raw=raw, thread_id=thread_id)

    def forward_message(self, *, raw: str) -> dict[str, Any]:
        return self._send(raw=raw, thread_id=None)

    # --- calendar ------------------------------------------------------------

    def list_calendars(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(c) for c in self.calendars.values()]

    def get_calendar(self, *, calendar_id: str) -> dict[str, Any]:
        if calendar_id not in self.calendars:
            raise NotFoundError(calendar_id)
        return copy.deepcopy(self.calendars[calendar_id])

    def list_events(self, *, calendar_id: str = "primary", time_min: str | None = None,
                    time_max: str | None = None, query: str | None = None,
                    limit: int = 25, page_token: str | None = None) -> dict[str, Any]:
        matches = [e for e in self.events.values()
                  if e.get("calendarId", "primary") == calendar_id]
        if query:
            matches = [e for e in matches if query.lower() in
                      (e.get("summary", "") + " " + e.get("description", "")).lower()]
        # Overlap test, not "the event starts inside the window" (Fix round 1, CINO
        # 2026-09-25): the discovery document defines timeMin as an EXCLUSIVE lower bound on
        # an event's END time, and timeMax as an EXCLUSIVE upper bound on its START time. An
        # event already under way when the window opens, or one that outlives it, is still
        # "in" the window under that definition. Filtering on start time alone (the previous
        # version of this method) silently dropped every straddling and in-progress event -
        # exactly the case Task 12's find_free_time needs right, since a meeting already under
        # way must not be offered back as a free slot.
        if time_min:
            lower = _parse_rfc3339(time_min, context="time_min")
            kept = []
            for e in matches:
                end = _event_boundary(e.get("end"), context=f"event {e.get('id', '?')!r} end")
                if end is None or end > lower:
                    kept.append(e)
            matches = kept
        if time_max:
            upper = _parse_rfc3339(time_max, context="time_max")
            kept = []
            for e in matches:
                start = _event_boundary(e.get("start"),
                                        context=f"event {e.get('id', '?')!r} start")
                if start is None or start < upper:
                    kept.append(e)
            matches = kept
        # Real events.list returns items under "items"; matching that key name rather than
        # inventing "events" avoids one more silent divergence from the API this fake stands in
        # for. Paginated the same way search_messages/list_threads are (Fix round 1, CINO
        # 2026-09-25): without page_token, a caller told "here are 25 events" out of 300 had no
        # way to ask for the rest - a silent undercount, not a complete answer.
        offset = int(page_token) if page_token else 0
        page = matches[offset:offset + limit]
        result: dict[str, Any] = {"items": [copy.deepcopy(e) for e in page]}
        if offset + limit < len(matches):
            result["nextPageToken"] = str(offset + limit)
        return result

    def _get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        event = self.events.get(event_id)
        if event is None or event.get("calendarId", "primary") != calendar_id:
            raise NotFoundError(event_id)
        return event

    def get_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._get_event(calendar_id, event_id))

    def query_freebusy(self, *, time_min: str, time_max: str,
                       calendar_ids: list[str]) -> dict[str, Any]:
        """Busy intervals per calendar, overlap-tested against `[time_min, time_max)` the same
        way `list_events` is (Fix round 1, CINO 2026-09-25) - compared as aware datetimes via
        `_parse_rfc3339`, not as raw strings, so an offset like `-07:00` orders correctly
        against a `Z` bound.

        Fix 4 (CINO 2026-09-25): a `calendar_id` this fake has never heard of comes back as
        `{"busy": []}`, same as a calendar this fake knows is genuinely free - it does NOT
        model Google's real per-calendar `errors` entry for an unknown/inaccessible calendar.
        Deliberately not modelled: `create_event`, `list_events`, and this method already treat
        `self.calendars` as unauthoritative (none of them require a `calendar_id` to be
        pre-registered there - see the ruling in the Task 3 report), so there is no existing
        notion of "known calendar" this method could check against without inventing one just
        for itself, which would make calendar existence validated in one Backend method and
        nowhere else. A caller must NOT read an empty `busy` list here as confirmation that the
        calendar exists or is free in production - only as "this fake has no busy interval on
        record for that id."

        A busy interval missing `start` or `end` raises `ValueError` naming the calendar and
        the interval's position, rather than the bare `KeyError` a direct `iv["start"]` would
        give (Fix round 2, CINO 2026-09-25) - a bare `KeyError` here becomes an
        `UnexpectedToolError` whose message the SDK suppresses, same rule as everywhere else in
        this fake.
        """
        lower = _parse_rfc3339(time_min, context="time_min")
        upper = _parse_rfc3339(time_max, context="time_max")
        calendars_out: dict[str, Any] = {}
        for calendar_id in calendar_ids:
            intervals = self.freebusy.get(calendar_id, [])
            overlapping = []
            for index, iv in enumerate(intervals):
                missing = [k for k in ("start", "end") if k not in iv]
                if missing:
                    raise ValueError(
                        f"freebusy interval {index} for calendar {calendar_id!r} is missing "
                        f"{missing}: {iv!r}")
                where = f"freebusy interval {index} for calendar {calendar_id!r}"
                end = _parse_rfc3339(iv["end"], context=f"{where} end")
                start = _parse_rfc3339(iv["start"], context=f"{where} start")
                if end > lower and start < upper:
                    overlapping.append(iv)
            calendars_out[calendar_id] = {"busy": copy.deepcopy(overlapping)}
        return {"timeMin": time_min, "timeMax": time_max, "calendars": calendars_out}

    def create_event(self, *, calendar_id: str, body: dict[str, Any],
                     send_updates: str = "all") -> dict[str, Any]:
        # send_updates is accepted and stored nowhere: it steers Google's attendee-notification
        # emails, and this fake has no delivery model to steer.
        event_id = self._next_id("event")
        event = copy.deepcopy(body)
        event["id"] = event_id
        event["calendarId"] = calendar_id
        event["etag"] = f'"{self._seq}"'
        self.events[event_id] = event
        return copy.deepcopy(event)

    def update_event(self, *, calendar_id: str, event_id: str, body: dict[str, Any],
                     send_updates: str = "all",
                     etag: str | None = None) -> dict[str, Any]:
        event = self._get_event(calendar_id, event_id)
        if etag is not None and event.get("etag") != etag:
            raise ConflictError(
                f"event {event_id!r} etag mismatch: expected {event.get('etag')!r}, got "
                f"{etag!r}")
        event.update(body)  # patch: only keys present in body are replaced
        self._seq += 1
        event["etag"] = f'"{self._seq}"'
        return copy.deepcopy(event)

    def respond_to_event(self, *, calendar_id: str, event_id: str, response: str,
                         comment: str | None = None) -> dict[str, Any]:
        """RSVP as the caller, and only the caller (Fix round 1, CINO 2026-09-25, correcting a
        real defect in this method's first version). Matched on `attendee["self"] is True` -
        the marker Google's own schema puts on the attendee entry that corresponds to the
        calendar being queried - never on email, which fails silently for an alias, a
        delegated calendar, or a `sendAs` address, all ordinary.

        Refuses rather than invents an attendee. The first version of this method appended a
        brand-new attendee entry when no existing one matched `me` - that is not accepting an
        invitation, it is writing yourself onto the organiser's copy of a meeting you were
        never invited to. Two distinct refusals, because they are different facts about the
        event: no attendee list at all means this is not an invitation in the first place (an
        entry on a calendar, not something to accept or decline - delete it instead); an
        attendee list that exists but does not include the caller means the caller genuinely
        was not invited, and responding on someone else's behalf is not this server's call to
        make.

        Writes back only `attendees` (`update_event`'s patch semantics - "only keys present in
        body are replaced" - do the rest), under the etag this same read just obtained, so a
        change to the event between the read and this write is a refusal (`ConflictError` from
        `update_event`), not a silent overwrite. `send_updates="none"`: responding is not an
        edit to the meeting, and nobody needs mail about it.
        """
        event = self.get_event(calendar_id=calendar_id, event_id=event_id)  # a copy + its etag
        attendees = event.get("attendees") or []
        if not attendees:
            raise UnsupportedOperation(
                f"event {event_id!r} has no attendees at all - it is not an invitation, so "
                f"there is nothing to accept or decline. Delete the event instead if you no "
                f"longer want it on your calendar.")
        me_attendee = next((a for a in attendees if a.get("self")), None)
        if me_attendee is None:
            raise UnsupportedOperation(
                f"you are not among the {len(attendees)} attendee(s) on event {event_id!r}. "
                f"Responding on another attendee's behalf is not something this server will "
                f"do.")
        me_attendee["responseStatus"] = response
        if comment is not None:
            me_attendee["comment"] = comment
        return self.update_event(calendar_id=calendar_id, event_id=event_id,
                                 body={"attendees": attendees}, send_updates="none",
                                 etag=event.get("etag"))

    def delete_event(self, *, calendar_id: str, event_id: str,
                     send_updates: str = "all") -> dict[str, Any]:
        self._get_event(calendar_id, event_id)  # validates existence/calendar before deleting
        del self.events[event_id]
        return {}  # mirrors Google's empty 204 body for a successful delete


def _map_http_error(exc: HttpError) -> CsaGoogleError:
    """Translate a googleapiclient `HttpError` into this project's own exception hierarchy, by
    HTTP status. `exc.reason` is already Google's parsed `error.message` field (`HttpError`
    extracts it from the response body itself, in `_get_reason`) - never the raw body, which
    can echo request content (a search query, a recipient address) that should not end up in a
    message that may be logged.

    412 gets its own branch, not a fallthrough to 409: a stale `If-Match` etag on the event
    patch path is a `ConflictError` like 409, but naming it "the event changed underneath this
    request" is what tells a caller what actually happened and what to do next (re-fetch,
    then retry) - a generic conflict message would leave that caller guessing.

    429 and 5xx are `ApiError` with an explicit note that a retry may succeed - this project
    does not add a retry layer of its own (see `ApiBackend`'s docstring for why), so that note
    is the only signal a caller gets that the failure might be transient.
    """
    status = exc.resp.status
    message = exc.reason or "no error message provided"
    detail = f"Google API error {status}: {message}"
    if status == 404:
        return NotFoundError(detail)
    if status == 403:
        return AccessError(detail)
    if status == 401:
        return AuthError(detail)
    if status == 412:
        return ConflictError(
            f"{detail} - the resource changed underneath this request (etag mismatch); "
            f"re-fetch it and retry with the new etag")
    if status == 409:
        return ConflictError(detail)
    if status == 429 or status >= 500:
        return ApiError(f"{detail} - a retry may succeed")
    return ApiError(detail)


def _translate(call: Callable[[], Any]) -> Any:
    """Every `ApiBackend` method routes its `.execute()` through this, so an `HttpError`
    reaching a caller (and, past that, the MCP layer, where it would otherwise become an
    `UnexpectedToolError` whose message the SDK suppresses) always arrives translated."""
    try:
        return call()
    except HttpError as exc:
        raise _map_http_error(exc) from exc


class ApiBackend:
    """`Backend` over real Gmail and Calendar API calls (`google-api-python-client`).

    Every method's real work is one or two discovery-client calls threaded through
    `_translate`, which is what turns Google's `HttpError` into this project's own hierarchy
    (see `_map_http_error`). Construct directly from already-built discovery services
    (mainly for tests, which hand it a recording double in place of a real service), or via
    `from_credentials`, which builds both from a single `Credentials` object.

    **`userId="me"` is threaded through one seam, `_mail`, rather than repeated at every Gmail
    call site.** Every `users.*` Gmail method requires it; a call site that forgot it would
    still often work by accident (many discovery methods default an omitted `userId` to
    `"me"` themselves) but that is Google's leniency, not a guarantee this project should rely
    on 25 separate times. `_mail(bound_method, **kwargs)` supplies it once and forwards the
    rest of `kwargs` untouched - a call site cannot omit it because it never has the chance to
    supply it at all.

    **Retry: none is added here, and none happens by default.** `HttpRequest.execute` takes a
    `num_retries` argument that defaults to `0`; verified directly against the installed
    `google-api-python-client` (its own `execute` docstring: "If zero (default), we attempt
    the request only once") - with `num_retries=0`, `_retry_request` does not retry at all,
    on any status, including 429/5xx. This project never passes `num_retries`, so an
    `ApiBackend` call fails on the first bad response every time; the 429/5xx branch of
    `_map_http_error` says "a retry may succeed" precisely because nothing below this class
    will attempt one. Adding a retry layer (which call sites are safe to retry - reads and
    idempotent writes, not `send_message` - and with what backoff) is a deliberate later
    decision, not an accidental gap; this task does not add one.

    **Pagination.** `search_messages`'s signature accepts `page_token`, so a caller can drive
    Gmail's own paging loop and a truncated result is distinguishable from a complete one via
    `nextPageToken` in the raw response (passed through unchanged - real `messages.list`
    already returns that key, the same shape `FakeBackend` mirrors). `list_events` cannot: the
    `Backend` Protocol's `list_events` signature has no `page_token` parameter, even though
    Calendar's real `events.list` paginates and can return a `nextPageToken` in exactly the
    same way. `ApiBackend.list_events` passes the raw response through unchanged, so a
    truncated result IS still distinguishable (the token is there in the dict), but nothing in
    this Protocol lets a caller ask for the next page - a caller told "here are 25 events" with
    no way to request the other 275 has been given a silent undercount for any calendar with
    more events in the window than `limit`. That is a Protocol-level gap, not something this
    task's `ApiBackend` can fix without changing `Backend` itself (which `FakeBackend` and
    every existing test also implement) - flagged here for a later task, not fixed in this one.

    **Resumable upload.** `_mime.upload_strategy` decides simple-vs-resumable from the size of
    the already-built API `raw` payload. `ApiBackend` calls it on every outbound/draft path
    and raises `ApiError` for the resumable case rather than silently sending a request Google
    will reject: a resumable upload session (`POST .../upload/gmail/v1/users/me/messages/send`
    with `uploadType=resumable`, an initial empty-body request to obtain a `Location` session
    URI, then one or more `PUT`s of the payload in chunks, each acknowledged before the next)
    is materially more machinery than this task can carry - a second HTTP round trip shape
    outside anything `googleapiclient`'s generated `Resource.send`/`Resource.create` methods
    do for you automatically from a plain `body={"raw": ...}` call. A message or draft this
    large is also already unusual (5 MB of base64url payload is roughly 3.5 MB of RFC822
    content - most messages, even with attachments, stay under it via `_mime.MESSAGE_LIMIT`'s
    much larger 25 MB ceiling), so refusing loudly here is a real gap to close in a later task,
    not a silent send-and-hope.
    """

    def __init__(self, gmail_service: Any, calendar_service: Any) -> None:
        self._gmail = gmail_service
        self._cal = calendar_service

    @classmethod
    def from_credentials(cls, credentials: Credentials) -> ApiBackend:
        gmail_service = discovery.build("gmail", "v1", credentials=credentials)
        calendar_service = discovery.build("calendar", "v3", credentials=credentials)
        return cls(gmail_service, calendar_service)

    def _mail(self, method: Callable[..., Any], **kwargs: Any) -> Any:
        """`method` is a bound Gmail discovery resource method (e.g.
        `self._gmail.users().messages().get`), not yet called. Supplies `userId="me"` and
        forwards everything else through `_translate`."""
        return _translate(method(userId="me", **kwargs).execute)

    def _cal_execute(self, request: Any) -> Any:
        return _translate(request.execute)

    def _send(self, *, raw: str, thread_id: str | None = None) -> dict[str, Any]:
        if upload_strategy(raw) == "resumable":
            raise ApiError(
                "this message's payload is at or above the 5 MB simple-upload threshold "
                "(_mime.upload_strategy); resumable upload is not implemented by this backend "
                "and a plain send would risk being rejected or truncated by Google")
        body: dict[str, Any] = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        return self._mail(self._gmail.users().messages().send, body=body)

    # --- mail: reads -----------------------------------------------------

    def search_messages(self, *, query: str, limit: int = 25,
                        page_token: str | None = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"q": query, "maxResults": limit}
        if page_token:
            kwargs["pageToken"] = page_token
        return self._mail(self._gmail.users().messages().list, **kwargs)

    def get_message(self, *, message_id: str, fmt: str = "full") -> dict[str, Any]:
        return self._mail(self._gmail.users().messages().get, id=message_id, format=fmt)

    def get_thread(self, *, thread_id: str, fmt: str = "full") -> dict[str, Any]:
        return self._mail(self._gmail.users().threads().get, id=thread_id, format=fmt)

    def list_threads(self, *, query: str | None = None, limit: int = 25,
                     page_token: str | None = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"maxResults": limit}
        if query:
            kwargs["q"] = query
        if page_token:
            kwargs["pageToken"] = page_token
        return self._mail(self._gmail.users().threads().list, **kwargs)

    def get_attachment(self, *, message_id: str, attachment_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().messages().attachments().get,
                          messageId=message_id, id=attachment_id)

    def list_labels(self) -> list[dict[str, Any]]:
        result = self._mail(self._gmail.users().labels().list)
        return result.get("labels", [])

    def list_drafts(self, *, limit: int = 25, page_token: str | None = None) -> dict[str, Any]:
        # Passed through unchanged, same as list_events/list_threads - nextPageToken travels
        # with it now instead of being received and discarded (Fix round 2, CINO 2026-09-25).
        kwargs: dict[str, Any] = {"maxResults": limit}
        if page_token:
            kwargs["pageToken"] = page_token
        return self._mail(self._gmail.users().drafts().list, **kwargs)

    def get_draft(self, *, draft_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().drafts().get, id=draft_id)

    def list_history(self, *, start_history_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().history().list, startHistoryId=start_history_id)

    def get_profile(self) -> dict[str, Any]:
        return self._mail(self._gmail.users().getProfile)

    # --- mail: reversible writes ------------------------------------------

    def create_draft(self, *, raw: str) -> dict[str, Any]:
        if upload_strategy(raw) == "resumable":
            raise ApiError(
                "this draft's payload is at or above the 5 MB simple-upload threshold "
                "(_mime.upload_strategy); resumable upload is not implemented by this backend "
                "and a plain create would risk being rejected or truncated by Google")
        return self._mail(self._gmail.users().drafts().create, body={"message": {"raw": raw}})

    def update_draft(self, *, draft_id: str, raw: str) -> dict[str, Any]:
        if upload_strategy(raw) == "resumable":
            raise ApiError(
                "this draft's payload is at or above the 5 MB simple-upload threshold "
                "(_mime.upload_strategy); resumable upload is not implemented by this backend "
                "and a plain update would risk being rejected or truncated by Google")
        return self._mail(self._gmail.users().drafts().update, id=draft_id,
                          body={"message": {"raw": raw}})

    def delete_draft(self, *, draft_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().drafts().delete, id=draft_id)

    def modify_message_labels(self, *, message_id: str, add: list[str] | None = None,
                              remove: list[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        return self._mail(self._gmail.users().messages().modify, id=message_id, body=body)

    def modify_thread_labels(self, *, thread_id: str, add: list[str] | None = None,
                             remove: list[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        return self._mail(self._gmail.users().threads().modify, id=thread_id, body=body)

    def archive_message(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, remove=["INBOX"])

    def archive_thread(self, *, thread_id: str) -> dict[str, Any]:
        return self.modify_thread_labels(thread_id=thread_id, remove=["INBOX"])

    def mark_read(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, remove=["UNREAD"])

    def mark_unread(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, add=["UNREAD"])

    def trash_message(self, *, message_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().messages().trash, id=message_id)

    def trash_thread(self, *, thread_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().threads().trash, id=thread_id)

    def untrash_message(self, *, message_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().messages().untrash, id=message_id)

    def untrash_thread(self, *, thread_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().threads().untrash, id=thread_id)

    def mark_spam(self, *, message_id: str) -> dict[str, Any]:
        # Real Gmail moves a spammed message out of the inbox, same as FakeBackend (Fix round
        # 1, CINO 2026-09-25) - there is no dedicated spam/unspam endpoint, so this is a label
        # modify like archive/read, not a call of its own.
        return self.modify_message_labels(message_id=message_id, add=["SPAM"], remove=["INBOX"])

    def unmark_spam(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, remove=["SPAM"], add=["INBOX"])

    def create_label(self, *, name: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().labels().create, body={"name": name})

    # --- mail: outbound ----------------------------------------------------

    def send_message(self, *, raw: str) -> dict[str, Any]:
        return self._send(raw=raw)

    def send_draft(self, *, draft_id: str) -> dict[str, Any]:
        return self._mail(self._gmail.users().drafts().send, body={"id": draft_id})

    def reply_message(self, *, raw: str, thread_id: str) -> dict[str, Any]:
        return self._send(raw=raw, thread_id=thread_id)

    def reply_all_message(self, *, raw: str, thread_id: str) -> dict[str, Any]:
        # Identical to reply_message at this layer, same as FakeBackend: what differs is the
        # To/Cc header set already baked into `raw` by whatever built it, not anything this
        # Backend method does.
        return self._send(raw=raw, thread_id=thread_id)

    def forward_message(self, *, raw: str) -> dict[str, Any]:
        return self._send(raw=raw)

    # --- calendar ------------------------------------------------------------

    def list_calendars(self) -> list[dict[str, Any]]:
        result = self._cal_execute(self._cal.calendarList().list())
        return result.get("items", [])

    def get_calendar(self, *, calendar_id: str) -> dict[str, Any]:
        return self._cal_execute(self._cal.calendars().get(calendarId=calendar_id))

    def list_events(self, *, calendar_id: str = "primary", time_min: str | None = None,
                    time_max: str | None = None, query: str | None = None,
                    limit: int = 25, page_token: str | None = None) -> dict[str, Any]:
        # timeMin/timeMax/q are server-side parameters here, not client-side filters - Google's
        # own overlap semantics for timeMin/timeMax (documented, and the same ones FakeBackend
        # reproduces for the offline suite) apply on its servers. Passed through unchanged
        # rather than re-filtered, so the two implementations cannot silently disagree.
        kwargs: dict[str, Any] = {"calendarId": calendar_id, "maxResults": limit}
        if time_min:
            kwargs["timeMin"] = time_min
        if time_max:
            kwargs["timeMax"] = time_max
        if query:
            kwargs["q"] = query
        if page_token:
            kwargs["pageToken"] = page_token
        return self._cal_execute(self._cal.events().list(**kwargs))

    def get_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]:
        return self._cal_execute(self._cal.events().get(calendarId=calendar_id, eventId=event_id))

    def query_freebusy(self, *, time_min: str, time_max: str,
                       calendar_ids: list[str]) -> dict[str, Any]:
        body = {"timeMin": time_min, "timeMax": time_max,
                "items": [{"id": cid} for cid in calendar_ids]}
        return self._cal_execute(self._cal.freebusy().query(body=body))

    def create_event(self, *, calendar_id: str, body: dict[str, Any],
                     send_updates: str = "all") -> dict[str, Any]:
        return self._cal_execute(self._cal.events().insert(
            calendarId=calendar_id, body=body, sendUpdates=send_updates))

    def update_event(self, *, calendar_id: str, event_id: str, body: dict[str, Any],
                     send_updates: str = "all",
                     etag: str | None = None) -> dict[str, Any]:
        # A patch, not a put - only keys present in `body` are replaced, matching FakeBackend.
        # `If-Match` is the etag concurrency path: set on the request object before `.execute()`
        # when a caller supplied one, omitted entirely otherwise, so a stale write is refused by
        # Google (412) rather than silently overwriting a change this caller never saw - handled
        # generically by `_map_http_error`.
        req = self._cal.events().patch(calendarId=calendar_id, eventId=event_id,
                                       body=body, sendUpdates=send_updates)
        if etag:
            req.headers["If-Match"] = etag
        return self._cal_execute(req)

    def respond_to_event(self, *, calendar_id: str, event_id: str, response: str,
                         comment: str | None = None) -> dict[str, Any]:
        """No native "RSVP" endpoint exists on the real Calendar API - responding is a
        read-modify-write against the `attendees` array, same shape and same refusals as
        `FakeBackend.respond_to_event` (see its docstring for the full reasoning; the two must
        agree exactly - the offline suite otherwise tests behaviour production does not have).

        Matched on `attendee.get("self")`, never on email: Google marks the attendee
        corresponding to the calendar being queried with `"self": true`, and that is the
        authoritative marker - email matching breaks for an alias, a delegated calendar, or a
        `sendAs` address. No attendee list at all -> not an invitation, `UnsupportedOperation`
        naming the event and pointing at delete instead. An attendee list that exists but does
        not include `self` -> genuinely not invited, `UnsupportedOperation` refusing to respond
        on someone else's behalf. `getProfile` is not called at all - the `self` marker is
        already on the read, so this needs one fewer round trip than the email-matching version
        it replaces.

        The patch body is `{"attendees": attendees}` only - never the whole event, which would
        silently turn a response into an edit and race whoever moved the meeting - carries the
        etag this same read obtained as `If-Match` (a change between read and write becomes a
        412 -> `ConflictError`, not a silent overwrite), and sends with `sendUpdates="none"`:
        responding is not an edit to the meeting, and nobody needs mail about it.
        """
        event = self.get_event(calendar_id=calendar_id, event_id=event_id)
        attendees = event.get("attendees") or []
        if not attendees:
            raise UnsupportedOperation(
                f"event {event_id!r} has no attendees at all - it is not an invitation, so "
                f"there is nothing to accept or decline. Delete the event instead if you no "
                f"longer want it on your calendar.")
        me_attendee = next((a for a in attendees if a.get("self")), None)
        if me_attendee is None:
            raise UnsupportedOperation(
                f"you are not among the {len(attendees)} attendee(s) on event {event_id!r}. "
                f"Responding on another attendee's behalf is not something this server will "
                f"do.")
        me_attendee["responseStatus"] = response
        if comment is not None:
            me_attendee["comment"] = comment
        req = self._cal.events().patch(calendarId=calendar_id, eventId=event_id,
                                       body={"attendees": attendees}, sendUpdates="none")
        etag = event.get("etag")
        if etag:
            req.headers["If-Match"] = etag
        return self._cal_execute(req)

    def delete_event(self, *, calendar_id: str, event_id: str,
                     send_updates: str = "all") -> dict[str, Any]:
        return self._cal_execute(self._cal.events().delete(
            calendarId=calendar_id, eventId=event_id, sendUpdates=send_updates))
