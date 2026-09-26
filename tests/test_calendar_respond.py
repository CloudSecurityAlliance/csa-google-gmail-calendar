"""`Calendar` — the thin `respond()` wrapper over `Backend.respond_to_event`, plus this
module's own logic: `create`, `reschedule`, `find_free`.

The `respond()` tests below are the brief's original nine, rewritten per the coordinator's
ruling: the read-modify-write, the two distinct refusals, the etag carry, and the
attendees-only patch body are `FakeBackend.respond_to_event`'s behaviour now (see
`tests/test_backend_contract.py` for the tests that pin it directly at that seam). What is
tested HERE is that `Calendar.respond()` delegates to it unchanged, and that `Calendar.respond()`
itself validates `response` before that delegation happens — the one piece of behaviour that
actually lives in this module.
"""
import pytest

from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.calendar import RESPONSES, Calendar
from csa_google_gmail_calendar.exceptions import NotFoundError, UnsupportedOperation


def _ev(attendees, etag='"v1"'):
    return {"id": "e1", "etag": etag, "summary": "Standup",
            "start": {"dateTime": "2026-10-01T09:00:00Z"},
            "end": {"dateTime": "2026-10-01T09:30:00Z"}, "attendees": attendees}


# --- respond(): delegation to Backend.respond_to_event -----------------------------------

def test_accepting_sets_only_my_response_status():
    ev = _ev([{"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
              {"email": "other@example.org", "responseStatus": "accepted"}])
    fake = FakeBackend(events={"e1": ev})
    Calendar(fake).respond("primary", "e1", "accepted")
    out = fake.events["e1"]["attendees"]
    assert out[0]["responseStatus"] == "accepted"
    assert out[1]["responseStatus"] == "accepted", "other attendees must be untouched"


def test_declining_does_not_clobber_another_attendees_response():
    ev = _ev([{"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
              {"email": "other@example.org", "responseStatus": "tentative"}])
    fake = FakeBackend(events={"e1": ev})
    Calendar(fake).respond("primary", "e1", "declined")
    assert fake.events["e1"]["attendees"][1]["responseStatus"] == "tentative"


def test_no_self_attendee_refuses_and_says_which_case():
    """The refusal is `Backend.respond_to_event`'s, but `Calendar.respond()` must let it
    through unchanged rather than swallowing or rewrapping it."""
    fake = FakeBackend(events={"e1": _ev([{"email": "a@example.org",
                                           "responseStatus": "needsAction"}])})
    with pytest.raises(UnsupportedOperation, match="not among the .* attendee"):
        Calendar(fake).respond("primary", "e1", "accepted")


def test_an_event_with_no_attendees_says_it_is_not_an_invitation():
    fake = FakeBackend(events={"e1": {"id": "e1", "etag": '"v1"', "summary": "Focus time"}})
    with pytest.raises(UnsupportedOperation, match="no attendees"):
        Calendar(fake).respond("primary", "e1", "accepted")


def test_an_unknown_response_value_is_refused_with_the_valid_set():
    """This one IS Calendar's own logic — validated before the Backend is ever called."""
    fake = FakeBackend(events={"e1": _ev([{"email": "me@example.com", "self": True,
                                           "responseStatus": "needsAction"}])})
    with pytest.raises(ValueError, match="accepted"):
        Calendar(fake).respond("primary", "e1", "maybe")


def test_an_unknown_response_never_reaches_the_backend():
    """A ValueError from validation must not cost a round trip to the Backend at all."""
    class Tripwire(FakeBackend):
        def respond_to_event(self, **kw):
            raise AssertionError("Calendar.respond must not delegate an invalid response")
    with pytest.raises(ValueError):
        Calendar(Tripwire()).respond("primary", "e1", "maybe")


def test_the_etag_from_the_read_is_sent_on_the_write():
    seen = {}
    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw)
            return super().update_event(**kw)
    fake = Spy(events={"e1": _ev([{"email": "me@example.com", "self": True,
                                   "responseStatus": "needsAction"}], etag='"v7"')})
    Calendar(fake).respond("primary", "e1", "accepted")
    assert seen["etag"] == '"v7"'


def test_a_comment_becomes_the_attendee_comment():
    fake = FakeBackend(events={"e1": _ev([{"email": "me@example.com", "self": True,
                                           "responseStatus": "needsAction"}])})
    Calendar(fake).respond("primary", "e1", "tentative", comment="might be travelling")
    assert fake.events["e1"]["attendees"][0]["comment"] == "might be travelling"


def test_a_missing_event_raises_notfound():
    with pytest.raises(NotFoundError):
        Calendar(FakeBackend()).respond("primary", "nope", "accepted")


def test_only_the_attendees_field_is_sent_back():
    seen = {}
    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw)
            return super().update_event(**kw)
    fake = Spy(events={"e1": _ev([{"email": "me@example.com", "self": True,
                                   "responseStatus": "needsAction"}])})
    Calendar(fake).respond("primary", "e1", "accepted")
    assert set(seen["body"]) == {"attendees"}


