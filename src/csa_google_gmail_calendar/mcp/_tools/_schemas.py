"""Structured-output shapes for the Gmail tools (`mail_read.py`/`mail_write.py`/
`mail_send.py`), and the small header/body helpers those tool modules share.

Plain `TypedDict`s built by hand, the same reasoning as
`../csa-google-workspace/src/csa_google_workspace/mcp/_schemas.py`: the wire shape is a
contract this project controls independently of `Backend`'s or `ParsedMessage`'s own internal
shape, and building it explicitly is what lets a field be renamed, added, or (as with
`transformations`, below) deliberately carried through without depending on either of those
internals staying byte-for-byte compatible with what a model reads.

**`transformations` is disclosure, not an implementation detail (DEC-021).** `ParsedMessage`
already computes it - every change `mail.py` made getting from a Gmail MIME tree to
`body_markdown` (HTML converted, hidden elements surfaced, codepoints stripped, bytes
replaced, the MIME tree truncated past `_MAX_MIME_DEPTH`). The one failure mode this module
exists to prevent is `message_out` building a response dict field-by-field and quietly leaving
that list out - so every call site is a single function, and this is the one place a future
field on `MessageOut` gets added or dropped, not four call sites each doing it slightly
differently.

**Body truncation, and why it defaults on.** A Gmail body can run to megabytes once HTML is
converted to Markdown (a long thread quoted inline, a newsletter's full boilerplate). Handing
the whole thing to a model by default spends its context on content nobody asked to read in
full - and unlike `search_messages`/`list_threads` truncation (which is Gmail's own pagination,
disclosed via `next_page_token`), a silently truncated BODY is the exact failure this plan has
hit before: the model sees a partial answer and has no signal it is one. `message_out` never
drops characters without saying so - `body_truncated` is `True` whenever the returned
`body_markdown` is shorter than the parsed body, and the tool's own docstring tells a caller
how to opt out (`full_body=True`).
"""
from __future__ import annotations

import base64
import email
import email.policy
import sys
from typing import Any

if sys.version_info >= (3, 12):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict

from ...mail import AttachmentRef, ParsedMessage

#: Chosen generously (roughly 800-1000 English words of Markdown) so an ordinary message needs
#: no `full_body=True` follow-up call at all - this bounds the OUTLIER (a multi-megabyte body
#: after HTML->Markdown conversion), not the common case. Not tied to any Gmail limit; purely
#: this server's own context-cost judgement call.
BODY_PREVIEW_CHARS = 4000


def header(headers: list[dict[str, Any]] | None, name: str) -> str:
    """Case-insensitive header lookup over a Gmail message resource's `payload.headers` list -
    the same shape `mail.py`'s own private `_header` reads, duplicated here (rather than
    imported) because that one is `mail.py`'s internal MIME-walking concern and this is a
    much shallower one: `reply`/`reply_all`/`forward` only ever need a handful of header
    VALUES off an already-fetched message, never the body/attachment walk `mail.py` owns.
    """
    for h in headers or ():
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def ensure_re_prefix(subject: str) -> str:
    """`Re: <subject>`, without doubling a prefix that is already there. Gmail's own web client
    treats `Re:`/`RE:`/`re:` interchangeably; matched case-insensitively here for the same
    reason - a client that already sent `RE: budget` should not come back as `Re: RE: budget`.
    """
    subject = subject or "(no subject)"
    if subject.strip().lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


def ensure_fwd_prefix(subject: str) -> str:
    subject = subject or "(no subject)"
    if subject.strip().lower().startswith("fwd:") or subject.strip().lower().startswith("fw:"):
        return subject
    return f"Fwd: {subject}"


class AttachmentRefOut(TypedDict):
    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int


def attachment_ref_out(a: AttachmentRef) -> AttachmentRefOut:
    return {"attachment_id": a.attachment_id, "filename": a.filename,
            "mime_type": a.mime_type, "size_bytes": a.size_bytes}


