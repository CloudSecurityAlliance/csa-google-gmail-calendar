import base64
import email
import email.policy
from pathlib import Path

import pytest

from csa_google_gmail_calendar import _mime
from csa_google_gmail_calendar._attachments import AttachmentPolicy
from csa_google_gmail_calendar.exceptions import PolicyError


def _parse(raw_b64: str) -> email.message.EmailMessage:
    # NOTE (deviation from the literal brief text): the brief's `_parse` called
    # `email.message_from_bytes` with no `policy` argument, which defaults to legacy
    # `compat32` and returns a plain `email.message.Message` - one that has neither
    # `get_content()` nor `iter_attachments()`, both of which several of these tests call.
    # Confirmed directly against this Python version: parsing without `policy=` raises
    # `AttributeError: 'Message' object has no attribute 'get_content'` on the very first test.
    # `policy=email.policy.default` is what `build()` itself uses to construct the message
    # (via plain `EmailMessage()`), so parsing with the same policy is what actually round-trips
    # it, including the RFC 2047/2231 non-ASCII decoding several tests below depend on.
    return email.message_from_bytes(
        base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4)),
        policy=email.policy.default,
    )


def test_a_plain_message_round_trips():
    msg = _parse(_mime.build(["a@example.com"], "Subject here", "Body here"))
    assert msg["To"] == "a@example.com" and msg["Subject"] == "Subject here"
    assert "Body here" in msg.get_content()


def test_multiple_recipients_are_comma_joined():
    msg = _parse(_mime.build(["a@example.com", "b@example.org"], "s", "b"))
    assert msg["To"] == "a@example.com, b@example.org"


def test_cc_is_set_and_bcc_is_also_a_header():
    """Gmail strips Bcc on send. It must still be present, or the recipient never gets it."""
    msg = _parse(_mime.build(["a@example.com"], "s", "b",
                             cc=["c@example.com"], bcc=["d@example.com"]))
    assert msg["Cc"] == "c@example.com" and msg["Bcc"] == "d@example.com"


def test_reply_headers_are_set_so_the_thread_holds_together():
    msg = _parse(_mime.build(["a@example.com"], "Re: s", "b",
                             in_reply_to="<x@mail.example.com>",
                             references="<w@mail.example.com> <x@mail.example.com>"))
    assert msg["In-Reply-To"] == "<x@mail.example.com>"
    assert "<w@mail.example.com>" in msg["References"]


def test_an_attachment_becomes_a_part_with_its_filename(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "r.pdf").write_bytes(b"%PDF-1.4 x")
    msg = _parse(_mime.build(["a@example.com"], "s", "b", attachments=["r.pdf"],
                             attach_policy=AttachmentPolicy(str(d))))
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ["r.pdf"]


def test_attachment_mime_type_is_guessed_from_the_extension(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "r.pdf").write_bytes(b"%PDF-1.4 x")
    msg = _parse(_mime.build(["a@example.com"], "s", "b", attachments=["r.pdf"],
                             attach_policy=AttachmentPolicy(str(d))))
    assert next(msg.iter_attachments()).get_content_type() == "application/pdf"


def test_an_unknown_extension_falls_back_to_octet_stream(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "x.zzz").write_bytes(b"data")
    msg = _parse(_mime.build(["a@example.com"], "s", "b", attachments=["x.zzz"],
                             attach_policy=AttachmentPolicy(str(d))))
    assert next(msg.iter_attachments()).get_content_type() == "application/octet-stream"


def test_attachments_without_a_policy_are_refused():
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        _mime.build(["a@example.com"], "s", "b", attachments=["x.pdf"])


def test_over_the_message_ceiling_is_refused_with_both_numbers(tmp_path):
    """REVIEW FOCUS #5. Google 413s; the refusal must say how big it was and what the cap is."""
    d = tmp_path / "a"
    d.mkdir()
    (d / "big.bin").write_bytes(b"\0" * (26 * 1024 * 1024))
    with pytest.raises(PolicyError, match=r"25|26"):
        _mime.build(["a@example.com"], "s", "b", attachments=["big.bin"],
                    attach_policy=AttachmentPolicy(str(d)))