def test_responses_constant_matches_what_respond_accepts():
    """Task 12's tool enum imports RESPONSES; pin that it is the exact set respond() allows."""
    assert RESPONSES == ("accepted", "declined", "tentative")


# --- create(): start/end validation before create_event -----------------------------------

def test_create_delegates_to_create_event():
    fake = FakeBackend()
    body = {"summary": "Sync", "start": {"dateTime": "2026-10-01T09:00:00Z"},
            "end": {"dateTime": "2026-10-01T09:30:00Z"}}
    out = Calendar(fake).create(calendar_id="primary", body=body)
    assert out["summary"] == "Sync"
    assert out["id"] in fake.events


def test_create_missing_start_raises_valueerror():
    body = {"summary": "Sync", "end": {"dateTime": "2026-10-01T09:30:00Z"}}
    with pytest.raises(ValueError, match="start.*end"):
        Calendar(FakeBackend()).create(calendar_id="primary", body=body)


def test_create_missing_end_raises_valueerror():
    body = {"summary": "Sync", "start": {"dateTime": "2026-10-01T09:00:00Z"}}
    with pytest.raises(ValueError, match="start.*end"):
        Calendar(FakeBackend()).create(calendar_id="primary", body=body)


def test_create_end_before_start_raises_valueerror():
    body = {"start": {"dateTime": "2026-10-01T09:30:00Z"},
            "end": {"dateTime": "2026-10-01T09:00:00Z"}}
    with pytest.raises(ValueError, match="before start"):
        Calendar(FakeBackend()).create(calendar_id="primary", body=body)


def test_create_zero_length_event_is_allowed():
    """A same-instant start/end is a legitimate marker, not an inverted event."""
    body = {"start": {"dateTime": "2026-10-01T09:00:00Z"},
            "end": {"dateTime": "2026-10-01T09:00:00Z"}}
    out = Calendar(FakeBackend()).create(calendar_id="primary", body=body)
    assert out["start"] == out["end"]


def test_create_all_day_event_uses_date_not_datetime():
    body = {"summary": "Conference", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-03"}}
    out = Calendar(FakeBackend()).create(calendar_id="primary", body=body)
    assert out["start"] == {"date": "2026-10-01"}


def test_create_all_day_event_inverted_still_raises():
    body = {"start": {"date": "2026-10-03"}, "end": {"date": "2026-10-01"}}
    with pytest.raises(ValueError, match="before start"):
        Calendar(FakeBackend()).create(calendar_id="primary", body=body)


def test_create_compares_timezone_aware_not_as_strings():
    """The Task 3 bug: '09:00-07:00' < '15:00Z' as strings, but 09:00-07:00 is 16:00 UTC —
    LATER than 15:00Z. A naive string comparison would wrongly refuse this as inverted."""
    body = {"start": {"dateTime": "2026-10-01T15:00:00Z"},
            "end": {"dateTime": "2026-10-01T09:00:00-07:00"}}
    out = Calendar(FakeBackend()).create(calendar_id="primary", body=body)
    assert out["end"]["dateTime"] == "2026-10-01T09:00:00-07:00"


def test_create_timezone_aware_still_catches_a_real_inversion():
    """Same test, opposite outcome: 22:00 UTC (start) is after 20:00 UTC (end) once the
    offsets are actually applied, so this one must raise."""
    body = {"start": {"dateTime": "2026-10-01T15:00:00-07:00"},
            "end": {"dateTime": "2026-10-01T20:00:00Z"}}
    with pytest.raises(ValueError, match="before start"):
        Calendar(FakeBackend()).create(calendar_id="primary", body=body)


# --- reschedule(): patch of start/end only, with etag --------------------------------------

def test_reschedule_patches_only_start_and_end():
    seen = {}
    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw)
            return super().update_event(**kw)
    ev = {"id": "e1", "etag": '"v1"', "summary": "Standup",
          "start": {"dateTime": "2026-10-01T09:00:00Z"}, "end": {"dateTime": "2026-10-01T09:30:00Z"}}
    fake = Spy(events={"e1": ev})
    Calendar(fake).reschedule(calendar_id="primary", event_id="e1",
                              start={"dateTime": "2026-10-02T09:00:00Z"},
                              end={"dateTime": "2026-10-02T09:30:00Z"})
    assert set(seen["body"]) == {"start", "end"}
    assert fake.events["e1"]["summary"] == "Standup", "an unrelated field must survive untouched"


