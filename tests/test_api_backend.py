import pytest
from googleapiclient.errors import HttpError

from csa_google_gmail_calendar._mime import SIMPLE_UPLOAD_LIMIT
from csa_google_gmail_calendar.backend import ApiBackend, FakeBackend
from csa_google_gmail_calendar.exceptions import (
    AccessError,
    ApiError,
    AuthError,
    ConflictError,
    NotFoundError,
    UnsupportedOperation,
)


class _Req:
    def __init__(self, rec, name, kwargs, result=None, error=None):
        self.headers = {}
        self._rec = rec
        self._name = name
        self._kwargs = kwargs
        self._result = result
        self._error = error

    def execute(self):
        self._rec.append((self._name, self._kwargs, dict(self.headers)))
        if self._error:
            raise self._error
        return self._result if self._result is not None else {}


# The names that are themselves the terminal, request-returning call - mirroring
# googleapiclient's own generated surface, where e.g. `service.users().messages().list(...)`
# has `users`/`messages` as resource accessors (they always return a sub-resource, whatever
# they're called with) and `list` as the leaf that returns an `HttpRequest`.
#
# Fix round 2 (CINO 2026-09-25): the original heuristic - "terminal if kwargs are non-empty or
# the path has 2+ dots" - is wrong for any zero-kwarg leaf call at a one-dot path, and
# `list_calendars` (`calendarList().list()`) is exactly that shape. Under the old heuristic it
# silently returned another `_Chain` instead of a request, which no attribute of `_Chain`
# forbids accessing - `.execute` just resolved to another callable - so the failure surfaced
# two calls later, as `'_Req' object has no attribute 'get'`, nowhere near its real cause. A
# test double that lies in the pessimistic direction (working code looks broken) is worse than
# one that lies optimistic (broken code looks fine reads as a bug in the code under test) - it
# invites "fixing" correct production code to satisfy a broken test.
_LEAF_METHODS = frozenset({
    "list", "get", "getProfile", "create", "update", "delete", "modify",
    "trash", "untrash", "send", "query", "insert", "patch",
})


class _Chain:
    """Records the full call path so a test can assert users().messages().modify() args.

    Terminality is decided by `_LEAF_METHODS` membership, not by counting dots or kwargs - see
    that constant's docstring for why the old heuristic was wrong.
    """

    def __init__(self, rec, path="", results=None):
        self._rec, self._path, self._results = rec, path, results or {}

    def __getattr__(self, name):
        def call(**kwargs):
            path = f"{self._path}.{name}".lstrip(".")
            if name in _LEAF_METHODS:
                return _Req(self._rec, path, kwargs, self._results.get(path))
            return _Chain(self._rec, path, self._results)
        return call


def test_search_passes_q_and_maxResults_to_gmail():
    rec = []
    ab = ApiBackend(_Chain(rec), _Chain([]))
    ab.search_messages(query="from:a@example.com", limit=7)
    name, kwargs, _ = rec[0]
    assert kwargs["q"] == "from:a@example.com" and kwargs["maxResults"] == 7
    assert kwargs["userId"] == "me"


def test_archive_removes_inbox_only():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).archive_message(message_id="m1")
    _, kwargs, _ = rec[0]
    assert kwargs["body"] == {"removeLabelIds": ["INBOX"]}


def test_mark_read_removes_unread():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).mark_read(message_id="m1")
    assert rec[0][1]["body"] == {"removeLabelIds": ["UNREAD"]}


def test_update_event_sends_if_match_when_an_etag_is_given():
    """Optimistic concurrency. Without it, two concurrent patches silently lose one."""
    rec = []
    ApiBackend(_Chain([]), _Chain(rec)).update_event(
        calendar_id="primary", event_id="e1", body={"summary": "x"}, etag='"abc"')
    _, _, headers = rec[0]
    assert headers["If-Match"] == '"abc"'


def test_update_event_omits_if_match_when_no_etag():
    rec = []
    ApiBackend(_Chain([]), _Chain(rec)).update_event(
        calendar_id="primary", event_id="e1", body={"summary": "x"})
    assert "If-Match" not in rec[0][2]


def _http_error(status: int, message: str) -> HttpError:
    class R:
        pass
    R.status = status
    R.reason = message
    body = f'{{"error":{{"message":"{message}"}}}}'.encode()
    return HttpError(R(), body)


def _boom(err: HttpError) -> "_Chain":
    """A `_Chain` whose every terminal call raises `err` instead of recording a result -
    building the request never fails (matching the real discovery client, which only raises
    from `.execute()`), only the eventual `.execute()` does."""

    class Boom(_Chain):
        def __getattr__(self, name):
            def call(**kw):
                path = f"{self._path}.{name}".lstrip(".")
                if name in _LEAF_METHODS:
                    return _Req([], path, kw, error=err)
                return Boom([], path)
            return call
    return Boom([])


def test_a_404_becomes_notfound():
    err = _http_error(404, "Not Found")
    with pytest.raises(NotFoundError):
        ApiBackend(_boom(err), _Chain([])).get_message(message_id="gone")