class MessageOut(TypedDict):
    id: str
    thread_id: str
    subject: str
    sender: str
    to: str
    cc: str
    date: str
    body_markdown: str
    body_source: str
    body_truncated: bool
    attachments: list[AttachmentRefOut]
    label_ids: list[str]
    transformations: list[str]


def message_out(parsed: ParsedMessage, *, full_body: bool) -> MessageOut:
    body = parsed.body_markdown
    truncated = False
    if not full_body and len(body) > BODY_PREVIEW_CHARS:
        body = body[:BODY_PREVIEW_CHARS]
        truncated = True
    return {
        "id": parsed.id, "thread_id": parsed.thread_id, "subject": parsed.subject,
        "sender": parsed.sender, "to": parsed.to, "cc": parsed.cc, "date": parsed.date,
        "body_markdown": body, "body_source": parsed.body_source,
        "body_truncated": truncated,
        "attachments": [attachment_ref_out(a) for a in parsed.attachments],
        "label_ids": list(parsed.label_ids),
        "transformations": list(parsed.transformations),
    }


class MessageSummaryOut(TypedDict):
    id: str
    thread_id: str


class SearchMessagesOut(TypedDict):
    messages: list[MessageSummaryOut]
    result_size_estimate: int
    next_page_token: str | None
    truncated: bool


def search_messages_out(raw: dict[str, Any]) -> SearchMessagesOut:
    token = raw.get("nextPageToken")
    return {
        "messages": [{"id": m["id"], "thread_id": m.get("threadId", m["id"])}
                     for m in raw.get("messages", [])],
        "result_size_estimate": raw.get("resultSizeEstimate", 0),
        "next_page_token": token,
        # An explicit boolean, not left for the model to infer from `next_page_token`'s mere
        # presence - "25 of 300" must not read as "here are the 25 results" (see this task's
        # brief on pagination surfaced to the model).
        "truncated": token is not None,
    }


class ThreadSummaryOut(TypedDict):
    id: str
    snippet: str


class ListThreadsOut(TypedDict):
    threads: list[ThreadSummaryOut]
    result_size_estimate: int
    next_page_token: str | None
    truncated: bool


def list_threads_out(raw: dict[str, Any]) -> ListThreadsOut:
    token = raw.get("nextPageToken")
    return {
        "threads": [{"id": t["id"], "snippet": t.get("snippet", "")}
                    for t in raw.get("threads", [])],
        "result_size_estimate": raw.get("resultSizeEstimate", 0),
        "next_page_token": token,
        "truncated": token is not None,
    }


class ThreadMessageSummaryOut(TypedDict):
    id: str
    sender: str
    subject: str
    date: str
    snippet: str
    label_ids: list[str]


class ThreadOut(TypedDict):
    id: str
    messages: list[ThreadMessageSummaryOut]


def thread_out(raw: dict[str, Any]) -> ThreadOut:
    """Message SUMMARIES, deliberately never full bodies - see the module docstring on body
    size. A caller that needs one message's full content calls `get_message` with its id."""
    out_messages: list[ThreadMessageSummaryOut] = []
    for m in raw.get("messages", []):
        headers = (m.get("payload") or {}).get("headers") or []
        out_messages.append({
            "id": m.get("id", ""),
            "sender": header(headers, "From"),
            "subject": header(headers, "Subject"),
            "date": header(headers, "Date"),
            "snippet": m.get("snippet", ""),
            "label_ids": list(m.get("labelIds") or []),
        })
    return {"id": raw.get("id", ""), "messages": out_messages}