def test_reschedule_carries_the_etag_from_a_fresh_read():
    seen = {}
    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw)
            return super().update_event(**kw)
    fake = Spy(events={"e1": {"id": "e1", "etag": '"v9"',
                              "start": {"dateTime": "2026-10-01T09:00:00Z"},
                              "end": {"dateTime": "2026-10-01T09:30:00Z"}}})
    Calendar(fake).reschedule(calendar_id="primary", event_id="e1",
                              start={"dateTime": "2026-10-02T09:00:00Z"},
                              end={"dateTime": "2026-10-02T09:30:00Z"})
    assert seen["etag"] == '"v9"'


def test_reschedule_defaults_to_notifying_attendees():
    """Unlike respond() (send_updates='none'): a reschedule is a real edit to the meeting, so
    the default must tell attendees, or someone shows up at the old time."""
    seen = {}
    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw)
            return super().update_event(**kw)
    fake = Spy(events={"e1": {"id": "e1", "etag": '"v1"',
                              "start": {"dateTime": "2026-10-01T09:00:00Z"},
                              "end": {"dateTime": "2026-10-01T09:30:00Z"},
                              "attendees": [{"email": "other@example.org", "self": False}]}})
    Calendar(fake).reschedule(calendar_id="primary", event_id="e1",
                              start={"dateTime": "2026-10-02T09:00:00Z"},
                              end={"dateTime": "2026-10-02T09:30:00Z"})
    assert seen["send_updates"] == "all"


def test_reschedule_does_not_refuse_for_having_attendees():
    """Rescheduling a meeting other people are on is ordinary; it is not the
    on-someone-elses-behalf case respond_to_event exists to block."""
    fake = FakeBackend(events={"e1": {"id": "e1", "etag": '"v1"',
                                      "start": {"dateTime": "2026-10-01T09:00:00Z"},
                                      "end": {"dateTime": "2026-10-01T09:30:00Z"},
                                      "attendees": [{"email": "other@example.org"}]}})
    Calendar(fake).reschedule(calendar_id="primary", event_id="e1",
                              start={"dateTime": "2026-10-02T09:00:00Z"},
                              end={"dateTime": "2026-10-02T09:30:00Z"})


def test_reschedule_can_be_quiet_when_asked():
    seen = {}
    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw)
            return super().update_event(**kw)
    fake = Spy(events={"e1": {"id": "e1", "etag": '"v1"',
                              "start": {"dateTime": "2026-10-01T09:00:00Z"},
                              "end": {"dateTime": "2026-10-01T09:30:00Z"}}})
    Calendar(fake).reschedule(calendar_id="primary", event_id="e1",
                              start={"dateTime": "2026-10-02T09:00:00Z"},
                              end={"dateTime": "2026-10-02T09:30:00Z"}, send_updates="none")
    assert seen["send_updates"] == "none"


def test_reschedule_end_before_start_raises_valueerror():
    fake = FakeBackend(events={"e1": {"id": "e1", "etag": '"v1"',
                                      "start": {"dateTime": "2026-10-01T09:00:00Z"},
                                      "end": {"dateTime": "2026-10-01T09:30:00Z"}}})
    with pytest.raises(ValueError, match="before start"):
        Calendar(fake).reschedule(calendar_id="primary", event_id="e1",
                                  start={"dateTime": "2026-10-02T09:30:00Z"},
                                  end={"dateTime": "2026-10-02T09:00:00Z"})


def test_reschedule_missing_event_raises_notfound():
    with pytest.raises(NotFoundError):
        Calendar(FakeBackend()).reschedule(calendar_id="primary", event_id="nope",
                                           start={"dateTime": "2026-10-02T09:00:00Z"},
                                           end={"dateTime": "2026-10-02T09:30:00Z"})


# --- find_free(): busy blocks inverted into gaps --------------------------------------------

