"""Backend seam. `Backend` is the Protocol every capability in `policy._GATES` gates by name;
`FakeBackend` is the in-memory double the whole offline test suite runs against. A real
`ApiBackend` (Gmail + Calendar over `google-api-python-client`) arrives in a later task and
must satisfy the same Protocol without either side needing to change.

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
from typing import Any, Protocol

from .exceptions import ConflictError, NotFoundError


class Backend(Protocol):
    # --- mail: reads ---
    def search_messages(self, *, query: str, limit: int = 25,
                        page_token: str | None = None) -> dict[str, Any]: ...
    def get_message(self, *, message_id: str, fmt: str = "full") -> dict[str, Any]: ...
    def get_thread(self, *, thread_id: str, fmt: str = "full") -> dict[str, Any]: ...
    def list_threads(self, *, query: str | None = None, limit: int = 25) -> dict[str, Any]: ...
    def get_attachment(self, *, message_id: str, attachment_id: str) -> dict[str, Any]: ...
    def list_labels(self) -> list[dict[str, Any]]: ...
    def list_drafts(self, *, limit: int = 25) -> list[dict[str, Any]]: ...
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
                    limit: int = 25) -> dict[str, Any]: ...
    def get_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]: ...
    def query_freebusy(self, *, time_min: str, time_max: str,
                       calendar_ids: list[str]) -> dict[str, Any]: ...
    def create_event(self, *, calendar_id: str, body: dict[str, Any],
                     send_updates: str = "all") -> dict[str, Any]: ...
    def update_event(self, *, calendar_id: str, event_id: str, body: dict[str, Any],
                     send_updates: str = "all",
                     etag: str | None = None) -> dict[str, Any]: ...
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

    Ten stores, per the ruling on the brief's incomplete list of seven (CINO, 2026-09-25):
    `messages`, `threads`, `drafts`, `labels`, `events`, `calendars`, `attachments`, `freebusy`,
    `profile` (a single dict, not keyed by id), and `sent` (a list every outbound method
    appends to).

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
                sent: list[dict[str, Any]] | None = None) -> None:
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

    def list_threads(self, *, query: str | None = None, limit: int = 25) -> dict[str, Any]:
        matched = []
        for thread_id in self.threads:
            msgs = self._thread_messages(thread_id)
            haystack = " ".join(_message_haystack(m) for m in msgs)
            if query and query.lower() not in haystack:
                continue
            snippet = msgs[-1].get("snippet", "") if msgs else ""
            matched.append({"id": thread_id, "snippet": snippet})
        return {"threads": matched[:limit], "resultSizeEstimate": len(matched)}

    def get_attachment(self, *, message_id: str, attachment_id: str) -> dict[str, Any]:
        if message_id not in self.messages:
            raise NotFoundError(message_id)
        if attachment_id not in self.attachments:
            raise NotFoundError(attachment_id)
        return copy.deepcopy(self.attachments[attachment_id])

    def list_labels(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(label) for label in self.labels.values()]

    def list_drafts(self, *, limit: int = 25) -> list[dict[str, Any]]:
        return [copy.deepcopy(d) for d in list(self.drafts.values())[:limit]]

    def get_draft(self, *, draft_id: str) -> dict[str, Any]:
        if draft_id not in self.drafts:
            raise NotFoundError(draft_id)
        return copy.deepcopy(self.drafts[draft_id])

    def list_history(self, *, start_history_id: str) -> dict[str, Any]:
        # No task in this plan seeds a history store, so there is nothing to diff against.
        # Reporting "nothing changed since start_history_id" (rather than raising) is the
        # correct default for an id Gmail's real history feed would also just echo back when
        # asked for changes since itself - a future task adding real history semantics should
        # add a `history` store and change this method, not work around an exception here.
        return {"history": [], "historyId": start_history_id}

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
        return self.modify_message_labels(message_id=message_id, add=["SPAM"])

    def unmark_spam(self, *, message_id: str) -> dict[str, Any]:
        return self.modify_message_labels(message_id=message_id, remove=["SPAM"])

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
        return copy.deepcopy(message)

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

    @staticmethod
    def _event_time(node: dict[str, Any] | None) -> str:
        node = node or {}
        return node.get("dateTime") or node.get("date") or ""

    def list_events(self, *, calendar_id: str = "primary", time_min: str | None = None,
                    time_max: str | None = None, query: str | None = None,
                    limit: int = 25) -> dict[str, Any]:
        matches = [e for e in self.events.values()
                  if e.get("calendarId", "primary") == calendar_id]
        if query:
            matches = [e for e in matches if query.lower() in
                      (e.get("summary", "") + " " + e.get("description", "")).lower()]
        if time_min:
            matches = [e for e in matches if self._event_time(e.get("end")) >= time_min]
        if time_max:
            matches = [e for e in matches if self._event_time(e.get("start")) <= time_max]
        # Real events.list returns items under "items"; matching that key name rather than
        # inventing "events" avoids one more silent divergence from the API this fake stands in
        # for.
        return {"items": [copy.deepcopy(e) for e in matches[:limit]]}

    def _get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        event = self.events.get(event_id)
        if event is None or event.get("calendarId", "primary") != calendar_id:
            raise NotFoundError(event_id)
        return event

    def get_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._get_event(calendar_id, event_id))

    def query_freebusy(self, *, time_min: str, time_max: str,
                       calendar_ids: list[str]) -> dict[str, Any]:
        calendars_out: dict[str, Any] = {}
        for calendar_id in calendar_ids:
            intervals = self.freebusy.get(calendar_id, [])
            overlapping = [iv for iv in intervals
                           if iv["end"] > time_min and iv["start"] < time_max]
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
        event = self._get_event(calendar_id, event_id)
        # Responding is inherently "as someone" - the fake models that literally via
        # get_profile() rather than guessing which attendee is "me" (e.g. via a "self" flag a
        # fixture author would otherwise have to remember to set on every seeded event).
        me = self.get_profile().get("emailAddress")
        attendees = event.setdefault("attendees", [])
        for attendee in attendees:
            if attendee.get("email") == me:
                attendee["responseStatus"] = response
                if comment is not None:
                    attendee["comment"] = comment
                break
        else:
            new_attendee = {"email": me, "responseStatus": response}
            if comment is not None:
                new_attendee["comment"] = comment
            attendees.append(new_attendee)
        return copy.deepcopy(event)

    def delete_event(self, *, calendar_id: str, event_id: str,
                     send_updates: str = "all") -> dict[str, Any]:
        self._get_event(calendar_id, event_id)  # validates existence/calendar before deleting
        del self.events[event_id]
        return {}  # mirrors Google's empty 204 body for a successful delete