def _decode_b64(data: str) -> bytes:
    """Gmail-style base64url, padding stripped by the API and restored here - the same
    recipe `mail.py._decode` uses for body parts, applied to raw bytes rather than decoded
    text (an attachment's bytes are not necessarily UTF-8 text at all)."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def decode_attachment_bytes(data: str) -> bytes:
    return _decode_b64(data)


class DraftPreviewOut(TypedDict):
    id: str
    subject: str
    to: str
    cc: str
    body_preview: str
    body_truncated: bool
    preview_available: bool


def draft_preview_out(draft: dict[str, Any]) -> DraftPreviewOut:
    """A draft's `message.raw` is caller-composed RFC822 text (base64url), not a Gmail message
    RESOURCE - `mail.py`'s `Mail`/`ParsedMessage` parse the latter (a JSON `payload` tree with
    `attachmentId`s), which a draft simply does not have. Parsing a raw MIME blob is a
    different, much smaller job - the standard-library `email` package does it directly, so
    this is not a second copy of `mail.py`'s MIME-walking logic, just a use of `email` the way
    `_mime.py` already does on the write side.

    Best-effort: a draft with no `raw` yet (freshly created empty), or one `email` cannot
    parse, still returns its id with `preview_available=False` rather than raising - a preview
    is a convenience, not something a caller depends on to identify which draft is which.
    """
    raw_b64 = ((draft.get("message") or {}).get("raw") or "")
    subject = to = cc = body_preview = ""
    truncated = False
    available = False
    if raw_b64:
        try:
            msg = email.message_from_bytes(_decode_b64(raw_b64), policy=email.policy.default)
            subject = msg.get("Subject", "") or ""
            to = msg.get("To", "") or ""
            cc = msg.get("Cc", "") or ""
            body_part = msg.get_body(preferencelist=("plain", "html"))
            if body_part is not None:
                content = body_part.get_content()
                body_preview = content if isinstance(content, str) else str(content)
            available = True
        except Exception:  # noqa: BLE001 - a malformed draft still returns its id; see docstring
            available = False
    if len(body_preview) > BODY_PREVIEW_CHARS:
        body_preview = body_preview[:BODY_PREVIEW_CHARS]
        truncated = True
    return {"id": draft.get("id", ""), "subject": subject, "to": to, "cc": cc,
           "body_preview": body_preview, "body_truncated": truncated,
           "preview_available": available}


class GetAttachmentOut(TypedDict):
    path: str
    filename: str
    size_bytes: int


class ProfileOut(TypedDict):
    email_address: str
    messages_total: int
    threads_total: int
    history_id: str


def profile_out(raw: dict[str, Any]) -> ProfileOut:
    """Gmail's own `users.getProfile` shape, renamed to this project's snake_case convention -
    same reasoning as every other `*_out` builder in this module: the wire shape is a contract
    this project controls, not a re-export of whatever key spelling the upstream API happens
    to use."""
    return {
        "email_address": raw.get("emailAddress", ""),
        "messages_total": raw.get("messagesTotal", 0),
        "threads_total": raw.get("threadsTotal", 0),
        "history_id": raw.get("historyId", ""),
    }


class WhoamiOut(TypedDict):
    email_address: str


def whoami_out(raw: dict[str, Any]) -> WhoamiOut:
    """The one field `whoami` exists to answer - see that tool's own docstring in
    `mail_read.py` for why it is a separate, narrower tool rather than telling every caller to
    read `get_profile()["email_address"]` themselves."""
    return {"email_address": raw.get("emailAddress", "")}


class HistoryOut(TypedDict):
    history: list[dict[str, Any]]
    history_id: str


def history_out(raw: dict[str, Any], *, start_history_id: str) -> HistoryOut:
    """Passed through mostly unchanged, the same choice `_list_events_out` (`calendar_read.py`)
    makes for `events`: a `History` record's own internal shape (`messagesAdded`,
    `labelsRemoved`, and so on, each nesting a partial message resource) is Gmail's, not this
    project's, to redesign, and duplicating that whole nested schema by hand buys nothing a
    passthrough does not already give a caller who reads Gmail's own `history.list`
    documentation. Only the two keys this project renames (`historyId` -> `history_id`) and a
    `start_history_id` used only as this function's own fallback (never as a business value the
    caller should read as their answer) are new."""
    return {
        "history": raw.get("history", []),
        "history_id": raw.get("historyId", start_history_id),
    }