def test_find_free_with_no_busy_blocks_is_the_whole_window():
    fake = FakeBackend(freebusy={"primary": []})
    gaps = Calendar(fake).find_free(time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T17:00:00Z",
                                    calendar_ids=["primary"])
    assert gaps == [{"start": "2026-10-01T09:00:00+00:00", "end": "2026-10-01T17:00:00+00:00"}]


def test_find_free_unknown_calendar_id_reads_as_free():
    """FakeBackend deliberately reports an id it has never heard of as `{"busy": []}` — same
    as a calendar known to be empty. find_free inherits that ambiguity at the Backend seam,
    but its OWN empty-vs-non-empty result contract stays unambiguous either way."""
    fake = FakeBackend()  # no freebusy store seeded at all
    gaps = Calendar(fake).find_free(time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T17:00:00Z",
                                    calendar_ids=["never-heard-of-it"])
    assert len(gaps) == 1


def test_find_free_busy_covering_the_entire_window_is_an_empty_list():
    fake = FakeBackend(freebusy={"primary": [{"start": "2026-10-01T09:00:00Z",
                                              "end": "2026-10-01T17:00:00Z"}]})
    gaps = Calendar(fake).find_free(time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T17:00:00Z",
                                    calendar_ids=["primary"])
    assert gaps == []


def test_find_free_reports_the_gap_between_two_busy_blocks():
    fake = FakeBackend(freebusy={"primary": [
        {"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"},
        {"start": "2026-10-01T11:00:00Z", "end": "2026-10-01T12:00:00Z"}]})
    gaps = Calendar(fake).find_free(time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T12:00:00Z",
                                    calendar_ids=["primary"])
    assert gaps == [{"start": "2026-10-01T10:00:00+00:00", "end": "2026-10-01T11:00:00+00:00"}]


def test_find_free_adjacent_busy_blocks_produce_no_zero_length_gap():
    fake = FakeBackend(freebusy={"primary": [
        {"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"},
        {"start": "2026-10-01T10:00:00Z", "end": "2026-10-01T11:00:00Z"}]})
    gaps = Calendar(fake).find_free(time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T11:00:00Z",
                                    calendar_ids=["primary"])
    assert gaps == []


def test_find_free_clips_a_busy_block_extending_beyond_the_window():
    fake = FakeBackend(freebusy={"primary": [
        {"start": "2026-10-01T07:00:00Z", "end": "2026-10-01T10:00:00Z"},
        {"start": "2026-10-01T16:00:00Z", "end": "2026-10-01T19:00:00Z"}]})
    gaps = Calendar(fake).find_free(time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T17:00:00Z",
                                    calendar_ids=["primary"])
    assert gaps == [{"start": "2026-10-01T10:00:00+00:00", "end": "2026-10-01T16:00:00+00:00"}]


def test_find_free_merges_busy_blocks_across_calendars():
    """Free on calendar A but busy on calendar B is busy for the meeting — must not be
    reported as a gap just because one of the two calendars queried is clear."""
    fake = FakeBackend(freebusy={
        "a": [{"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"}],
        "b": [{"start": "2026-10-01T09:30:00Z", "end": "2026-10-01T10:30:00Z"}]})
    gaps = Calendar(fake).find_free(time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T11:00:00Z",
                                    calendar_ids=["a", "b"])
    assert gaps == [{"start": "2026-10-01T10:30:00+00:00", "end": "2026-10-01T11:00:00+00:00"}]


def test_find_free_compares_timezone_aware_not_as_strings():
    """A busy block given with a negative offset must clip/merge correctly against a
    Z-suffixed window bound — the same naive-string-comparison bug Task 3 found elsewhere."""
    fake = FakeBackend(freebusy={"primary": [
        {"start": "2026-10-01T02:00:00-07:00", "end": "2026-10-01T03:00:00-07:00"}]})
    # 02:00-07:00 == 09:00Z, 03:00-07:00 == 10:00Z
    gaps = Calendar(fake).find_free(time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T11:00:00Z",
                                    calendar_ids=["primary"])
    assert gaps == [{"start": "2026-10-01T10:00:00+00:00", "end": "2026-10-01T11:00:00+00:00"}]


def test_find_free_invalid_window_raises_valueerror():
    with pytest.raises(ValueError, match="time_max"):
        Calendar(FakeBackend()).find_free(time_min="2026-10-01T17:00:00Z",
                                          time_max="2026-10-01T09:00:00Z", calendar_ids=["primary"])
