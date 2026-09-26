import pytest

from csa_google_gmail_calendar.backend import ApiBackend, Backend, FakeBackend
from csa_google_gmail_calendar.exceptions import NotFoundError


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
