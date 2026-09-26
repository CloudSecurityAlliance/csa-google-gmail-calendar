"""Task 12: the 8 Calendar tools (`_tools/calendar_read.py`/`calendar_write.py`).

**Adapted from the brief's given test bodies** (task-12-brief.md), because the
ALREADY-REVIEWED `calendar.Calendar`/`backend.FakeBackend` this task must not reimplement
behave differently from what the brief assumed:

1. `Calendar.find_free` returns `{"free": [...], "unreadable_calendars": [...]}`, not a bare
   list - the brief's own `find_free_time` test already reads `out["free"]` as a dict key, so
   that one needed no change; a second test below exercises `unreadable_calendars` directly,
   which the brief did not cover at all.
2. The installed `mcp` package's internal `Tool` object (what `_tool_manager.get_tool(...)`
   returns) names its JSON-Schema fields `.parameters` (input) and `.output_schema` (output) -
   NOT `.input_schema`, which exists only on the WIRE type (`mcp.types.Tool`); see
   `_tools/_base.py::_declared_properties`'s own docstring and
   `test_mcp_server_shape.py::test_the_installed_mcp_tool_object_names_the_input_schema_attribute_input_schema`
   for this already being a documented, verified fact about this dependency. The brief's own
   test read `.input_schema` off the internal object, which does not have that attribute;
   fixed here to read `.parameters` instead.
"""
import pytest

from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.mcp import create_server


def _call(server, name, **kw):
    return server._tool_manager.get_tool(name).fn(**kw)


def test_respond_to_event_is_one_tool_with_a_constrained_response_argument():
    """ADR-016: a tool is (operation x constrained arguments). Three tools would be three
    descriptions saying the same thing."""
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    names = {t.name for t in s._tool_manager.list_tools()}
    assert "respond_to_event" in names
    assert not {"accept_event", "decline_event"} & names
    schema = s._tool_manager.get_tool("respond_to_event").parameters
    assert set(schema["properties"]["response"]["enum"]) == {"accepted", "declined", "tentative"}


def test_create_event_refuses_an_end_before_its_start():
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    with pytest.raises(Exception, match="before"):
        _call(s, "create_event", summary="Backwards",
              start="2026-10-01T10:00:00Z", end="2026-10-01T09:00:00Z")


def test_create_event_defaults_to_notifying_attendees():
    """An invitation nobody is told about is not an invitation."""
    seen = {}

    class Spy(FakeBackend):
        def create_event(self, **kw):
            seen.update(kw)
            return super().create_event(**kw)

    s = create_server(backend=Spy(), policy=policy.Policy())
    _call(s, "create_event", summary="Sync", start="2026-10-01T09:00:00Z",
          end="2026-10-01T10:00:00Z", attendees=["a@example.com"])
    assert seen["send_updates"] == "all"


def test_create_event_builds_attendee_dicts_from_plain_email_addresses():
    fake = FakeBackend()
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "create_event", summary="Sync", start="2026-10-01T09:00:00Z",
               end="2026-10-01T10:00:00Z", attendees=["a@example.com", "b@example.org"])
    assert out["attendees"] == [{"email": "a@example.com"}, {"email": "b@example.org"}]


def test_create_event_treats_a_bare_date_as_an_all_day_event():
    fake = FakeBackend()
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "create_event", summary="Offsite", start="2026-10-01", end="2026-10-02")
    assert out["start"] == {"date": "2026-10-01"}
    assert out["end"] == {"date": "2026-10-02"}


def test_update_event_moves_only_the_time_and_notifies_by_default():
    seen = {}

    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw)
            return super().update_event(**kw)

    fake = Spy(events={"e1": {"id": "e1", "calendarId": "primary", "summary": "Standup",
                              "start": {"dateTime": "2026-10-01T09:00:00Z"},
                              "end": {"dateTime": "2026-10-01T09:30:00Z"},
                              "etag": '"1"'}})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "update_event", event_id="e1",
               start="2026-10-02T09:00:00Z", end="2026-10-02T09:30:00Z")
    assert out["summary"] == "Standup"  # untouched
    assert out["start"] == {"dateTime": "2026-10-02T09:00:00Z"}
    assert seen["send_updates"] == "all"
    assert seen["body"] == {"start": {"dateTime": "2026-10-02T09:00:00Z"},
                            "end": {"dateTime": "2026-10-02T09:30:00Z"}}


def test_respond_to_event_refuses_when_the_event_has_no_attendees_at_all():
    fake = FakeBackend(events={"e1": {"id": "e1", "calendarId": "primary", "etag": '"1"'}})
    s = create_server(backend=fake, policy=policy.Policy())
    with pytest.raises(Exception, match="no attendees at all"):
        _call(s, "respond_to_event", event_id="e1", response="accepted")