def test_a_403_becomes_accesserror():
    err = _http_error(403, "Insufficient Permission")
    with pytest.raises(AccessError):
        ApiBackend(_boom(err), _Chain([])).get_message(message_id="m1")


def test_a_401_becomes_autherror():
    err = _http_error(401, "Invalid Credentials")
    with pytest.raises(AuthError):
        ApiBackend(_boom(err), _Chain([])).get_profile()


def test_a_409_becomes_conflicterror():
    err = _http_error(409, "Conflict")
    with pytest.raises(ConflictError):
        ApiBackend(_Chain([]), _boom(err)).create_event(calendar_id="primary", body={})


def test_a_412_becomes_conflicterror_naming_the_etag_mismatch():
    """The etag path: a stale If-Match must not read as an ordinary 409 - the message has to
    say the resource changed underneath the request, or a caller has no idea a re-fetch would
    fix it."""
    err = _http_error(412, "Precondition Failed")
    with pytest.raises(ConflictError, match="changed underneath"):
        ApiBackend(_Chain([]), _boom(err)).update_event(
            calendar_id="primary", event_id="e1", body={"summary": "x"}, etag='"abc"')


def test_a_429_becomes_apierror_mentioning_retry():
    err = _http_error(429, "Rate Limit Exceeded")
    with pytest.raises(ApiError, match="retry"):
        ApiBackend(_boom(err), _Chain([])).search_messages(query="x")


def test_a_500_becomes_apierror_mentioning_retry():
    err = _http_error(500, "Internal Error")
    with pytest.raises(ApiError, match="retry"):
        ApiBackend(_Chain([]), _boom(err)).get_event(calendar_id="primary", event_id="e1")


def test_an_unmapped_4xx_becomes_apierror_without_a_retry_note():
    err = _http_error(400, "Bad Request")
    with pytest.raises(ApiError) as excinfo:
        ApiBackend(_boom(err), _Chain([])).get_message(message_id="m1")
    assert "retry" not in str(excinfo.value)


def test_error_message_does_not_leak_the_raw_body():
    """Only the status and Google's parsed error.message travel into the exception - never the
    raw response body, which can echo request content (a search query, a recipient address)."""
    class R:
        status = 404
        reason = "Not Found"
    body = (b'{"error":{"message":"Not Found",'
            b'"secret_query_echo":"from:someone@example.com"}}')
    err = HttpError(R(), body)
    with pytest.raises(NotFoundError) as excinfo:
        ApiBackend(_boom(err), _Chain([])).get_message(message_id="m1")
    assert "secret_query_echo" not in str(excinfo.value)
    assert "someone@example.com" not in str(excinfo.value)


def test_userid_me_is_always_supplied_even_with_no_other_kwargs():
    """get_profile takes no arguments of its own - the only kwarg on the wire is userId, and it
    must still be there."""
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).get_profile()
    assert rec[0][1] == {"userId": "me"}


def test_mark_spam_adds_spam_and_removes_inbox():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).mark_spam(message_id="m1")
    assert rec[0][1]["body"] == {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}


def test_unmark_spam_removes_spam_and_adds_inbox():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).unmark_spam(message_id="m1")
    assert rec[0][1]["body"] == {"addLabelIds": ["INBOX"], "removeLabelIds": ["SPAM"]}


def test_search_messages_passes_through_page_token():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).search_messages(query="x", page_token="42")
    assert rec[0][1]["pageToken"] == "42"


def test_list_events_passes_time_min_and_time_max_through_without_client_side_filtering():
    """These are server-side parameters for the real API - ApiBackend must not re-filter,
    only forward them."""
    rec = []
    ApiBackend(_Chain([]), _Chain(rec)).list_events(
        calendar_id="cal1", time_min="2026-01-01T00:00:00Z", time_max="2026-01-02T00:00:00Z")
    _, kwargs, _ = rec[0]
    assert kwargs["timeMin"] == "2026-01-01T00:00:00Z"
    assert kwargs["timeMax"] == "2026-01-02T00:00:00Z"
    assert kwargs["calendarId"] == "cal1"


def test_send_message_over_the_simple_upload_threshold_raises_apierror():
    """upload_strategy's resumable case is refused loudly rather than sent and rejected by
    Google - see ApiBackend's docstring for why resumable upload isn't implemented here."""
    oversized_raw = "A" * (SIMPLE_UPLOAD_LIMIT + 1)
    with pytest.raises(ApiError, match="resumable"):
        ApiBackend(_Chain([]), _Chain([])).send_message(raw=oversized_raw)


def test_send_message_under_the_threshold_is_sent_normally():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).send_message(raw="c2hvcnQ=")
    assert rec[0][1]["body"] == {"raw": "c2hvcnQ="}


# --- respond_to_event: Fix round 1 (CINO 2026-09-25) -------------------------------------
#
# Same contract as FakeBackend.respond_to_event - see its docstring in backend.py. rec[0] is
# always the read (events.get), rec[1] the write (events.patch), since respond_to_event does
# exactly one of each in that order.

