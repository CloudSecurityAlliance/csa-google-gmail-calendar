"""Task 11: the 29 Gmail tools (`_tools/mail_read.py`/`mail_write.py`/`mail_send.py`).

Two adaptations from the brief's given test bodies, made because the ALREADY-REVIEWED
`FakeBackend`/`_mime.build` this task must not reimplement behave more strictly than the
brief's literal fixtures assumed - see task-11-report.md for the full account:

1. **`fake.sent[-1]["raw"]` is base64url, not plain text** (`_mime.build`'s own documented
   contract: its return value IS the API's base64 `raw` payload, the same value `ApiBackend`
   forwards to Google unchanged - see `_mime.py`'s module docstring). A header value like
   `sender@example.org` cannot appear as a literal substring of a base64-encoded blob, so
   every assertion that checks for one decodes the entry first via `_raw_text` below, instead
   of substring-testing the dict (which has no `.count()` method at all) or its still-encoded
   `"raw"` field.
2. **`FakeBackend.reply_message`/`reply_all_message` require the thread to already exist**
   in its `threads` store (`if thread_id not in self.threads: raise NotFoundError(...)`,
   matching real Gmail: a reply targets a thread that already has at least one message in it).
   The reply/reply_all fixtures below seed `threads={"t1": {}}` alongside `messages` for
   exactly this reason - a real received message always arrives already inside a thread.
"""
import base64

import pytest

from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.mcp import create_server


def _call(server, tool_name, **kw):
    return server._tool_manager.get_tool(tool_name).fn(**kw)


def _raw_text(sent_entry: dict) -> str:
    """Decode one `FakeBackend.sent` entry's base64url `raw` field back to the assembled
    RFC822 text, so header/body content can be substring-matched. See the module docstring."""
    data = sent_entry["raw"]
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def test_send_message_puts_exactly_one_message_on_the_wire():
    fake = FakeBackend()
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "send_message", to=["a@example.com"], subject="Hi", body="There")
    assert len(fake.sent) == 1


def test_reply_all_keeps_every_original_recipient_except_me():
    """The whole reason reply_all is its own tool: getting this wrong is public."""
    fake = FakeBackend(messages={"m1": {
        "id": "m1", "threadId": "t1", "payload": {"headers": [
            {"name": "From", "value": "sender@example.org"},
            {"name": "To", "value": "me@example.com, other@example.org"},
            {"name": "Cc", "value": "cc@example.net, me@example.com"},
            {"name": "Message-ID", "value": "<orig@example.org>"}]}}},
        threads={"t1": {}},
        profile={"emailAddress": "me@example.com"})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "reply_all", message_id="m1", body="ack")
    raw = _raw_text(fake.sent[-1])
    assert "sender@example.org" in raw and "other@example.org" in raw
    assert "cc@example.net" in raw
    assert raw.count("me@example.com") == 0, "never reply to yourself"


def test_reply_sets_in_reply_to_from_the_original_message_id():
    fake = FakeBackend(messages={"m1": {"id": "m1", "threadId": "t1", "payload": {"headers": [
        {"name": "From", "value": "s@example.org"},
        {"name": "Message-ID", "value": "<orig@example.org>"}]}}},
        threads={"t1": {}},
        profile={"emailAddress": "me@example.com"})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "reply", message_id="m1", body="ack")
    assert "<orig@example.org>" in _raw_text(fake.sent[-1])


def test_reply_subject_gets_one_re_prefix_not_two():
    fake = FakeBackend(messages={"m1": {"id": "m1", "threadId": "t1", "payload": {"headers": [
        {"name": "From", "value": "s@example.org"},
        {"name": "Subject", "value": "Re: already a reply"}]}}},
        threads={"t1": {}},
        profile={"emailAddress": "me@example.com"})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "reply", message_id="m1", body="ack")
    assert _raw_text(fake.sent[-1]).count("Re:") == 1


def test_an_unknown_argument_is_refused_rather_than_ignored():
    """csa-zendesk's lesson: a silently-dropped argument is a silently-wrong call."""
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    with pytest.raises(Exception, match="unknown argument"):
        _call(s, "send_message", to=["a@example.com"], subject="s", body="b", bbc=["x@y.z"])


def test_attachments_without_a_configured_dir_refuse_with_the_variable_name():
    s = create_server(backend=FakeBackend(), policy=policy.Policy(), attach_policy=None)
    with pytest.raises(Exception, match="CSA_GGC_ATTACH_DIR"):
        _call(s, "send_message", to=["a@example.com"], subject="s", body="b",
              attachments=["anything.pdf"])


