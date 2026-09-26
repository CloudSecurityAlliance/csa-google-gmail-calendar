import pytest

from csa_google_gmail_calendar.backend import ApiBackend, Backend, FakeBackend
from csa_google_gmail_calendar.exceptions import ConflictError, NotFoundError, UnsupportedOperation


def test_fake_implements_every_protocol_method():
    """Any method on the Protocol the Fake lacks is a hole the offline suite cannot see."""
    wanted = {m for m in dir(Backend) if not m.startswith("_")}
    missing = [m for m in wanted if not callable(getattr(FakeBackend, m, None))]
    assert not missing, f"FakeBackend is missing {missing}"


def test_api_backend_implements_every_protocol_method():
    """A method the real backend lacks is one that works in every offline test and fails only
    in production - this is the check that would have caught that before a user did."""
    wanted = {m for m in dir(Backend) if not m.startswith("_")}
    missing = [m for m in wanted if not callable(getattr(ApiBackend, m, None))]
    assert not missing, f"ApiBackend is missing {missing}"


def test_get_message_returns_the_seeded_message():
    fake = FakeBackend(messages={"m1": {"id": "m1", "snippet": "hello",
                                        "payload": {"headers": [{"name": "Subject", "value": "Hi"}]}}})
    assert fake.get_message(message_id="m1")["snippet"] == "hello"


def test_unknown_id_raises_notfound_not_keyerror():
    """A KeyError becomes an UnexpectedToolError whose message the SDK suppresses."""
    with pytest.raises(NotFoundError, match="nope"):
        FakeBackend().get_message(message_id="nope")


def test_archive_removes_inbox_and_nothing_else():
    fake = FakeBackend(messages={"m1": {"id": "m1", "labelIds": ["INBOX", "UNREAD", "IMPORTANT"]}})
    fake.archive_message(message_id="m1")
    assert fake.messages["m1"]["labelIds"] == ["UNREAD", "IMPORTANT"]


def test_archive_is_idempotent():
    fake = FakeBackend(messages={"m1": {"id": "m1", "labelIds": ["UNREAD"]}})
    fake.archive_message(message_id="m1")
    assert fake.messages["m1"]["labelIds"] == ["UNREAD"]


def test_mark_spam_removes_inbox_and_adds_spam():
    """Fix round 1: real Gmail moves a spammed message out of the inbox, not just onto SPAM."""
    fake = FakeBackend(messages={"m1": {"id": "m1", "labelIds": ["INBOX"]}})
    fake.mark_spam(message_id="m1")
    assert fake.messages["m1"]["labelIds"] == ["SPAM"]


def test_unmark_spam_removes_spam_and_restores_inbox():
    fake = FakeBackend(messages={"m1": {"id": "m1", "labelIds": ["SPAM"]}})
    fake.unmark_spam(message_id="m1")
    assert fake.messages["m1"]["labelIds"] == ["INBOX"]


def _event(event_id, start, end, calendar_id="primary"):
    return {"id": event_id, "calendarId": calendar_id, "summary": "e", "start": start, "end": end}


# Fix round 2 (CINO 2026-09-25): the overlap rule is one piece of logic shared by list_events
# and query_freebusy, and round 1 only property-tested list_events - query_freebusy's own
# straddle/before/after behaviour was correct by construction, not by assertion. Parametrised
# over BOTH the property (a case below) and the method under test, with explicit ids on each
# axis, so a failure names both "which property" and "which method" rather than hiding either
# behind a shared helper.
_OVERLAP_CASES = [
    pytest.param("2026-10-01T08:00:00Z", "2026-10-01T09:30:00Z",
                 "2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z", True,
                 id="straddles_time_min"),
    pytest.param("2026-10-01T09:30:00Z", "2026-10-01T11:00:00Z",
                 "2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z", True,
                 id="straddles_time_max"),
    pytest.param("2026-10-01T06:00:00Z", "2026-10-01T07:00:00Z",
                 "2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z", False,
                 id="entirely_before_window"),
    pytest.param("2026-10-01T12:00:00Z", "2026-10-01T13:00:00Z",
                 "2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z", False,
                 id="entirely_after_window"),
    pytest.param("2026-10-01T09:00:00-07:00", "2026-10-01T09:30:00-07:00",
                 "2026-10-01T15:00:00Z", "2026-10-01T17:00:00Z", True,
                 id="non_utc_offset_inside_utc_window"),
]


