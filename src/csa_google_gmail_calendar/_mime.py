"""RFC 2822 assembly. There is no attachment upload endpoint (issue #8) - an attachment is a
part of a message body that rides along on drafts.create / drafts.update / messages.send, so
this is where "attachment support" actually lives.

**Three numbers, each with one job - and the whole first round of review confusion came from
two of them wearing the same name.**

- **The stat-estimate** (`_estimate_rfc822_size`) - computed from `Path.stat().st_size` alone,
  before any attachment is opened. Refuses EARLY, conservatively (a little too early is fine; a
  little too late defeats the whole point).
- **The RFC822 message size** (`len(msg.as_bytes())`) - the assembled message as the mail system
  sees it: headers plus MIME parts, each binary attachment base64'd once into its own part.
  **This is what Gmail's `MESSAGE_LIMIT` (25 MB) actually governs** - it is what the recipient's
  server measures, and it is measured here as the belt-and-braces final guard.
- **The API `raw` payload** (`base64.urlsafe_b64encode(msg.as_bytes())`, `build()`'s return
  value) - transport encoding for the Gmail API request itself: the whole RFC822 message,
  base64url-encoded a SECOND time because that is the literal contract of the `raw` field.
  Gmail's 25 MB user-facing limit has nothing to say about this number - `SIMPLE_UPLOAD_LIMIT`
  (5 MB) does (`upload_strategy`, below).

The first version of this module (and the brief it followed) checked the API `raw` payload
against `MESSAGE_LIMIT` - comparing a doubly-encoded quantity against a limit that governs the
singly-encoded one. Measured directly against this build:

```
  1 MB file -> RFC822   1.35 MB (x1.351)   API raw   1.80 MB (x1.802)
 10 MB file -> RFC822  13.51 MB (x1.351)   API raw  18.01 MB (x1.801)
```

Checking RFC822 against 25 MB admits files up to ~18.2 MB, which is correct. Checking API raw
against 25 MB instead caps them at ~13.7 MB - silently rejecting a quarter of the range Gmail
would actually accept, with a refusal message that reads as nonsense to whoever gets it ("your
15 MB file is over the 25 MB limit"). Fixed by measuring `msg.as_bytes()`, not its base64url
encoding, against `MESSAGE_LIMIT` - see `build`'s final check.

**The ceiling is still checked BEFORE any attachment is read, not only after assembly.** Reading
every attachment into memory before deciding it is too large is the harm this module exists to
prevent - a 10 GB file pointed at by an allowlisted path would be read in full and only then
rejected. So every attachment is `stat()`'d first (`Path.stat().st_size` reads no file content)
and the RFC822 size is estimated from those numbers alone, refusing before a single
`read_bytes()` call. The post-assembly measurement stays too, as a cheap final guard against an
error in the estimate - it is not the one this module depends on to avoid the read.
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
MESSAGE_LIMIT = 25 * 1024 * 1024          # Gmail's total message ceiling - governs RFC822 size

# Applied ONCE, not twice: a binary attachment's Content-Transfer-Encoding: base64 is the only
# base64 pass an RFC822 message pays (the SECOND pass - base64url over the whole message - is
# transport encoding for the API `raw` field, a different quantity entirely; see `upload_strategy`
# and the module docstring). Plain 4/3 would be 1.333x; measured against this build it is
# actually ~1.351x (see the table above and the reproduction in task-6-report.md) - MIME
# line-wraps base64 text every 76 characters with a trailing newline, adding ~1/77 more on top
# of the raw 4/3. Rounded up to 1.36 here, deliberately on the over- rather than under-estimating
# side, so the pre-read ESTIMATE stays conservative relative to what `msg.as_bytes()` will
# actually measure once assembled.
_RFC822_EXPANSION_ESTIMATE = 1.36

#: Deliberately over- rather than under-estimated: MIME boundary lines and each attachment's own
#: `Content-Type` / `Content-Disposition` / `filename*` headers cost real bytes this estimate has
#: to account for sight unseen. These are literal RFC822 bytes, not base64'd content, so they are
#: added AFTER the expansion factor is applied to the attachment bytes alone, not before.
#: Rounding up here can only make the early refusal fire a little sooner than strictly
#: necessary, never later - the safe direction for a check whose entire job is to run before the
#: read it is protecting.
_FIXED_OVERHEAD_BYTES = 2048
_PER_ATTACHMENT_OVERHEAD_BYTES = 1024


def _utf8_len(*parts: str | None) -> int:
    return sum(len((part or "").encode("utf-8")) for part in parts)


def _estimate_rfc822_size(*, header_bytes: int, attachment_sizes: list[int]) -> int:
    """The projected RFC822 message size - the quantity `MESSAGE_LIMIT` governs - from
    stat()'d attachment sizes and header/body byte counts alone, never from an attachment's
    content. This is the number that has to be right, or safely over rather than under, BEFORE
    `build` opens a single attachment file.
    """
    encoded_attachments = math.ceil(sum(attachment_sizes) * _RFC822_EXPANSION_ESTIMATE)
    overhead = _FIXED_OVERHEAD_BYTES + len(attachment_sizes) * _PER_ATTACHMENT_OVERHEAD_BYTES
    return header_bytes + overhead + encoded_attachments


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
    """Which HTTP shape a caller should POST this already-built message with - a ROUTING
    decision, not a permission one: `"simple"` below `SIMPLE_UPLOAD_LIMIT` (an ordinary JSON
    `{"raw": ...}` body against `messages.send` / `drafts.create`), `"resumable"` at or above it
    (Google wants the `/upload/...` endpoint with a resumable upload session for a JSON body
    this large - a plain POST that size risks being rejected or truncated by an intermediate
    proxy before it ever reaches Gmail's own ceiling). This answers "how do we send this", never
    "may we send this" - that question is `build`'s `MESSAGE_LIMIT` check, against a different,
    smaller quantity (the RFC822 size, not this one - see the module docstring).

    `encoded` is exactly `build`'s return value: the API `raw` payload, base64url over the whole
    RFC822 message. That is deliberately the quantity measured here (~1.80x the underlying file
    bytes, per the module docstring's table), not the RFC822 size `MESSAGE_LIMIT` governs -
    `SIMPLE_UPLOAD_LIMIT` is Google's limit on the HTTP request body, which IS the doubly-encoded
    payload. `_mime` only assembles the message and makes no HTTP calls, so this is exposed as a
    pure function rather than acted on here - Task 7's `ApiBackend` is the intended caller,
    choosing between a plain `messages.send` call and a resumable upload session before it POSTs.
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
        estimated = _estimate_rfc822_size(header_bytes=header_bytes, attachment_sizes=sizes)
        if estimated > MESSAGE_LIMIT:
            raise PolicyError(
                f"the attachment(s) total {sum(sizes) / 1024 / 1024:.1f} MB on disk; assembled "
                f"into the message (base64-encoded once, into its own MIME part) that projects "
                f"to about {estimated / 1024 / 1024:.1f} MB, over Gmail's "
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
    if len(raw) > MESSAGE_LIMIT:
        # Belt-and-braces: the pre-read estimate above should already have refused anything
        # that would reach here. This catches an error IN that estimate, not a large read - the
        # message is already fully assembled in memory by this point either way. Measured
        # against the RFC822 message itself (`msg.as_bytes()`), NOT its base64url `raw` encoding
        # - see the module docstring for why those are two different numbers.
        raise PolicyError(
            f"the assembled message is {len(raw) / 1024 / 1024:.1f} MB, over Gmail's "
            f"{MESSAGE_LIMIT // 1024 // 1024} MB message limit. Send a link instead, or split "
            f"the attachments across messages.")
    return base64.urlsafe_b64encode(raw).decode()
