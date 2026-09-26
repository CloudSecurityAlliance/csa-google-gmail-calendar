import base64
import re

import pytest

from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.exceptions import NotFoundError
from csa_google_gmail_calendar.mail import _MAX_MIME_DEPTH, Mail, _count_utf8_replacements


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
    # FIX 1 (fix round 1): a genuinely bodyless message must NOT carry the depth-truncation
    # disclosure - the two "none" cases (nothing here vs. something here I didn't look at) have
    # to be distinguishable, or this test and the deep-nesting test below assert the same
    # observable result for different reasons and the trap is back.
    assert not any("nested past" in t for t in m.transformations)


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
    assert m.body_source == "none"
    # FIX 1 (fix round 1): "none" here must NOT look like the genuinely-bodyless "none" in
    # `test_a_message_with_no_body_part_at_all_says_so` above - a caller (and this test suite)
    # has to be able to tell "nothing here" from "something here I never inspected". Discriminates
    # by content, not merely presence: this message's disclosure names the depth limit, the
    # bodyless one above has no such entry at all.
    assert any("nested past" in t and str(_MAX_MIME_DEPTH) in t for t in m.transformations)


def test_payload_exactly_at_the_depth_bound_is_not_flagged_as_truncated():
    """FIX 1 boundary check: a message that reaches `_MAX_MIME_DEPTH` and stops there on its
    own (no further children past the bound) is not truncated - `_walk` only flags truncation
    when it stops descending WHILE children remain unvisited."""
    leaf = {"mimeType": "text/plain", "body": {"data": _b64("at the edge")}}
    payload = leaf
    for _ in range(_MAX_MIME_DEPTH):
        payload = {"mimeType": "multipart/mixed", "parts": [payload]}
    m = Mail(FakeBackend(messages={"m1": _msg(payload)})).read_message("m1")
    assert m.body_markdown == "at the edge"
    assert not any("nested past" in t for t in m.transformations)


def test_message_id_not_found_raises_notfounderror_not_keyerror():
    with pytest.raises(NotFoundError):
        Mail(FakeBackend()).read_message("does-not-exist")