def test_get_attachment_writes_to_the_download_dir_and_returns_the_path(tmp_path):
    from csa_google_gmail_calendar._attachments import DownloadPolicy
    d = tmp_path / "d"
    d.mkdir()
    fake = FakeBackend(messages={"m1": {"id": "m1", "threadId": "m1"}},
                       attachments={"att-1": {"data": "aGVsbG8", "size": 5}})
    s = create_server(backend=fake, policy=policy.Policy(),
                      download_policy=DownloadPolicy(str(d)))
    out = _call(s, "get_attachment", message_id="m1", attachment_id="att-1",
                filename="note.txt")
    assert (d / "note.txt").read_bytes() == b"hello"
    assert "note.txt" in str(out)


def test_a_downloaded_attachment_filename_cannot_escape_the_dir(tmp_path):
    """The filename comes from the MESSAGE, which a stranger wrote."""
    from csa_google_gmail_calendar._attachments import DownloadPolicy
    d = tmp_path / "d"
    d.mkdir()
    fake = FakeBackend(messages={"m1": {"id": "m1", "threadId": "m1"}},
                       attachments={"att-1": {"data": "aGVsbG8", "size": 5}})
    s = create_server(backend=fake, policy=policy.Policy(),
                      download_policy=DownloadPolicy(str(d)))
    with pytest.raises(Exception, match="outside|invalid"):
        _call(s, "get_attachment", message_id="m1", attachment_id="att-1",
              filename="../../escaped.txt")


def test_trash_email_exists_and_delete_email_does_not():
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    names = {t.name for t in s._tool_manager.list_tools()}
    assert "trash_email" in names and "untrash_email" in names
    assert "delete_email" not in names


def test_every_gmail_minimum_set_tool_is_registered():
    """Spec §5's minimum set, less `list_history`/`get_profile` (task 13) - 29 tools across
    the reading, composing, sending, organising and disposal tiers."""
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    names = {t.name for t in s._tool_manager.list_tools()}
    expected = {
        "search_messages", "get_message", "get_thread", "list_threads", "get_attachment",
        "list_labels",
        "create_draft", "update_draft", "get_draft", "list_drafts", "delete_draft",
        "send_draft",
        "send_message", "reply", "reply_all", "forward",
        "archive_email", "archive_thread", "modify_message_labels", "modify_thread_labels",
        "mark_read", "mark_unread", "create_label",
        "trash_email", "trash_thread", "untrash_email", "untrash_thread",
        "mark_spam", "unmark_spam",
    }
    assert expected <= names
    assert len(expected) == 29


# --- reading tier, beyond the brief's 9 - one smoke test per remaining tool -------------------

def _message(**overrides):
    base = {"id": "m1", "threadId": "t1", "snippet": "hello there",
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {"headers": [{"name": "From", "value": "a@example.com"},
                                    {"name": "Subject", "value": "Hi"}]}}
    base.update(overrides)
    return base


def test_search_messages_reports_truncation_explicitly():
    fake = FakeBackend(messages={f"m{i}": _message(id=f"m{i}") for i in range(3)})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "search_messages", query="", limit=2)
    assert len(out["messages"]) == 2
    assert out["truncated"] is True
    assert out["next_page_token"] is not None


def test_get_message_truncates_the_body_by_default_and_discloses_it():
    long_body = "x" * 5000
    fake = FakeBackend(messages={"m1": {
        "id": "m1", "threadId": "t1",
        "payload": {"headers": [{"name": "Subject", "value": "Long"}],
                   "mimeType": "text/plain", "body": {"data": _b64(long_body)}}}})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "get_message", message_id="m1")
    assert out["body_truncated"] is True
    assert len(out["body_markdown"]) < len(long_body)
    full = _call(s, "get_message", message_id="m1", full_body=True)
    assert full["body_truncated"] is False
    assert len(full["body_markdown"]) == len(long_body)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_get_thread_returns_message_summaries_not_full_bodies():
    fake = FakeBackend(messages={"m1": _message()}, threads={"t1": {}})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "get_thread", thread_id="t1")
    assert out["messages"][0]["id"] == "m1"
    assert out["messages"][0]["sender"] == "a@example.com"
    assert "body_markdown" not in out["messages"][0]


def test_list_threads_reports_truncation_explicitly():
    fake = FakeBackend(messages={f"m{i}": _message(id=f"m{i}", threadId=f"t{i}")
                                 for i in range(3)},
                       threads={f"t{i}": {} for i in range(3)})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "list_threads", limit=2)
    assert len(out["threads"]) == 2
    assert out["truncated"] is True


def test_list_labels_passes_through():
    fake = FakeBackend()
    fake.create_label(name="Work")
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "list_labels")
    assert out["labels"][0]["name"] == "Work"


