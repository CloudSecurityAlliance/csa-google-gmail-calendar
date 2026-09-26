"""Task 13's three additions to the Gmail reading tier (`mail_read.py`): `list_history`,
`get_profile`, `whoami`. All three call through a `FakeBackend` seeded the same way
`test_backend_contract.py` seeds `list_history`'s own two branches."""
from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.mcp import create_server


def _call(server, tool_name, **kw):
    return server._tool_manager.get_tool(tool_name).fn(**kw)


def _server(fake):
    return create_server(backend=fake, policy=policy.Policy(frozenset({policy.MAIL_READ})))


def test_get_profile_returns_the_seeded_profile():
    fake = FakeBackend(profile={"emailAddress": "me@example.com", "messagesTotal": 3,
                                "threadsTotal": 2, "historyId": "42"})
    out = _call(_server(fake), "get_profile")
    assert out == {"email_address": "me@example.com", "messages_total": 3,
                   "threads_total": 2, "history_id": "42"}


def test_whoami_returns_only_the_address():
    fake = FakeBackend(profile={"emailAddress": "me@example.com", "messagesTotal": 3,
                                "threadsTotal": 2, "historyId": "42"})
    out = _call(_server(fake), "whoami")
    assert out == {"email_address": "me@example.com"}


def test_get_profile_and_whoami_are_absent_without_mail_read():
    fake = FakeBackend()
    server = create_server(backend=fake, policy=policy.Policy(frozenset()))
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "get_profile" not in names
    assert "whoami" not in names
    assert "list_history" not in names


def test_list_history_tool_reports_nothing_changed_against_an_empty_store():
    fake = FakeBackend()
    out = _call(_server(fake), "list_history", start_history_id="10")
    assert out == {"history": [], "history_id": "10"}


def test_list_history_tool_reports_a_record_newer_than_start_history_id():
    fake = FakeBackend(history=[{"id": "12", "messagesAdded": [{"message": {"id": "m2"}}]}])
    out = _call(_server(fake), "list_history", start_history_id="10")
    assert [h["id"] for h in out["history"]] == ["12"]
    assert out["history_id"] == "12"
