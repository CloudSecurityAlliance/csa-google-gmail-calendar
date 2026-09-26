"""`_tools/_schemas.py` helpers, exercised directly rather than only through the mail tools
that call them - `ensure_fwd_prefix`'s double-prefix guard, `attachment_ref_out`'s plain
conversion, and `draft_preview_out`'s three edge cases (no matching body part, an unparseable
`raw`, and a body long enough to need truncation) are all easy to miss when only reached
through a full `list_drafts`/`get_draft` round trip."""
import base64

from csa_google_gmail_calendar import _mime
from csa_google_gmail_calendar.mail import AttachmentRef
from csa_google_gmail_calendar.mcp._tools import _schemas


def test_ensure_fwd_prefix_does_not_double_an_existing_fwd_prefix():
    assert _schemas.ensure_fwd_prefix("Fwd: budget") == "Fwd: budget"


def test_ensure_fwd_prefix_recognises_the_short_fw_variant_too():
    assert _schemas.ensure_fwd_prefix("FW: budget") == "FW: budget"


def test_ensure_fwd_prefix_adds_the_prefix_when_absent():
    assert _schemas.ensure_fwd_prefix("budget") == "Fwd: budget"


def test_attachment_ref_out_converts_the_four_fields():
    ref = AttachmentRef(attachment_id="att-1", filename="report.pdf",
                        mime_type="application/pdf", size_bytes=4096)
    assert _schemas.attachment_ref_out(ref) == {
        "attachment_id": "att-1", "filename": "report.pdf",
        "mime_type": "application/pdf", "size_bytes": 4096,
    }


def _b64(raw_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")


def test_draft_preview_with_no_text_or_html_part_reports_no_body():
    """A draft whose only content is a non-text part has nothing `get_body` can find under
    `("plain", "html")` - `body_part` is `None`, and the preview must say so rather than
    raising, while still reporting the draft parseable (`preview_available=True`: the
    headers WERE read successfully)."""
    raw_bytes = (
        b"To: a@example.com\r\n"
        b"Subject: Attachment only\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"YmluYXJ5LWRhdGE=\r\n"
    )
    raw = _b64(raw_bytes)
    out = _schemas.draft_preview_out({"id": "d1", "message": {"raw": raw}})
    assert out["preview_available"] is True
    assert out["body_preview"] == ""


def test_draft_preview_with_unparseable_raw_reports_unavailable_not_a_raised_exception():
    """`raw` that is not valid base64 at all - `_decode_b64` itself raises - must still return
    the draft's id with `preview_available=False`, per this function's own documented
    best-effort contract."""
    out = _schemas.draft_preview_out({"id": "d1", "message": {"raw": "!!!not base64!!!"}})
    assert out["id"] == "d1"
    assert out["preview_available"] is False
    assert out["body_preview"] == ""


def test_draft_preview_truncates_a_body_longer_than_the_preview_limit():
    long_body = "x" * (_schemas.BODY_PREVIEW_CHARS + 500)
    raw = _mime.build(["a@example.com"], "Long draft", long_body)
    out = _schemas.draft_preview_out({"id": "d1", "message": {"raw": raw}})
    assert out["preview_available"] is True
    assert out["body_truncated"] is True
    assert len(out["body_preview"]) == _schemas.BODY_PREVIEW_CHARS