def _list_events_matched(start, end, time_min, time_max):
    events = {"e1": _event("e1", {"dateTime": start}, {"dateTime": end})}
    fake = FakeBackend(events=events)
    result = fake.list_events(calendar_id="primary", time_min=time_min, time_max=time_max)
    return len(result["items"])


def _query_freebusy_matched(start, end, time_min, time_max):
    fake = FakeBackend(freebusy={"primary": [{"start": start, "end": end}]})
    result = fake.query_freebusy(time_min=time_min, time_max=time_max, calendar_ids=["primary"])
    return len(result["calendars"]["primary"]["busy"])


_OVERLAP_METHODS = [
    pytest.param(_list_events_matched, id="list_events"),
    pytest.param(_query_freebusy_matched, id="query_freebusy"),
]


@pytest.mark.parametrize("method", _OVERLAP_METHODS)
@pytest.mark.parametrize("start,end,time_min,time_max,expected", _OVERLAP_CASES)
def test_overlap_property(start, end, time_min, time_max, expected, method):
    assert (method(start, end, time_min, time_max) == 1) == expected


def test_all_day_event_is_returned_by_a_window_overlapping_its_date():
    events = {"e1": _event("e1", {"date": "2026-10-01"}, {"date": "2026-10-02"})}
    fake = FakeBackend(events=events)
    result = fake.list_events(calendar_id="primary", time_min="2026-10-01T15:00:00Z",
                              time_max="2026-10-01T17:00:00Z")
    assert [e["id"] for e in result["items"]] == ["e1"]


def test_all_day_event_is_excluded_from_a_window_entirely_on_the_next_day():
    """Confirms the exclusive-end-day convention chosen in `_event_boundary`: an all-day event
    on 2026-10-01 (end date 2026-10-02, per Google's own all-day convention) does not overlap a
    window entirely on 2026-10-02 - the same boundary treatment a timed event gets."""
    events = {"e1": _event("e1", {"date": "2026-10-01"}, {"date": "2026-10-02"})}
    fake = FakeBackend(events=events)
    result = fake.list_events(calendar_id="primary", time_min="2026-10-02T01:00:00Z",
                              time_max="2026-10-02T02:00:00Z")
    assert result["items"] == []


def test_malformed_event_datetime_raises_and_names_the_value_and_event():
    """Fix round 2: a malformed dateTime used to take part harmlessly in a string comparison;
    now it must fail loudly, and the message must name both the bad value and its event -
    datetime.fromisoformat's own message names neither."""
    events = {"e1": _event("e1", {"dateTime": "2026-10-01T09:00:00Z"},
                           {"dateTime": "not-a-timestamp"})}
    fake = FakeBackend(events=events)
    with pytest.raises(ValueError) as excinfo:
        fake.list_events(calendar_id="primary", time_min="2026-10-01T00:00:00Z")
    message = str(excinfo.value)
    assert "not-a-timestamp" in message
    assert "e1" in message


def test_freebusy_interval_missing_start_or_end_raises_and_names_the_interval():
    """Fix round 2: query_freebusy used to direct-index iv["start"]/iv["end"], a bare KeyError
    on a malformed interval - the same defect class the project bans elsewhere."""
    fake = FakeBackend(freebusy={"primary": [{"end": "2026-10-01T10:00:00Z"}]})  # no "start"
    with pytest.raises(ValueError) as excinfo:
        fake.query_freebusy(time_min="2026-10-01T00:00:00Z", time_max="2026-10-01T23:00:00Z",
                            calendar_ids=["primary"])
    message = str(excinfo.value)
    assert "start" in message
    assert "primary" in message


# --- respond_to_event: Fix round 1 (CINO 2026-09-25) -------------------------------------
#
# Matches on attendee["self"], never on email; refuses rather than inventing an attendee.
# See the method's own docstring in backend.py for the full reasoning.