def test_malformed_base64_in_a_body_part_does_not_break_the_whole_message():
    """FIX 2 (fix round 1). A single leftover base64 character (length 1 more than a multiple
    of 4) cannot be repaired by re-padding and raises `binascii.Error`. That must not escape
    this module as a bare exception - and one corrupt part must not make an otherwise-readable
    message unreadable: the good `text/html` sibling should still come through."""
    p = {"mimeType": "multipart/alternative", "headers": [], "parts": [
        {"mimeType": "text/plain", "body": {"data": "a"}},  # invalid: 1 leftover char
        {"mimeType": "text/html", "body": {"data": _b64("<p>fallback</p>")}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.body_source == "text/html"
    assert "fallback" in m.body_markdown
    assert any("could not be decoded" in t and "text/plain" in t and "m1" in t
               for t in m.transformations)


def _disclosed_replacement_count(transformations: list[str]) -> int:
    """Extract the "N byte(s) ... were not valid UTF-8" count as an integer, so a test can pin
    the exact number rather than doing a substring check (`"2" in t` also matches `"12"`,
    `"20"`, or any other string containing that digit - fix round 2 finding)."""
    matches = [re.search(r"(\d+) byte\(s\).*not valid UTF-8", t) for t in transformations]
    hits = [mt for mt in matches if mt]
    assert len(hits) == 1, f"expected exactly one UTF-8-replacement disclosure, got: {transformations}"
    return int(hits[0].group(1))


def test_invalid_utf8_bytes_are_replaced_and_the_count_is_the_true_byte_count():
    """FIX 3 (fix round 1), corrected in fix round 2. `errors="replace"` is right - refusing to
    show a body over two bad bytes helps nobody - but silent substitution with no disclosure
    means `transformations` positively (and wrongly) asserts nothing happened.

    Two cases with DIFFERENT counts, so a hardcoded constant in the implementation would fail
    at least one of them (fix round 2 finding: one test with one number cannot distinguish a
    correct computation from a lucky literal):

    - a single stray invalid byte: 1 byte invalid, 1 replacement character in the output - counts
      agree, so this case alone would not have caught the original bug.
    - a truncated multi-byte sequence: 2 bytes invalid (verified against `bytes.decode`'s own
      `UnicodeDecodeError.start`/`.end`) but only 1 replacement character in the output, because
      `bytes.decode(errors="replace")` collapses a truncated sequence to ONE U+FFFD. This is
      exactly the case that made `str.count("�")` on the output wrong (fix round 2): that
      approach would have disclosed "1" here, not the true byte count "2".
    """
    single_byte = b"ok \xff done"  # one byte that is not valid UTF-8 on its own
    m = Mail(FakeBackend(messages={"m1": _msg({
        "mimeType": "text/plain", "headers": [],
        "body": {"data": base64.urlsafe_b64encode(single_byte).decode()},
    })})).read_message("m1")
    assert "�" in m.body_markdown
    assert _disclosed_replacement_count(m.transformations) == 1

    truncated_sequence = b"abc\xe2\x82"  # a 3-byte sequence missing its final byte
    m2 = Mail(FakeBackend(messages={"m1": _msg({
        "mimeType": "text/plain", "headers": [],
        "body": {"data": base64.urlsafe_b64encode(truncated_sequence).decode()},
    })})).read_message("m1")
    assert m2.body_markdown.count("�") == 1  # ONE replacement character in the output...
    assert _disclosed_replacement_count(m2.transformations) == 2  # ...but TWO bytes were invalid


def test_counting_invalid_utf8_is_correct_on_a_large_all_invalid_body():
    """Fix round 3. The round-2 loop-based counter was measured quadratic on an all-invalid
    body: ~18s extrapolated for a 1 MB body, ~3 hours for Gmail's own 25 MB message ceiling - a
    reachable input (a binary attachment mislabelled `text/plain` produces exactly this byte
    pattern), not a contrived one. A timing assertion here would be flaky, so this pins WORK
    instead: on the linear fix, 200,000 bytes complete in a small fraction of a second (this
    whole test suite runs in well under a second); on the reintroduced quadratic version it is
    slow enough to notice immediately in any normal test run. See
    `test_utf8_replacement_counting_never_reslices_its_input` below for a check on the same
    property that does not depend on how long anything takes."""
    body = b"\xff" * 200_000  # every byte individually invalid: the worst case for the old loop
    m = Mail(FakeBackend(messages={"m1": _msg({
        "mimeType": "text/plain", "headers": [],
        "body": {"data": base64.urlsafe_b64encode(body).decode()},
    })})).read_message("m1")
    assert m.body_markdown == "�" * 200_000
    assert _disclosed_replacement_count(m.transformations) == 200_000


def test_utf8_replacement_counting_never_reslices_its_input():
    """Fix round 3, the direct (non-timing) proof. The round-2 implementation's defect was
    concretely `remaining = remaining[exc.end:]` - re-slicing a shrinking buffer once per
    invalid byte - and switching that slice to a `memoryview` (avoiding the copy) turned out NOT
    to fix the underlying cost either (measured directly - see `_count_utf8_replacements`'s
    docstring). The implementation kept here never slices its input at all: it makes exactly one
    `bytes.decode()` call over the original object and lets the registered error handler do the
    counting via `codecs`' own per-error callback.

    Asserted here without any wall clock: `_SpyBytes` is a `bytes` subclass that records every
    `[start:]`-style slice taken of it. `bytes.decode()` reads the underlying buffer directly (it
    does not go through `__getitem__`), so this only fires if OUR code slices the object - which
    the chosen implementation never does, and the round-2 loop did on every single iteration.
    Confirmed by hand against the reverted round-2 loop as a mutation check: it recorded a
    non-zero slice against this same input (only the FIRST slice is visible this way, because
    `bytes.__getitem__` on a subclass returns a plain `bytes` instance rather than another
    `_SpyBytes` - which is exactly why this test cannot ALSO catch a memoryview-based
    reintroduction: `memoryview(raw)` never calls `raw.__getitem__` either. It pins the specific
    defect this project actually had and would have caught it; it is not a universal proof
    against every possible quadratic rewrite, which is what
    `test_counting_invalid_utf8_is_correct_on_a_large_all_invalid_body` above is for.
    """
    slices_taken: list[object] = []

    class _SpyBytes(bytes):
        def __getitem__(self, item):
            slices_taken.append(item)
            return super().__getitem__(item)

    raw = _SpyBytes(b"\xff" * 5000)
    assert _count_utf8_replacements(raw) == 5000
    assert slices_taken == []


def test_a_backend_that_raises_bare_keyerror_is_still_translated():
    """`Backend` is a structural Protocol, not a guarantee every implementation raises
    `NotFoundError` the way `FakeBackend` does. `Mail` must not let a bare `KeyError` from some
    other backend escape past its own exception hierarchy."""

    class _KeyErrorBackend:
        def get_message(self, *, message_id, fmt="full"):
            raise KeyError(message_id)

    with pytest.raises(NotFoundError):
        Mail(_KeyErrorBackend()).read_message("m1")
