"""Reads a Gmail message: walks its MIME tree, finds the body, converts an HTML body to
Markdown through `_markdown.to_markdown`, and lists the parts a person actually attached.

DEC-021 (csa-zendesk, adopted here): disclose what you changed. `ParsedMessage.transformations`
names every change made to the body - HTML converted to Markdown, hidden HTML elements
surfaced, each codepoint-stripping rule that fired - as a plain-language sentence a person can
read directly, never a bare rule id standing on its own.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from . import _markdown
from .backend import Backend
from .exceptions import NotFoundError

#: Preference order when a message offers more than one body candidate (Gmail routinely nests
#: `multipart/related` inside `multipart/alternative` inside `multipart/mixed`, with a
#: `text/plain` and a `text/html` sibling at the `alternative` level). `text/plain` wins WHEN IT
#: HAS CONTENT - see `_pick_body` below for why "present" and "has content" are not the same
#: test, and why blurring them yields an empty body on exactly the messages a plain-preferring
#: reader most needs to get right.
_BODY_PREFERENCE = ("text/plain", "text/html")

#: No legitimate Gmail MIME tree nests anywhere near this deep - `mixed > alternative > related
#: > text` is four levels, and even a message wrapping every forwarded reply in its own
#: multipart rarely reaches double digits. Bounding `_walk`'s recursion turns a malformed or
#: adversarial message's unbounded nesting from a `RecursionError` (an opaque crash that takes
#: the whole read down with it) into "the parts past this depth are silently not walked" - a
#: named, bounded failure instead of a stack-exhaustion crash. `parts` here is nested dicts, not
#: references, so a true cycle is not possible; the risk this guards against is depth, not a
#: cycle.
_MAX_MIME_DEPTH = 50


@dataclass
class AttachmentRef:
    """One real attachment: a part with both a filename and an attachment id.

    Deliberately NOT every part with an `attachmentId` - see `_is_attachment`'s docstring for
    why an inline `cid:` image (attachment id present, filename empty) is excluded.
    """

    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int


@dataclass
class ParsedMessage:
    """The result of `Mail.read_message` - a Gmail message resource reduced to what a caller
    (human or model) needs: who it is from and about, its body as Markdown, its real
    attachments, and a disclosure of every change made getting there (DEC-021).
    """

    id: str
    thread_id: str
    subject: str
    sender: str
    to: str
    cc: str
    date: str
    body_markdown: str
    body_source: str  # "text/plain" | "text/html" | "none"
    attachments: list[AttachmentRef]
    label_ids: list[str]
    transformations: list[str] = field(default_factory=list)


def _walk(part: dict[str, Any], *, depth: int = 0,
         truncated: list[bool] | None = None) -> Iterator[dict[str, Any]]:
    """Depth-first over the MIME tree. Gmail nests `multipart/related` inside
    `multipart/alternative` inside `multipart/mixed` routinely, and a one-level scan misses
    the body entirely on exactly those messages. Bounded by `_MAX_MIME_DEPTH` - see its
    docstring for why.

    Fix round 1 (CINO 2026-09-26): hitting the bound with unwalked children left behind used to
    be silent - `read_message` could not tell "this message genuinely has no body part" from
    "the body is past depth 50 and was never inspected", and both produced the identical
    `body_source == "none"`. That is exactly the trap the brief named. `truncated` is an
    out-parameter (a shared mutable list, appended to but never read here) rather than a return
    value, because `_walk` is a generator - its "return value" is only ever visible after full
    iteration, and the caller needs to know truncation happened as it drains the walk, not
    reconstruct it from a second pass.
    """
    yield part
    children = part.get("parts") or ()
    if depth >= _MAX_MIME_DEPTH:
        if children and truncated is not None:
            truncated.append(True)
        return
    for child in children:
        yield from _walk(child, depth=depth + 1, truncated=truncated)


def _is_attachment(part: dict[str, Any]) -> bool:
    """A filename AND an attachmentId. An inline cid: image has an attachmentId and an empty
    filename - it is part of the body, not something a person attached, and listing it makes
    every HTML newsletter look like it carried three files.
    """
    return bool(part.get("filename")) and bool((part.get("body") or {}).get("attachmentId"))


def _decode(body: dict[str, Any]) -> str:
    data = body.get("data")
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)  # Gmail strips base64url padding
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _decode_body_part(body: dict[str, Any], *, label: str) -> tuple[str, list[str]]:
    """Decode one body part's `data` via `_decode`, and disclose (never silently swallow or
    silently substitute) either of the two ways that can go wrong.

    Fix round 1 (CINO 2026-09-26), two findings folded into one wrapper because they are the
    same failure mode - the body gets altered and the caller cannot tell - at two different
    points in `_decode`:

    FIX 2 - malformed base64. `data` of a length that cannot be valid base64 (Gmail-stripped
    padding restores MOST truncated data, but a length that is 1 more than a multiple of 4 is
    unrecoverable - no amount of `=` padding fixes it) raises `binascii.Error` (a `ValueError`
    subclass) OUT OF `_decode`, uncaught, past this module's boundary - and at the MCP layer an
    untranslated exception becomes an opaque `UnexpectedToolError` whose text this project's own
    SDK suppresses, so a user would see nothing but "error executing tool". Caught here instead,
    and NOT re-raised: one corrupt part should not make an otherwise-readable message unreadable
    (a `multipart/alternative` with a mangled `text/plain` and a perfectly good `text/html`
    sibling should still show the html). The caller loses only this one candidate - `label`
    names which message and which part, so the loss is visible rather than merely "no body was
    found".

    FIX 3 - invalid UTF-8. `errors="replace"` is the right behaviour for `_decode` to keep -
    refusing to show a body over two bad bytes helps nobody - but substituting with NO
    disclosure means `transformations` positively asserts nothing happened when something did,
    which is worse than an empty `transformations` list: an empty list is a true "nothing to
    report", a non-empty body with silently substituted bytes is a false one. Counted here
    (`str.count("\\ufffd")`, the replacement character `errors="replace"` inserts per invalid
    byte sequence) rather than merely flagged, the same reasoning csa-zendesk's
    `_untrusted.py` gives for counting neutralised characters instead of a bare flag: a count is
    what lets a reader judge whether two bytes were mangled or the whole body was.
    """
    try:
        text = _decode(body)
    except (binascii.Error, ValueError) as exc:
        return "", [f"{label} could not be decoded and was treated as empty: {exc}"]
    substituted = text.count("�")
    if substituted:
        return text, [f"{substituted} byte(s) in {label} were not valid UTF-8 and were "
                      f"replaced with U+FFFD"]
    return text, []


def _header(headers: list[dict[str, Any]] | None, name: str) -> str:
    for h in headers or ():
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _pick_body(candidates: dict[str, str]) -> tuple[str, str]:
    """Choose which decoded body wins, and report which MIME type it came from.

    `text/plain` beats `text/html` only when the plain part actually HAS content.
    `multipart/alternative` legitimately carries a `text/plain` part that is empty - a
    compatibility stub for clients that do not render HTML - with the real message only in the
    `text/html` sibling. A preference test on presence alone ("is there a text/plain part at
    all") picks that empty stub and returns nothing, on precisely the messages this preference
    order exists to get right. So the test here is on CONTENT, not presence: `candidates` only
    ever holds a MIME type that decoded to a non-empty string (see the caller), so simply
    walking `_BODY_PREFERENCE` in order already skips an empty-but-present plain part in favour
    of a non-empty html one.
    """
    for mime_type in _BODY_PREFERENCE:
        if mime_type in candidates:
            return candidates[mime_type], mime_type
    return "", "none"


class Mail:
    """Reads Gmail messages against a `Backend`."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    def read_message(self, message_id: str) -> ParsedMessage:
        # `FakeBackend.get_message` already raises `NotFoundError` for an unknown id, and
        # nothing here indexes `messages` directly - but `Backend` is a structural Protocol, not
        # a promise every implementation keeps the same way, so a bare `KeyError` from some
        # other backend is translated here rather than assumed away. Belt-and-braces, not
        # dead code: a caller of this library should be able to catch one exception hierarchy
        # (`exceptions.CsaGoogleError` and friends) and never see a raw `KeyError` from any
        # `Backend` implementation.
        try:
            message = self._backend.get_message(message_id=message_id)
        except KeyError as exc:
            raise NotFoundError(message_id) from exc
        payload = message.get("payload") or {}
        headers = payload.get("headers")

        body_candidates: dict[str, str] = {}
        attachments: list[AttachmentRef] = []
        decode_notes: list[str] = []
        truncated: list[bool] = []
        for part in _walk(payload, truncated=truncated):
            if _is_attachment(part):
                body = part.get("body") or {}
                attachments.append(AttachmentRef(
                    attachment_id=body.get("attachmentId", ""),
                    filename=part.get("filename", ""),
                    mime_type=part.get("mimeType", ""),
                    size_bytes=int(body.get("size", 0) or 0),
                ))
                continue
            mime_type = part.get("mimeType", "")
            if mime_type in _BODY_PREFERENCE and mime_type not in body_candidates:
                text, notes = _decode_body_part(
                    part.get("body") or {},
                    label=f"the {mime_type} part of message {message_id!r}",
                )
                decode_notes.extend(notes)
                if text:  # empty content loses to a non-empty sibling - see `_pick_body`
                    body_candidates[mime_type] = text

        raw_body, body_source = _pick_body(body_candidates)
        body_markdown, render_notes = self._render_body(raw_body, body_source)
        # Structural notes (truncation) first, then per-part decode notes, then body-render
        # notes - roughly the order a reader would want them: "here is what I could not even
        # look at", then "here is what went wrong decoding what I did look at", then "here is
        # what I changed converting what decoded fine".
        transformations: list[str] = []
        if truncated:
            transformations.append(
                f"this message's MIME tree is nested past the {_MAX_MIME_DEPTH}-level walk "
                "limit; parts beyond that depth were not inspected and may hold additional "
                "body or attachment content"
            )
        transformations.extend(decode_notes)
        transformations.extend(render_notes)

        return ParsedMessage(
            id=message.get("id", message_id),
            thread_id=message.get("threadId", ""),
            subject=_header(headers, "Subject"),
            sender=_header(headers, "From"),
            to=_header(headers, "To"),
            cc=_header(headers, "Cc"),
            date=_header(headers, "Date"),
            body_markdown=body_markdown,
            body_source=body_source,
            attachments=attachments,
            label_ids=list(message.get("labelIds") or []),
            transformations=transformations,
        )

    @staticmethod
    def _render_body(raw_body: str, body_source: str) -> tuple[str, list[str]]:
        """Render the chosen body to Markdown and disclose every change made along the way
        (DEC-021). Plain text is not exempt from disclosure: it still goes through
        `_markdown.strip_suspicious` for the same Trojan-Source / control-character reasons an
        html body does, it is just never HTML-converted.
        """
        transformations: list[str] = []
        if body_source == "text/html":
            markdown, hidden_texts, fired = _markdown.to_markdown(raw_body)
            transformations.append("body converted from text/html to Markdown")
            if hidden_texts:
                transformations.append(
                    f"{len(hidden_texts)} hidden element(s) surfaced from HTML the browser "
                    f"would have concealed from a reader: {'; '.join(hidden_texts)}"
                )
            transformations.extend(f"codepoint rule fired: {rule}" for rule in fired)
            return markdown, transformations
        if body_source == "text/plain":
            fired = _markdown.rules_fired(raw_body)
            transformations.extend(f"codepoint rule fired: {rule}" for rule in fired)
            return _markdown.strip_suspicious(raw_body), transformations
        return "", transformations