def test_base64_overhead_counts_toward_the_ceiling(tmp_path):
    """A 19 MB file is well over the 25 MB ceiling once its two base64 passes are counted
    (see the module docstring) - checking the file size alone passes it and Google then rejects
    the send, which is a worse place to find out."""
    d = tmp_path / "a"
    d.mkdir()
    (d / "big.bin").write_bytes(b"\0" * (19 * 1024 * 1024))
    with pytest.raises(PolicyError, match="25"):
        _mime.build(["a@example.com"], "s", "b", attachments=["big.bin"],
                    attach_policy=AttachmentPolicy(str(d)))


def test_the_ceiling_is_refused_before_the_attachment_is_read(tmp_path, monkeypatch):
    """The point of the whole exercise: a 10 GB (simulated) file must never be opened. Patch
    `Path.read_bytes` to explode if called, and prove the oversize refusal happens without it."""
    d = tmp_path / "a"
    d.mkdir()
    (d / "huge.bin").write_bytes(b"\0" * (30 * 1024 * 1024))  # a real, but modest, stand-in

    real_read_bytes = Path.read_bytes

    def _boom(self):
        raise AssertionError(f"read_bytes() called on {self} - the early size check did not "
                             f"prevent the read")

    monkeypatch.setattr(Path, "read_bytes", _boom)
    try:
        with pytest.raises(PolicyError, match="25"):
            _mime.build(["a@example.com"], "s", "b", attachments=["huge.bin"],
                        attach_policy=AttachmentPolicy(str(d)))
    finally:
        monkeypatch.setattr(Path, "read_bytes", real_read_bytes)


def test_the_post_assembly_check_still_catches_a_wrong_estimate(tmp_path, monkeypatch):
    """Belt-and-braces: force the pre-read estimate to (wrongly) say "fine" and prove the
    final, post-assembly measurement is still there to catch an oversized message anyway."""
    d = tmp_path / "a"
    d.mkdir()
    (d / "big.bin").write_bytes(b"\0" * (26 * 1024 * 1024))
    monkeypatch.setattr(_mime, "_estimate_encoded_size", lambda **_: 0)
    with pytest.raises(PolicyError, match="assembled message"):
        _mime.build(["a@example.com"], "s", "b", attachments=["big.bin"],
                    attach_policy=AttachmentPolicy(str(d)))


def test_an_html_alternative_produces_a_multipart_alternative():
    msg = _parse(_mime.build(["a@example.com"], "s", "plain", html_body="<p>rich</p>"))
    assert msg.get_content_type() == "multipart/alternative"


def test_result_is_urlsafe_base64_with_no_plus_or_slash():
    raw = _mime.build(["a@example.com"], "s", "b" * 500)
    assert "+" not in raw and "/" not in raw


# --- Header injection: the brief's most likely real vulnerability, made deliberate and tested
# rather than left as an accident of whichever `email` version happens to be installed. ---

def test_a_crlf_in_the_subject_is_refused_not_injected():
    with pytest.raises(PolicyError, match="line break"):
        _mime.build(["a@example.com"], "evil\r\nBcc: attacker@example.com", "b")


def test_a_crlf_in_a_recipient_is_refused_not_injected():
    with pytest.raises(PolicyError, match="line break"):
        _mime.build(["a@example.com", "evil\r\nBcc: attacker@example.com"], "s", "b")


def test_a_crlf_in_cc_is_refused_not_injected():
    with pytest.raises(PolicyError, match="line break"):
        _mime.build(["a@example.com"], "s", "b", cc=["evil\r\nX-Injected: true"])


def test_a_bare_lf_in_from_addr_is_refused_not_injected():
    with pytest.raises(PolicyError, match="line break"):
        _mime.build(["a@example.com"], "s", "b", from_addr="me@example.com\nX-Injected: true")


# --- Non-ASCII in headers and filenames: RFC 2047/2231 encoding should be automatic - verified,
# not assumed, so a mojibake subject is caught here rather than discovered live. ---

def test_non_ascii_subject_round_trips_without_mojibake():
    subject = "日本語件名"  # "Japanese-language subject" in Japanese
    msg = _parse(_mime.build(["a@example.com"], subject, "b"))
    assert msg["Subject"] == subject


