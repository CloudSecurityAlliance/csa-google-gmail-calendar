"""RFC 2822 assembly. There is no attachment upload endpoint (issue #8) - an attachment is a
part of a message body that rides along on drafts.create / drafts.update / messages.send, so
this is where "attachment support" actually lives.

**The ceiling is checked BEFORE any attachment is read, not after assembly.** An earlier
version of this module built the whole message first and measured the result:

    raw = msg.as_bytes()
    encoded = base64.urlsafe_b64encode(raw)
    if len(encoded) > MESSAGE_LIMIT: ...

That reads every attachment into memory before deciding it is too large - the check causes the
harm it exists to prevent. A 10 GB file pointed at by an allowlisted path would be read in full
and only then rejected. This version stats every attachment first (`Path.stat().st_size` reads
no file content) and estimates the encoded size from those numbers alone, refusing before a
single `read_bytes()` call. The post-assembly measurement stays too, as a belt-and-braces final
guard against an error in the estimate - it is cheap once the message is already built, and it
is not the one this module depends on to avoid the read.

**Why the estimate uses a squared base64 factor, not a single 4/3 pass.** Binary attachment
content is never sent as `8bit`/`binary` - `EmailMessage.add_attachment` always chooses `base64`
Content-Transfer-Encoding for it, which is one 4/3 expansion. The ENTIRE assembled message
(headers plus that already-base64'd attachment text) is then base64url-encoded again, because
that is the literal contract of the Gmail API's `raw` field - a second, independent 4/3
expansion. Measured directly: a 19 MB attachment assembles to ~25.7 MB of raw MIME and ~34.2 MB
once base64url-encoded - close to `19 * (4/3)**2 ~= 33.8 MB`, not the ~25.3 MB a single 4/3 pass
predicts. An estimate built on the single-pass factor would under-count real attachments large
enough to matter and let some of them straight through to a full read before the final,
post-assembly check caught them - not the 10 GB catastrophe, but not "refused before the read"
either. Squaring the factor keeps the guarantee honest.
"""
from __future__ import annotations

import base64
import math
import mimetypes
from email.message import EmailMessage
from pathlib import Path

from ._attachments import AttachmentPolicy
from .exceptions import PolicyError

SIMPLE_UPLOAD_LIMIT = 5 * 1024 * 1024     # above this Google wants a resumable upload
MESSAGE_LIMIT = 25 * 1024 * 1024          # Gmail's total message ceiling

#: See the module docstring: attachment bytes pay base64's 4/3 expansion twice, not once - once
#: as the MIME part's own Content-Transfer-Encoding, once more for Gmail's `raw` field.
_BASE64_EXPANSION = (4 / 3) ** 2

#: Deliberately over- rather than under-estimated: MIME boundary lines and each attachment's own
#: `Content-Type` / `Content-Disposition` / `filename*` headers cost real bytes this estimate has
#: to account for sight unseen. Rounding up here can only make the early refusal fire a little
#: sooner than strictly necessary, never later - the safe direction for a check whose entire job
#: is to run before the read it is protecting.
_FIXED_OVERHEAD_BYTES = 2048
_PER_ATTACHMENT_OVERHEAD_BYTES = 1024


def _utf8_len(*parts: str | None) -> int:
    return sum(len((part or "").encode("utf-8")) for part in parts)


def _estimate_encoded_size(*, header_bytes: int, attachment_sizes: list[int]) -> int:
    """The projected encoded size, from stat()'d attachment sizes and header/body byte counts
    alone - never from an attachment's content. This is the number that has to be right, or
    close enough to right, BEFORE `build` opens a single attachment file.
    """
    raw_estimate = (
        header_bytes
        + _FIXED_OVERHEAD_BYTES
        + len(attachment_sizes) * _PER_ATTACHMENT_OVERHEAD_BYTES
        + sum(attachment_sizes)
    )
    return math.ceil(raw_estimate * _BASE64_EXPANSION)


def _set_header(msg: EmailMessage, name: str, value: str) -> None:
    """Assign one header, translating the `email` package's own line-break refusal into this
    project's exception hierarchy.

    Header injection: a `Subject` or recipient carrying an embedded `\\r\\n` could otherwise
    smuggle a second header line past this function (`"evil\\r\\nBcc: attacker@x"`) - the
    classic email-injection bug. `EmailMessage` (this class's default policy is
    `email.policy.default`, not legacy `compat32`) already raises `ValueError` from
    `__setitem__` for any header value containing a bare `\\r` or `\\n`, checked against the
    JOINED value - a `\\r\\n` embedded in any ONE recipient of a comma-joined `To:` list still
    trips it, confirmed directly against this Python version rather than assumed from the docs.
    That refusal is real, but it is an accident of the `email` package's own hardening unless
    something in THIS module asserts and tests it - so it is caught here and re-raised as a
    `PolicyError` (this project's own hierarchy, never a bare stdlib `ValueError` escaping to a
    caller) and pinned by `test_a_crlf_in_the_subject_is_refused_not_injected` and
    `test_a_crlf_in_a_recipient_is_refused_not_injected`.
    """
    try:
        msg[name] = value
    except ValueError as exc:
        raise PolicyError(
            f"{name} contains a line break, which would inject additional header lines into "
            f"this message; refused rather than silently accepted") from exc


def _reject_blank(addrs: list[str], label: str) -> None:
    if any(not addr or not addr.strip() for addr in addrs):
        raise PolicyError(f"{label} must not contain a blank recipient")


def _require_recipients(to: list[str]) -> None:
    # An empty `To:` header is not "send to nobody", it is silent: the header can be omitted
    # or emitted blank and either way nothing describes what happened. Refused instead of built.
    if not to:
        raise PolicyError("to must contain at least one recipient")
    _reject_blank(to, "to")