def test_list_drafts_and_get_draft_preview_a_composed_message():
    fake = FakeBackend()
    raw = _mime_build(["a@example.com"], "Draft subject", "draft body")
    created = fake.create_draft(raw=raw)
    s = create_server(backend=fake, policy=policy.Policy())
    listed = _call(s, "list_drafts")
    assert listed["drafts"][0]["subject"] == "Draft subject"
    got = _call(s, "get_draft", draft_id=created["id"])
    assert got["to"] == "a@example.com"
    assert got["preview_available"] is True


def test_get_draft_with_no_raw_content_yet_still_returns_its_id():
    """A freshly-created, still-empty draft - `draft_preview_out`'s documented
    `preview_available=False` case."""
    fake = FakeBackend(drafts={"d1": {"id": "d1", "message": {}}})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "get_draft", draft_id="d1")
    assert out["id"] == "d1"
    assert out["preview_available"] is False


def _mime_build(to, subject, body):
    from csa_google_gmail_calendar import _mime
    return _mime.build(to, subject, body)


# --- organising/composing tier -----------------------------------------------------------

def test_create_draft_update_draft_and_delete_draft_round_trip():
    fake = FakeBackend()
    s = create_server(backend=fake, policy=policy.Policy())
    created = _call(s, "create_draft", to=["a@example.com"], subject="s", body="b")
    before = _call(s, "get_draft", draft_id=created["id"])
    assert before["subject"] == "s"
    _call(s, "update_draft", draft_id=created["id"], to=["a@example.com"],
         subject="s2", body="b2")
    after = _call(s, "get_draft", draft_id=created["id"])
    assert after["subject"] == "s2"  # replaced wholesale, not merged
    _call(s, "delete_draft", draft_id=created["id"])
    assert created["id"] not in fake.drafts


def test_modify_message_and_thread_labels():
    fake = FakeBackend(messages={"m1": _message()}, threads={"t1": {}})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "modify_message_labels", message_id="m1", add=["STARRED"], remove=["UNREAD"])
    assert "STARRED" in fake.messages["m1"]["labelIds"]
    assert "UNREAD" not in fake.messages["m1"]["labelIds"]
    _call(s, "modify_thread_labels", thread_id="t1", add=["IMPORTANT"])
    assert "IMPORTANT" in fake.messages["m1"]["labelIds"]


def test_archive_mark_and_label_tools():
    fake = FakeBackend(messages={"m1": _message(), "m2": _message(id="m2", threadId="t1")},
                       threads={"t1": {}})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "archive_email", message_id="m1")
    assert "INBOX" not in fake.messages["m1"]["labelIds"]
    _call(s, "archive_thread", thread_id="t1")
    assert "INBOX" not in fake.messages["m2"]["labelIds"]
    _call(s, "mark_read", message_id="m1")
    assert "UNREAD" not in fake.messages["m1"]["labelIds"]
    _call(s, "mark_unread", message_id="m1")
    assert "UNREAD" in fake.messages["m1"]["labelIds"]
    label = _call(s, "create_label", name="Projects")
    assert label["name"] == "Projects"


def test_trash_untrash_and_spam_tools():
    fake = FakeBackend(messages={"m1": _message(), "m2": _message(id="m2", threadId="t1")},
                       threads={"t1": {}})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "trash_email", message_id="m1")
    assert "TRASH" in fake.messages["m1"]["labelIds"]
    _call(s, "untrash_email", message_id="m1")
    assert "TRASH" not in fake.messages["m1"]["labelIds"]
    _call(s, "trash_thread", thread_id="t1")
    assert "TRASH" in fake.messages["m2"]["labelIds"]
    _call(s, "untrash_thread", thread_id="t1")
    assert "TRASH" not in fake.messages["m2"]["labelIds"]
    _call(s, "mark_spam", message_id="m1")
    assert "SPAM" in fake.messages["m1"]["labelIds"]
    _call(s, "unmark_spam", message_id="m1")
    assert "SPAM" not in fake.messages["m1"]["labelIds"]


# --- sending tier, remaining tools ---------------------------------------------------------

def test_send_draft_puts_the_drafted_message_on_the_wire():
    fake = FakeBackend()
    raw = _mime_build(["a@example.com"], "s", "b")
    created = fake.create_draft(raw=raw)
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "send_draft", draft_id=created["id"])
    assert len(fake.sent) == 1
    assert created["id"] not in fake.drafts


def test_forward_quotes_the_original_and_prefixes_fwd():
    fake = FakeBackend(messages={"m1": {
        "id": "m1", "threadId": "t1",
        "payload": {"headers": [{"name": "From", "value": "orig@example.com"},
                                {"name": "Subject", "value": "Budget"}],
                   "mimeType": "text/plain",
                   "body": {"data": _b64("original content")}}}})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "forward", message_id="m1", to=["new@example.com"], body="fyi")
    raw = _raw_text(fake.sent[-1])
    assert "Fwd: Budget" in raw
    assert "original content" in raw
    assert "fyi" in raw
    assert "new@example.com" in raw