def test_respond_to_event_patches_only_attendees_with_if_match_and_send_updates_none():
    seeded_event = {
        "id": "e1", "etag": '"7"',
        "attendees": [
            {"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
            {"email": "other@example.com", "responseStatus": "needsAction"},
        ],
    }
    rec = []
    cal = _Chain(rec, results={"events.get": seeded_event})
    ApiBackend(_Chain([]), cal).respond_to_event(
        calendar_id="primary", event_id="e1", response="accepted", comment="see you there")

    name, kwargs, headers = rec[1]
    assert name == "events.patch"
    assert list(kwargs["body"].keys()) == ["attendees"]
    attendees = {a["email"]: a for a in kwargs["body"]["attendees"]}
    assert attendees["me@example.com"]["responseStatus"] == "accepted"
    assert attendees["me@example.com"]["comment"] == "see you there"
    # The other attendee is untouched - no responseStatus change, no comment added.
    assert attendees["other@example.com"]["responseStatus"] == "needsAction"
    assert "comment" not in attendees["other@example.com"]
    assert kwargs["sendUpdates"] == "none"
    assert headers["If-Match"] == '"7"'


def test_respond_to_event_with_no_self_attendee_refuses_and_never_patches():
    seeded_event = {
        "id": "e1", "etag": '"1"',
        "attendees": [{"email": "a@example.com", "responseStatus": "needsAction"},
                      {"email": "b@example.com", "responseStatus": "needsAction"}],
    }
    rec = []
    cal = _Chain(rec, results={"events.get": seeded_event})
    with pytest.raises(UnsupportedOperation, match="not among the 2 attendee"):
        ApiBackend(_Chain([]), cal).respond_to_event(
            calendar_id="primary", event_id="e1", response="accepted")
    assert len(rec) == 1  # only the read happened - the write was never sent


def test_respond_to_event_with_no_attendees_at_all_refuses_with_a_different_message():
    seeded_event = {"id": "e1", "etag": '"1"'}
    rec = []
    cal = _Chain(rec, results={"events.get": seeded_event})
    with pytest.raises(UnsupportedOperation, match="no attendees at all"):
        ApiBackend(_Chain([]), cal).respond_to_event(
            calendar_id="primary", event_id="e1", response="accepted")
    assert len(rec) == 1


def test_respond_to_event_omits_if_match_when_the_read_event_has_no_etag():
    seeded_event = {"id": "e1", "attendees": [{"email": "me@example.com", "self": True}]}
    rec = []
    cal = _Chain(rec, results={"events.get": seeded_event})
    ApiBackend(_Chain([]), cal).respond_to_event(
        calendar_id="primary", event_id="e1", response="accepted")
    _, _, headers = rec[1]
    assert "If-Match" not in headers


# --- list_events / list_threads pagination: Fix round 1 (CINO 2026-09-25) ----------------

def test_list_events_passes_through_page_token():
    rec = []
    ApiBackend(_Chain([]), _Chain(rec)).list_events(calendar_id="primary", page_token="tok1")
    assert rec[0][1]["pageToken"] == "tok1"


def test_list_threads_passes_through_page_token():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).list_threads(page_token="tok2")
    assert rec[0][1]["pageToken"] == "tok2"


# --- list_calendars: the zero-kwarg, one-dot leaf call - Fix round 2 (CINO 2026-09-25) ---

def test_list_calendars_returns_the_items_from_a_zero_kwarg_call():
    """calendarList().list() takes no kwargs of its own - the exact shape the old `_Chain`
    heuristic (kwargs-or-two-dots) got wrong, silently handing back a `_Chain` instead of a
    request. This is the regression test for that: the call must reach `.execute()` and
    `list_calendars` must return the unwrapped `items` list."""
    seeded = {"items": [{"id": "primary"}, {"id": "team@example.com"}]}
    rec = []
    cal = _Chain(rec, results={"calendarList.list": seeded})
    result = ApiBackend(_Chain([]), cal).list_calendars()
    assert result == seeded["items"]
    name, kwargs, _ = rec[0]
    assert name == "calendarList.list"
    assert kwargs == {}


# --- outbound return shape parity: Fix round 2 (CINO 2026-09-25) -------------------------

def test_send_message_returns_the_same_keys_on_both_backends():
    """FakeBackend.send_message used to also return `raw`, a key real `messages.send` never
    sends back - a caller reading `result["raw"]` would pass every offline test and fail in
    production. Pinned here so the two backends' return shapes cannot silently drift apart
    again, rather than merely happening to agree."""
    fake_result = FakeBackend().send_message(raw="aGVsbG8=")

    seeded = {"id": "m1", "threadId": "m1", "labelIds": ["SENT"]}
    api_result = ApiBackend(_Chain([], results={"users.messages.send": seeded}),
                            _Chain([])).send_message(raw="aGVsbG8=")

    assert set(fake_result) == set(api_result) == {"id", "threadId", "labelIds"}
    assert "raw" not in fake_result


def test_list_drafts_passes_through_page_token_and_maxresults():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).list_drafts(limit=10, page_token="tok3")
    _, kwargs, _ = rec[0]
    assert kwargs["maxResults"] == 10
    assert kwargs["pageToken"] == "tok3"
