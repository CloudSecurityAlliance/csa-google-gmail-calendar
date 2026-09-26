import base64

import pytest

from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.exceptions import NotFoundError
from csa_google_gmail_calendar.mail import Mail


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def _msg(payload, mid="m1"):
    return {"id": mid, "threadId": "t1", "labelIds": ["INBOX"], "payload": payload}


def test_plain_text_body_is_read_straight_through():
    p = {"mimeType": "text/plain", "headers": [{"name": "Subject", "value": "Hi"}],
         "body": {"data": _b64("hello there")}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.body_markdown == "hello there"
    assert m.body_source == "text/plain"


def test_html_only_message_is_converted_not_dropped():
    """REVIEW FOCUS #3. Gmail returns HTML-only bodies routinely. A reader that assumes
    text/plain exists returns nothing and the mailbox looks empty."""
    p = {"mimeType": "text/html", "headers": [{"name": "Subject", "value": "Hi"}],
         "body": {"data": _b64("<p>hello <b>there</b></p>")}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert "hello" in m.body_markdown and "there" in m.body_markdown
    assert m.body_source == "text/html"


def test_multipart_alternative_prefers_plain_over_html():
    p = {"mimeType": "multipart/alternative", "headers": [], "parts": [
        {"mimeType": "text/plain", "body": {"data": _b64("plain version")}},
        {"mimeType": "text/html", "body": {"data": _b64("<p>html version</p>")}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.body_markdown == "plain version"


def test_multipart_alternative_falls_back_to_html_when_plain_part_is_empty():
    """Preferring text/plain BLINDLY (present-but-empty still wins) yields an empty body on
    exactly the messages this exists to handle: a plain alternative Gmail carries only for
    compatibility, with all the real content in the html part. Emptiness, not presence, is
    what should lose."""
    p = {"mimeType": "multipart/alternative", "headers": [], "parts": [
        {"mimeType": "text/plain", "body": {"data": _b64("")}},
        {"mimeType": "text/html", "body": {"data": _b64("<p>html only</p>")}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.body_source == "text/html"
    assert "html only" in m.body_markdown


def test_nested_multipart_related_inside_alternative_is_walked():
    p = {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "multipart/related", "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>deep</p>")}}]}]}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert "deep" in m.body_markdown
    # body_source names the leaf part's MIME type, not the nesting path that led to it.
    assert m.body_source == "text/html"


def test_a_message_with_no_body_part_at_all_says_so():
    p = {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "application/pdf", "filename": "x.pdf",
         "body": {"attachmentId": "a1", "size": 10}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.body_source == "none"
    assert m.body_markdown == ""
    assert len(m.attachments) == 1


def test_attachments_are_listed_with_id_name_type_and_size():
    p = {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "text/plain", "body": {"data": _b64("see attached")}},
        {"mimeType": "application/pdf", "filename": "report.pdf",
         "body": {"attachmentId": "att-1", "size": 4096}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert [(a.filename, a.mime_type, a.size_bytes) for a in m.attachments] \
        == [("report.pdf", "application/pdf", 4096)]
    assert m.attachments[0].attachment_id == "att-1"


def test_inline_image_without_a_filename_is_not_reported_as_an_attachment():
    """A cid: image is part of the body, not something a person attached."""
    p = {"mimeType": "multipart/related", "headers": [], "parts": [
        {"mimeType": "text/html", "body": {"data": _b64("<p>hi</p>")}},
        {"mimeType": "image/png", "filename": "",
         "headers": [{"name": "Content-Disposition", "value": "inline"}],
         "body": {"attachmentId": "img-1", "size": 99}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.attachments == []


def test_body_conversion_is_disclosed_as_a_transformation():
    """DEC-021: disclose what you changed. A converted body is not the original."""
    p = {"mimeType": "text/html", "headers": [], "body": {"data": _b64("<p>x</p>")}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert any("html" in t for t in m.transformations)


def test_hidden_html_content_is_surfaced_not_silently_dropped():
    """A `display:none` element's text never reaches a reader through normal rendering, so it
    is removed from the Markdown body - but disclosed, not discarded, per DEC-021."""
    p = {"mimeType": "text/html", "headers": [],
         "body": {"data": _b64('<p>visible</p><p style="display:none">secret</p>')}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert "visible" in m.body_markdown
    assert "secret" not in m.body_markdown
    assert any("hidden" in t and "secret" in t for t in m.transformations)


def test_bidi_override_codepoints_are_stripped_and_counted():
    """A RIGHT-TO-LEFT OVERRIDE can make a body render as text it does not contain."""
    p = {"mimeType": "text/plain", "headers": [],
         "body": {"data": _b64("safe‮txt.exe")}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert "‮" not in m.body_markdown
    assert any("bidi" in t for t in m.transformations)


def test_deeply_nested_payload_does_not_blow_the_stack():
    """A malformed or adversarial message could nest `parts` far deeper than any real MIME
    tree does (mixed > alternative > related > text is four levels). `_walk` bounds its
    recursion so a hostile message degrades to "some deep parts are not walked", not a
    `RecursionError` that takes the whole read down with it.

    Uses a minimal stub backend, not `FakeBackend`: `FakeBackend.get_message` returns its
    result through a plain `copy.deepcopy`, which has no depth bound of its own and would
    exhaust Python's recursion limit before `_walk` ever runs - a confound this test needs to
    avoid to isolate what it is actually checking (`_walk`'s own bound, not `copy.deepcopy`'s
    lack of one)."""
    leaf = {"mimeType": "text/plain", "body": {"data": _b64("buried")}}
    payload = leaf
    for _ in range(5000):
        payload = {"mimeType": "multipart/mixed", "parts": [payload]}
    message = _msg(payload)

    class _DeepMessageBackend:
        def get_message(self, *, message_id, fmt="full"):
            return message

    # Must not raise RecursionError.
    m = Mail(_DeepMessageBackend()).read_message("m1")
    # The leaf is past `_MAX_MIME_DEPTH`, so it is legitimately not found - "none" is the
    # honest answer for a message this malformed, not a crash.
    assert m.body_source == "none"


def test_message_id_not_found_raises_notfounderror_not_keyerror():
    with pytest.raises(NotFoundError):
        Mail(FakeBackend()).read_message("does-not-exist")


def test_a_backend_that_raises_bare_keyerror_is_still_translated():
    """`Backend` is a structural Protocol, not a guarantee every implementation raises
    `NotFoundError` the way `FakeBackend` does. `Mail` must not let a bare `KeyError` from some
    other backend escape past its own exception hierarchy."""

    class _KeyErrorBackend:
        def get_message(self, *, message_id, fmt="full"):
            raise KeyError(message_id)

    with pytest.raises(NotFoundError):
        Mail(_KeyErrorBackend()).read_message("m1")