def test_reply_all_refuses_when_nobody_is_left_but_yourself():
    fake = FakeBackend(messages={"m1": {"id": "m1", "threadId": "t1", "payload": {"headers": [
        {"name": "From", "value": "me@example.com"},
        {"name": "To", "value": "me@example.com"}]}}},
        threads={"t1": {}},
        profile={"emailAddress": "me@example.com"})
    s = create_server(backend=fake, policy=policy.Policy())
    with pytest.raises(Exception, match="nobody"):
        _call(s, "reply_all", message_id="m1", body="ack")


def test_reply_refuses_when_the_original_has_no_from_header():
    fake = FakeBackend(messages={"m1": {"id": "m1", "threadId": "t1", "payload": {"headers": []}}},
                       threads={"t1": {}})
    s = create_server(backend=fake, policy=policy.Policy())
    with pytest.raises(Exception, match="From"):
        _call(s, "reply", message_id="m1", body="ack")


def test_get_attachment_without_a_configured_dir_refuses_with_the_variable_name():
    fake = FakeBackend(messages={"m1": {"id": "m1"}},
                       attachments={"att-1": {"data": "aGVsbG8", "size": 5}})
    s = create_server(backend=fake, policy=policy.Policy(), download_policy=None)
    with pytest.raises(Exception, match="CSA_GGC_DOWNLOAD_DIR"):
        _call(s, "get_attachment", message_id="m1", attachment_id="att-1", filename="a.txt")


def test_get_attachment_refuses_a_symlinked_escape_even_without_dotdot(tmp_path):
    """`DownloadPolicy.resolve`'s own containment lesson, applied on the write side too: the
    check must run on the RESOLVED path, because a symlink can point outside the root under a
    perfectly innocent-looking, dot-dot-free name."""
    from csa_google_gmail_calendar._attachments import DownloadPolicy
    d = tmp_path / "d"
    d.mkdir()
    (tmp_path / "outside").mkdir()
    (d / "escape").symlink_to(tmp_path / "outside")
    fake = FakeBackend(messages={"m1": {"id": "m1"}},
                       attachments={"att-1": {"data": "aGVsbG8", "size": 5}})
    s = create_server(backend=fake, policy=policy.Policy(),
                      download_policy=DownloadPolicy(str(d)))
    with pytest.raises(Exception, match="outside"):
        _call(s, "get_attachment", message_id="m1", attachment_id="att-1",
              filename="escape/pwned.txt")


def test_get_attachment_refuses_to_overwrite_an_existing_file_in_the_download_dir(tmp_path):
    """FIX 1 (final whole-branch review, CINO 2026-09-26) - the end-to-end chain: a stranger's
    message names its attachment the same as a real file already sitting in the download
    directory (e.g. one the user themselves put there, or - before this fix - one
    `send_message` reads outgoing attachments from). Downloading it must refuse rather than
    silently overwrite, and the original bytes must survive untouched."""
    from csa_google_gmail_calendar._attachments import DownloadPolicy
    d = tmp_path / "d"
    d.mkdir()
    real_file = d / "q3-budget.pdf"
    real_file.write_bytes(b"THE USER'S REAL BUDGET")
    fake = FakeBackend(messages={"m1": {"id": "m1"}},
                       attachments={"att-1": {"data": "aGVsbG8", "size": 5}})
    s = create_server(backend=fake, policy=policy.Policy(),
                      download_policy=DownloadPolicy(str(d)))
    with pytest.raises(Exception, match="already exists"):
        _call(s, "get_attachment", message_id="m1", attachment_id="att-1",
              filename="q3-budget.pdf")
    assert real_file.read_bytes() == b"THE USER'S REAL BUDGET"


def test_download_dir_and_attach_dir_must_be_different_directories(tmp_path):
    """The misconfiguration that recreates the whole bug: `CSA_GGC_ATTACH_DIR` and
    `CSA_GGC_DOWNLOAD_DIR` pointed at the SAME directory must be impossible to hold, refused at
    server construction naming both variables, rather than merely discouraged in prose."""
    from csa_google_gmail_calendar._attachments import AttachmentPolicy, DownloadPolicy
    same = tmp_path / "same"
    same.mkdir()
    with pytest.raises(Exception, match="CSA_GGC_ATTACH_DIR.*CSA_GGC_DOWNLOAD_DIR|"
                                        "CSA_GGC_DOWNLOAD_DIR.*CSA_GGC_ATTACH_DIR"):
        create_server(backend=FakeBackend(), policy=policy.Policy(),
                     attach_policy=AttachmentPolicy(str(same)),
                     download_policy=DownloadPolicy(str(same)))