def test_respond_to_event_refuses_when_the_caller_is_not_among_the_attendees():
    fake = FakeBackend(events={"e1": {"id": "e1", "calendarId": "primary", "etag": '"1"',
                                      "attendees": [{"email": "other@example.com"}]}})
    s = create_server(backend=fake, policy=policy.Policy())
    with pytest.raises(Exception, match="not among"):
        _call(s, "respond_to_event", event_id="e1", response="accepted")


def test_respond_to_event_sends_no_notification():
    seen = {}

    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw)
            return super().update_event(**kw)

    fake = Spy(events={"e1": {"id": "e1", "calendarId": "primary", "etag": '"1"',
                              "attendees": [{"email": "me@example.com", "self": True}]}})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "respond_to_event", event_id="e1", response="tentative")
    assert seen["send_updates"] == "none"


def test_delete_event_is_absent_when_calendar_delete_is_off():
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    assert "delete_event" not in {t.name for t in s._tool_manager.list_tools()}


def test_delete_event_appears_when_the_capability_is_enabled():
    p = policy.Policy(frozenset(policy.ALL_CAPABILITIES))
    s = create_server(backend=FakeBackend(), policy=p)
    assert "delete_event" in {t.name for t in s._tool_manager.list_tools()}


def test_delete_event_is_annotated_destructive():
    p = policy.Policy(frozenset(policy.ALL_CAPABILITIES))
    s = create_server(backend=FakeBackend(), policy=p)
    assert s._tool_manager.get_tool("delete_event").annotations.destructive_hint is True


def test_find_free_time_returns_gaps_not_busy_blocks():
    """A model asked 'when are we free' should not have to invert the answer itself."""
    fake = FakeBackend(freebusy={"primary": [
        {"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"}]})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "find_free_time", time_min="2026-10-01T08:00:00Z",
                time_max="2026-10-01T12:00:00Z", calendar_ids=["primary"])
    assert any(g["start"] == "2026-10-01T10:00:00Z" for g in out["free"])
    assert any(g["end"] == "2026-10-01T09:00:00Z" for g in out["free"])
    assert out["unreadable_calendars"] == []


def test_find_free_time_names_an_unreadable_calendar_rather_than_treating_it_as_free():
    """Fix round 1 (calendar.py): an inaccessible calendar must not silently read as free."""
    class WithUnreadableCalendar(FakeBackend):
        def query_freebusy(self, *, time_min, time_max, calendar_ids):
            result = super().query_freebusy(time_min=time_min, time_max=time_max,
                                            calendar_ids=calendar_ids)
            result["calendars"]["broken"] = {"errors": [{"reason": "notFound"}]}
            return result

    fake = WithUnreadableCalendar(freebusy={"primary": [
        {"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"}]})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "find_free_time", time_min="2026-10-01T08:00:00Z",
                time_max="2026-10-01T12:00:00Z", calendar_ids=["primary", "broken"])
    assert out["unreadable_calendars"] == [{"id": "broken", "reason": "notFound"}]
    # The readable calendar's own busy interval still shrinks `free` normally.
    assert any(g["start"] == "2026-10-01T10:00:00Z" for g in out["free"])


def test_find_free_time_output_schema_requires_unreadable_calendars():
    """Non-negotiable: a caller (or a model rendering this into context) must not be able to
    drop `unreadable_calendars` - it is a required key of the output schema, not optional."""
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    schema = s._tool_manager.get_tool("find_free_time").output_schema
    assert "unreadable_calendars" in schema["required"]


def test_list_events_is_an_overlap_filter_not_a_starts_in_window_filter():
    """An event already under way when the window opens must still be returned."""
    fake = FakeBackend(events={"e1": {
        "id": "e1", "calendarId": "primary", "summary": "In progress",
        "start": {"dateTime": "2026-10-01T08:00:00Z"},
        "end": {"dateTime": "2026-10-01T09:30:00Z"}}})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "list_events", calendar_id="primary",
               time_min="2026-10-01T09:00:00Z", time_max="2026-10-01T10:00:00Z")
    assert [e["id"] for e in out["events"]] == ["e1"]


def test_list_calendars_returns_every_calendar_the_account_can_see():
    fake = FakeBackend(calendars={"primary": {"id": "primary", "summary": "me@example.com"},
                                  "team": {"id": "team", "summary": "Team calendar"}})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "list_calendars")
    assert {c["id"] for c in out["calendars"]} == {"primary", "team"}


def test_get_event_returns_the_full_event_by_id():
    fake = FakeBackend(events={"e1": {"id": "e1", "calendarId": "primary",
                                      "summary": "1:1"}})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "get_event", event_id="e1")
    assert out["summary"] == "1:1"