def _event_with_attendees(event_id, attendees, etag='"1"'):
    event = _event(event_id, {"dateTime": "2026-10-01T09:00:00Z"},
                   {"dateTime": "2026-10-01T10:00:00Z"})
    event["attendees"] = attendees
    event["etag"] = etag
    return event


def test_respond_to_event_accepting_sets_only_the_callers_own_status():
    event = _event_with_attendees("e1", [
        {"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
        {"email": "other@example.com", "responseStatus": "needsAction"},
    ])
    fake = FakeBackend(events={"e1": event})
    result = fake.respond_to_event(calendar_id="primary", event_id="e1", response="accepted",
                                   comment="see you there")
    attendees = {a["email"]: a for a in result["attendees"]}
    assert attendees["me@example.com"]["responseStatus"] == "accepted"
    assert attendees["me@example.com"]["comment"] == "see you there"


def test_respond_to_event_does_not_touch_another_attendees_status():
    event = _event_with_attendees("e1", [
        {"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
        {"email": "other@example.com", "responseStatus": "needsAction"},
    ])
    fake = FakeBackend(events={"e1": event})
    result = fake.respond_to_event(calendar_id="primary", event_id="e1", response="declined")
    attendees = {a["email"]: a for a in result["attendees"]}
    assert attendees["other@example.com"]["responseStatus"] == "needsAction"
    assert "comment" not in attendees["other@example.com"]


def test_respond_to_event_with_no_self_attendee_refuses():
    """Genuinely not invited. Responding on someone else's behalf is not this server's call -
    it must refuse, not append a new attendee entry (the defect this fix corrects)."""
    event = _event_with_attendees("e1", [
        {"email": "someone-else@example.com", "responseStatus": "needsAction"},
        {"email": "another@example.com", "responseStatus": "needsAction"},
    ])
    fake = FakeBackend(events={"e1": event})
    with pytest.raises(UnsupportedOperation, match="not among the 2 attendee"):
        fake.respond_to_event(calendar_id="primary", event_id="e1", response="accepted")
    # And nobody was added.
    assert len(fake.events["e1"]["attendees"]) == 2


def test_respond_to_event_with_no_attendees_at_all_refuses_with_a_different_message():
    """No attendee list is not an invitation - it's an entry on a calendar. The message must
    say something distinct from "you're not among the attendees" (there are none to be among),
    and point at delete rather than decline."""
    event = _event("e1", {"dateTime": "2026-10-01T09:00:00Z"},
                    {"dateTime": "2026-10-01T10:00:00Z"})
    fake = FakeBackend(events={"e1": event})
    with pytest.raises(UnsupportedOperation, match="no attendees at all"):
        fake.respond_to_event(calendar_id="primary", event_id="e1", response="accepted")


def test_respond_to_event_carries_the_read_etag_as_the_write_etag():
    event = _event_with_attendees("e1", [
        {"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
    ], etag='"7"')
    fake = FakeBackend(events={"e1": event})
    calls = []
    original_update_event = fake.update_event

    def spy(**kwargs):
        calls.append(kwargs)
        return original_update_event(**kwargs)
    fake.update_event = spy

    fake.respond_to_event(calendar_id="primary", event_id="e1", response="accepted")

    assert calls[0]["etag"] == '"7"'
    assert calls[0]["send_updates"] == "none"
    assert list(calls[0]["body"].keys()) == ["attendees"]


def test_respond_to_event_refuses_when_the_event_changed_between_read_and_write():
    """A change landing in the read/write gap must be a refusal, not a silent overwrite."""
    event = _event_with_attendees("e1", [
        {"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
    ], etag='"1"')
    fake = FakeBackend(events={"e1": event})
    original_get_event = fake.get_event

    def get_event_then_mutate(**kwargs):
        result = original_get_event(**kwargs)
        fake.events["e1"]["etag"] = '"2"'  # another writer lands in the gap
        return result
    fake.get_event = get_event_then_mutate

    with pytest.raises(ConflictError):
        fake.respond_to_event(calendar_id="primary", event_id="e1", response="accepted")


# --- list_events / list_threads pagination: Fix round 1 (CINO 2026-09-25) ----------------

def test_list_events_reports_a_next_page_token_when_truncated():
    events = {f"e{i}": _event(f"e{i}", {"dateTime": "2026-10-01T09:00:00Z"},
                              {"dateTime": "2026-10-01T10:00:00Z"}) for i in range(3)}
    fake = FakeBackend(events=events)
    first = fake.list_events(calendar_id="primary", limit=2)
    assert len(first["items"]) == 2
    assert "nextPageToken" in first
    second = fake.list_events(calendar_id="primary", limit=2, page_token=first["nextPageToken"])
    assert len(second["items"]) == 1
    assert "nextPageToken" not in second
    seen = {e["id"] for e in first["items"]} | {e["id"] for e in second["items"]}
    assert seen == set(events)


def test_list_threads_reports_a_next_page_token_when_truncated():
    fake = FakeBackend(threads={"t1": {}, "t2": {}, "t3": {}})
    first = fake.list_threads(limit=2)
    assert len(first["threads"]) == 2
    assert "nextPageToken" in first
    second = fake.list_threads(limit=2, page_token=first["nextPageToken"])
    assert len(second["threads"]) == 1
    assert "nextPageToken" not in second


def test_list_drafts_reports_a_next_page_token_when_truncated():
    """Fix round 2 (CINO 2026-09-25): list_drafts changed from a bare list to the same dict
    shape as list_threads, so a truncated page is distinguishable and continuable."""
    fake = FakeBackend(drafts={"d1": {"id": "d1"}, "d2": {"id": "d2"}, "d3": {"id": "d3"}})
    first = fake.list_drafts(limit=2)
    assert len(first["drafts"]) == 2
    assert first["resultSizeEstimate"] == 3
    assert "nextPageToken" in first
    second = fake.list_drafts(limit=2, page_token=first["nextPageToken"])
    assert len(second["drafts"]) == 1
    assert "nextPageToken" not in second


# --- list_history (task 13 - deferred from task 3, see that task's own report) --------------

def test_list_history_reports_nothing_changed_when_the_store_is_empty():
    """The one branch the previous version of this method could ever take - kept as its own
    test now that a second, non-trivial branch exists, so a future edit cannot collapse them
    back into the same untested state."""
    fake = FakeBackend()
    result = fake.list_history(start_history_id="10")
    assert result == {"history": [], "historyId": "10"}


def test_list_history_reports_a_record_newer_than_start_history_id():
    """The branch task 3 could not test: a seeded history store makes 'something changed' a
    real, assertable outcome instead of the only untestable claim in the tool."""
    fake = FakeBackend(history=[{"id": "5", "messagesAdded": [{"message": {"id": "m1"}}]},
                                {"id": "12", "messagesAdded": [{"message": {"id": "m2"}}]}])
    result = fake.list_history(start_history_id="10")
    assert [h["id"] for h in result["history"]] == ["12"]
    assert result["historyId"] == "12"


def test_list_history_excludes_records_at_or_before_start_history_id():
    """`startHistoryId` is exclusive, matching Gmail's own `history.list` semantics - a record
    whose id equals the ask is not "since" it."""
    fake = FakeBackend(history=[{"id": "10"}])
    result = fake.list_history(start_history_id="10")
    assert result["history"] == []
    assert result["historyId"] == "10"


def test_list_history_orders_records_ascending_by_id():
    fake = FakeBackend(history=[{"id": "30"}, {"id": "20"}, {"id": "40"}])
    result = fake.list_history(start_history_id="10")
    assert [h["id"] for h in result["history"]] == ["20", "30", "40"]


def test_list_history_treats_a_non_numeric_start_history_id_as_zero():
    """Malformed, not unknown - `get_message`/`get_thread` raise `NotFoundError` for an id they
    have never heard of; a `start_history_id` this fake cannot parse is not a lookup failure the
    same way, so it is read as the widest possible ask rather than raised."""
    fake = FakeBackend(history=[{"id": "1"}])
    result = fake.list_history(start_history_id="not-a-number")
    assert [h["id"] for h in result["history"]] == ["1"]
