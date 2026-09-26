import pytest

from csa_google_gmail_calendar.backend import Backend, FakeBackend
from csa_google_gmail_calendar.exceptions import NotFoundError


def test_fake_implements_every_protocol_method():
    """Any method on the Protocol the Fake lacks is a hole the offline suite cannot see."""
    wanted = {m for m in dir(Backend) if not m.startswith("_")}
    missing = [m for m in wanted if not callable(getattr(FakeBackend, m, None))]
    assert not missing, f"FakeBackend is missing {missing}"


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


def test_event_straddling_time_min_is_returned():
    """timeMin is an EXCLUSIVE bound on an event's END time: an event already under way when
    the window opens must still come back."""
    events = {"e1": _event("e1", {"dateTime": "2026-10-01T08:00:00Z"},
                           {"dateTime": "2026-10-01T10:00:00Z"})}
    fake = FakeBackend(events=events)
    result = fake.list_events(calendar_id="primary", time_min="2026-10-01T09:00:00Z")
    assert [e["id"] for e in result["items"]] == ["e1"]


def test_event_straddling_time_max_is_returned():
    """timeMax is an EXCLUSIVE bound on an event's START time: an event that outlives the
    window must still come back."""
    events = {"e1": _event("e1", {"dateTime": "2026-10-01T10:00:00Z"},
                           {"dateTime": "2026-10-01T12:00:00Z"})}
    fake = FakeBackend(events=events)
    result = fake.list_events(calendar_id="primary", time_max="2026-10-01T11:00:00Z")
    assert [e["id"] for e in result["items"]] == ["e1"]


def test_event_entirely_before_window_is_not_returned():
    events = {"e1": _event("e1", {"dateTime": "2026-10-01T06:00:00Z"},
                           {"dateTime": "2026-10-01T07:00:00Z"})}
    fake = FakeBackend(events=events)
    result = fake.list_events(calendar_id="primary", time_min="2026-10-01T09:00:00Z",
                              time_max="2026-10-01T10:00:00Z")
    assert result["items"] == []


def test_event_entirely_after_window_is_not_returned():
    events = {"e1": _event("e1", {"dateTime": "2026-10-01T12:00:00Z"},
                           {"dateTime": "2026-10-01T13:00:00Z"})}
    fake = FakeBackend(events=events)
    result = fake.list_events(calendar_id="primary", time_min="2026-10-01T09:00:00Z",
                              time_max="2026-10-01T10:00:00Z")
    assert result["items"] == []


def test_event_with_a_non_utc_offset_inside_a_utc_window_is_returned():
    """The failure mode Fix round 1 demonstrated: 09:00-07:00 (= 16:00Z) naively string-sorts
    below 15:00Z, so a plain string comparison wrongly excludes it from 15:00Z..17:00Z."""
    events = {"e1": _event("e1", {"dateTime": "2026-10-01T09:00:00-07:00"},
                           {"dateTime": "2026-10-01T09:30:00-07:00"})}
    fake = FakeBackend(events=events)
    result = fake.list_events(calendar_id="primary", time_min="2026-10-01T15:00:00Z",
                              time_max="2026-10-01T17:00:00Z")
    assert [e["id"] for e in result["items"]] == ["e1"]


def test_freebusy_interval_with_a_non_utc_offset_is_detected_as_busy():
    """Same fix, same failure mode, in query_freebusy's overlap test."""
    fake = FakeBackend(freebusy={"primary": [{"start": "2026-10-01T09:00:00-07:00",
                                              "end": "2026-10-01T09:30:00-07:00"}]})
    result = fake.query_freebusy(time_min="2026-10-01T15:00:00Z",
                                 time_max="2026-10-01T17:00:00Z",
                                 calendar_ids=["primary"])
    assert len(result["calendars"]["primary"]["busy"]) == 1