def _unique_filename(seen: dict[str, int], filename: str) -> str:
    """Two different attachment PATHS can share a basename (`report/q1.pdf`, `archive/q1.pdf`
    both named `q1.pdf`). MIME does not forbid two parts with the same filename, but Gmail then
    shows the recipient two attachments both called `q1.pdf`, indistinguishable until opened -
    and any downstream tooling that saves attachments by filename would overwrite one with the
    other. Disambiguated here by suffixing every repeat occurrence, so both names survive.
    """
    count = seen.get(filename, 0)
    seen[filename] = count + 1
    if count == 0:
        return filename
    stem, sep, ext = filename.rpartition(".")
    suffix = f"-{count + 1}"
    return f"{stem}{suffix}.{ext}" if sep else f"{filename}{suffix}"


def upload_strategy(encoded: str) -> str:
    """Which HTTP shape a caller should POST this already-built message with: `"simple"` below
    `SIMPLE_UPLOAD_LIMIT` (an ordinary JSON `{"raw": ...}` body against `messages.send` /
    `drafts.create`), `"resumable"` at or above it (Google requires the `/upload/...` endpoint
    with a resumable upload session for a JSON body this large - a plain POST that size risks
    being rejected or truncated by an intermediate proxy before it ever reaches Gmail's own
    ceiling). `_mime` only assembles the message; it makes no HTTP calls, so this is exposed as
    a pure function of the already-encoded string rather than acted on here. `SIMPLE_UPLOAD_LIMIT`
    is otherwise unused in this task - Task 7's `ApiBackend` is the intended caller, choosing
    between a plain `messages.send` call and a resumable upload session before it POSTs.
    """
    size = len(encoded.encode("ascii"))
    return "simple" if size < SIMPLE_UPLOAD_LIMIT else "resumable"


def build(
    to: list[str],
    subject: str,
    body: str,
    *,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    from_addr: str | None = None,
    attachments: list[str] | None = None,
    attach_policy: AttachmentPolicy | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    html_body: str | None = None,
) -> str:
    _require_recipients(to)
    if cc:
        _reject_blank(cc, "cc")
    if bcc:
        _reject_blank(bcc, "bcc")

    msg = EmailMessage()
    _set_header(msg, "To", ", ".join(to))
    if cc:
        _set_header(msg, "Cc", ", ".join(cc))
    if bcc:
        # Gmail strips this before delivery. It must be present as a header here regardless -
        # if it is absent, the blind recipient is simply not a recipient at all.
        _set_header(msg, "Bcc", ", ".join(bcc))
    if from_addr:
        _set_header(msg, "From", from_addr)
    _set_header(msg, "Subject", subject)
    if in_reply_to:
        _set_header(msg, "In-Reply-To", in_reply_to)
    if references:
        _set_header(msg, "References", references)
    # Non-ASCII subjects/display names: `email.policy.default` RFC-2047-encodes header values
    # and RFC-2231-encodes attachment filenames automatically - verified directly against this
    # Python version (see task-6-report.md) rather than assumed, and pinned by
    # `test_non_ascii_subject_round_trips_without_mojibake` /
    # `test_non_ascii_attachment_filename_round_trips` below.
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    if attachments:
        resolved: list[Path] = []
        for path in attachments:
            if attach_policy is None:
                raise PolicyError(
                    "attachments are disabled: no attachment directory is configured. Set "
                    "CSA_GGC_ATTACH_DIR to a directory this server may read files from.")
            # `resolve()`, not `read()`: this needs the Path to `stat()` before anything is
            # read, and the SAME resolved Path is read from below - never a path re-derived
            # from the caller's original string, which would sidestep the containment check.
            resolved.append(attach_policy.resolve(path))

        sizes = [p.stat().st_size for p in resolved]  # no file content read yet
        header_bytes = _utf8_len(
            subject, body, html_body, ", ".join(to), ", ".join(cc or []),
            ", ".join(bcc or []), from_addr, in_reply_to, references,
        )
        estimated = _estimate_encoded_size(header_bytes=header_bytes, attachment_sizes=sizes)
        if estimated > MESSAGE_LIMIT:
            raise PolicyError(
                f"the attachment(s) total {sum(sizes) / 1024 / 1024:.1f} MB on disk; assembled "
                f"and base64-encoded (once per MIME part, again for Gmail's `raw` field) that "
                f"projects to about {estimated / 1024 / 1024:.1f} MB, over Gmail's "
                f"{MESSAGE_LIMIT // 1024 // 1024} MB message limit. Refused before reading the "
                f"attachment content. Send a link instead, or split the attachments across "
                f"messages.")

        seen: dict[str, int] = {}
        for p in resolved:
            content = p.read_bytes()  # first and only read of this attachment's content
            filename = _unique_filename(seen, p.name)
            guessed, _ = mimetypes.guess_type(filename)
            maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    raw = msg.as_bytes()
    encoded = base64.urlsafe_b64encode(raw)
    if len(encoded) > MESSAGE_LIMIT:
        # Belt-and-braces: the pre-read estimate above should already have refused anything
        # that would reach here. This catches an error IN that estimate, not a large read - the
        # message is already fully assembled in memory by this point either way.
        raise PolicyError(
            f"the assembled message is {len(encoded) / 1024 / 1024:.1f} MB once encoded, over "
            f"Gmail's {MESSAGE_LIMIT // 1024 // 1024} MB limit. base64 encoding (applied twice - "
            f"see the module docstring) costs roughly {_BASE64_EXPANSION:.2f}x, so the "
            f"underlying files total {len(raw) / 1024 / 1024:.1f} MB assembled. Send a link "
            f"instead, or split the attachments across messages.")
    return encoded.decode()