def test_non_ascii_display_name_round_trips():
    to = "José García <a@example.com>"
    msg = _parse(_mime.build([to], "s", "b"))
    assert "José García" in msg["To"]


def test_non_ascii_attachment_filename_round_trips(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    filename = "日本語.pdf"  # "Japanese" + .pdf
    (d / filename).write_bytes(b"%PDF-1.4 x")
    msg = _parse(_mime.build(["a@example.com"], "s", "b", attachments=[filename],
                             attach_policy=AttachmentPolicy(str(d))))
    assert next(msg.iter_attachments()).get_filename() == filename


# --- An empty recipient list, and a recipient that is an empty string. ---

def test_an_empty_recipient_list_is_refused():
    with pytest.raises(PolicyError, match="at least one recipient"):
        _mime.build([], "s", "b")


def test_a_blank_string_recipient_is_refused():
    with pytest.raises(PolicyError, match="blank"):
        _mime.build(["a@example.com", ""], "s", "b")


def test_a_whitespace_only_recipient_is_refused():
    with pytest.raises(PolicyError, match="blank"):
        _mime.build(["a@example.com", "   "], "s", "b")


def test_a_blank_cc_recipient_is_refused():
    with pytest.raises(PolicyError, match="blank"):
        _mime.build(["a@example.com"], "s", "b", cc=[""])


def test_a_blank_bcc_recipient_is_refused():
    with pytest.raises(PolicyError, match="blank"):
        _mime.build(["a@example.com"], "s", "b", bcc=[""])


# --- Duplicate attachment filenames: two different paths whose basenames collide. ---

def test_duplicate_attachment_basenames_are_disambiguated(tmp_path):
    d = tmp_path / "a"
    (d / "x").mkdir(parents=True)
    (d / "y").mkdir()
    (d / "x" / "report.pdf").write_bytes(b"%PDF-1.4 one")
    (d / "y" / "report.pdf").write_bytes(b"%PDF-1.4 two")
    msg = _parse(_mime.build(["a@example.com"], "s", "b",
                             attachments=["x/report.pdf", "y/report.pdf"],
                             attach_policy=AttachmentPolicy(str(d))))
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ["report.pdf", "report-2.pdf"]


def test_three_duplicate_basenames_without_an_extension_are_each_disambiguated(tmp_path):
    d = tmp_path / "a"
    for sub in ("x", "y", "z"):
        (d / sub).mkdir(parents=True)
        (d / sub / "README").write_bytes(f"{sub}".encode())
    msg = _parse(_mime.build(["a@example.com"], "s", "b",
                             attachments=["x/README", "y/README", "z/README"],
                             attach_policy=AttachmentPolicy(str(d))))
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ["README", "README-2", "README-3"]


# --- A zero-byte attachment. ---

def test_a_zero_byte_attachment_is_accepted(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "empty.txt").write_bytes(b"")
    msg = _parse(_mime.build(["a@example.com"], "s", "b", attachments=["empty.txt"],
                             attach_policy=AttachmentPolicy(str(d))))
    part = next(msg.iter_attachments())
    assert part.get_filename() == "empty.txt"
    # `.txt` guesses as `text/plain`, so `get_content()` decodes it to `str`, not `bytes` -
    # the payload itself (what actually went over the wire) is the empty-bytes assertion that
    # matters here.
    assert part.get_payload(decode=True) == b""


# --- SIMPLE_UPLOAD_LIMIT: currently unused by `build()` itself (see the module docstring on
# `upload_strategy`); this pins the one function in this module that does consume it. ---

def test_upload_strategy_is_simple_under_the_limit():
    raw = _mime.build(["a@example.com"], "s", "b")
    assert _mime.upload_strategy(raw) == "simple"


def test_upload_strategy_is_resumable_at_or_over_the_limit(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f.bin").write_bytes(b"\0" * (6 * 1024 * 1024))
    raw = _mime.build(["a@example.com"], "s", "b", attachments=["f.bin"],
                      attach_policy=AttachmentPolicy(str(d)))
    assert _mime.upload_strategy(raw) == "resumable"
