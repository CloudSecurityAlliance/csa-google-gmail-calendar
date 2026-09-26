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
