"""The Gmail sending tier (spec §5): `send_message`, `send_draft`, `reply`, `reply_all`,
`forward` - 5 tools, all gated `policy.MAIL_SEND`, all irreversible (mail put on the wire
cannot be recalled).

**Why `reply_all` is its own tool, not a `reply_all=True` argument on `reply`.** Getting the
recipient set wrong here is a public incident, not a bug report - the model choosing the wrong
BOOLEAN on a shared tool is a smaller, easier mistake than the model choosing the wrong TOOL
NAME, and the two tools' descriptions can each say exactly one thing rather than one
description branching on an argument (ADR-016's `respond_to_event` reasoning, applied to the
opposite conclusion for the opposite reason: there, one enum was clearer than three tools;
here, two tool NAMES are clearer than one boolean).

`reply`/`reply_all`/`forward` all read the ORIGINAL message via `backend.get_message` (never
`Mail.read_message` for header-only needs - that class exists to parse a body/attachments/
disclosure, which none of these three tools need for the original's HEADERS) to thread
`In-Reply-To`/`References` from its `Message-ID` and to compute recipients. `forward` is the
one exception: it also renders the original BODY (via `Mail`) to quote it, which is exactly
what that class is for.
"""
from __future__ import annotations

import email.utils
from typing import Any

from mcp.server import MCPServer

from ... import _mime
from ... import policy as policy_mod
from ..._attachments import AttachmentPolicy
from ...backend import Backend
from ...exceptions import PolicyError
from ...mail import Mail
from ._base import WRITE, tool
from ._schemas import ensure_fwd_prefix, ensure_re_prefix, header


def _headers(raw_message: dict[str, Any]) -> list[dict[str, Any]]:
    return (raw_message.get("payload") or {}).get("headers") or []


def register_mail_send_tools(app: MCPServer, backend: Backend, policy_obj: policy_mod.Policy,
                             attach_policy: AttachmentPolicy | None = None) -> None:
    if policy_obj.allows("send_message"):
        @tool(app, annotations=WRITE)
        def send_message(to: list[str], subject: str, body: str,
                         cc: list[str] | None = None, bcc: list[str] | None = None,
                         attachments: list[str] | None = None,
                         html_body: str | None = None) -> dict[str, object]:
            """Send a brand-new message immediately - irreversible, with no undo. Prefer
            `create_draft` first when you are not certain of the content or recipients; a
            draft can be reviewed and edited, this cannot be recalled once sent.

            `attachments` are local file paths under the directory configured by
            `CSA_GGC_ATTACH_DIR` (refused, naming that variable, if unset); refused before
            reading their content if they would push the message over Gmail's 25 MB limit.
            This is REPLYING to nothing and starts a new thread - use `reply`/`reply_all` to
            respond within an existing conversation instead."""
            raw = _mime.build(to, subject, body, cc=cc, bcc=bcc, attachments=attachments,
                              attach_policy=attach_policy, html_body=html_body)
            return backend.send_message(raw=raw)

    if policy_obj.allows("send_draft"):
        @tool(app, annotations=WRITE)
        def send_draft(draft_id: str) -> dict[str, object]:
            """Send an existing draft as-is - irreversible. Use `get_draft` first to confirm
            its content, or `update_draft` to change it before sending."""
            return backend.send_draft(draft_id=draft_id)

    if policy_obj.allows("reply_message"):
        @tool(app, annotations=WRITE)
        def reply(message_id: str, body: str,
                 attachments: list[str] | None = None) -> dict[str, object]:
            """Reply to the ORIGINAL SENDER ONLY - never the other recipients of the original
            message. If you mean to keep everyone who was on the original (its To and Cc, minus
            yourself) on the reply, use `reply_all` instead; choosing the wrong one of these
            two tools is exactly the mistake that makes a private reply public or drops people
            who expected to stay in the loop.

            Threads correctly: the subject gets exactly one `Re:` prefix (never doubled if the
            original already had one), and `In-Reply-To`/`References` are set from the
            original's `Message-ID` so mail clients (including Gmail's own) group this with the
            conversation instead of starting a new one. Sent within the same thread as the
            original - irreversible once sent."""
            raw_message = backend.get_message(message_id=message_id)
            headers = _headers(raw_message)
            thread_id = raw_message.get("threadId", message_id)
            from_addr = email.utils.parseaddr(header(headers, "From"))[1]
            if not from_addr:
                raise PolicyError(
                    f"message {message_id!r} has no readable From address to reply to")
            subject = ensure_re_prefix(header(headers, "Subject"))
            message_id_header = header(headers, "Message-ID") or None
            raw = _mime.build([from_addr], subject, body, attachments=attachments,
                              attach_policy=attach_policy, in_reply_to=message_id_header,
                              references=message_id_header)
            return backend.reply_message(raw=raw, thread_id=thread_id)

    if policy_obj.allows("reply_all_message"):
        @tool(app, annotations=WRITE)
        def reply_all(message_id: str, body: str,
                     attachments: list[str] | None = None) -> dict[str, object]:
            """Reply to the original sender AND every other original recipient - the set is
            exactly `{From} ∪ {To} ∪ {Cc}`, minus your OWN address (read from `get_profile`),
            deduplicated case-insensitively. You are never added as a recipient of your own
            reply, even if you were on the original To/Cc.

            Use `reply` instead when you mean to answer only the original sender - reply_all
            is the tool for keeping a group conversation intact, and using it when a private
            reply was intended is a real, public mistake, not merely an inconvenience to the
            wrong people.

            Threading is identical to `reply` (one `Re:` prefix, `In-Reply-To`/`References`
            from the original `Message-ID`). Irreversible once sent."""
            raw_message = backend.get_message(message_id=message_id)
            headers = _headers(raw_message)
            thread_id = raw_message.get("threadId", message_id)
            profile = backend.get_profile()
            me = (profile.get("emailAddress") or "").strip().lower()
            from_addr = email.utils.parseaddr(header(headers, "From"))[1]
            to_candidates = [addr for _, addr in
                            email.utils.getaddresses([header(headers, "To")]) if addr]
            cc_candidates = [addr for _, addr in
                            email.utils.getaddresses([header(headers, "Cc")]) if addr]

            seen: set[str] = set()
            to_final: list[str] = []
            for addr in ([from_addr, *to_candidates]):
                key = addr.strip().lower()
                if not addr or key == me or key in seen:
                    continue
                seen.add(key)
                to_final.append(addr)

            cc_final: list[str] = []
            for addr in cc_candidates:
                key = addr.strip().lower()
                if not addr or key == me or key in seen:
                    continue
                seen.add(key)
                cc_final.append(addr)

            if not to_final:
                raise PolicyError(
                    "reply_all has nobody left to send to once your own address "
                    f"({me or 'unknown'}) is excluded from the original message's "
                    "From/To/Cc - it would otherwise send a reply to nobody but yourself")

            subject = ensure_re_prefix(header(headers, "Subject"))
            message_id_header = header(headers, "Message-ID") or None
            raw = _mime.build(to_final, subject, body, cc=cc_final or None,
                              attachments=attachments, attach_policy=attach_policy,
                              in_reply_to=message_id_header, references=message_id_header)
            return backend.reply_all_message(raw=raw, thread_id=thread_id)

    if policy_obj.allows("forward_message"):
        @tool(app, annotations=WRITE)
        def forward(message_id: str, to: list[str], body: str | None = None,
                   cc: list[str] | None = None, bcc: list[str] | None = None,
                   attachments: list[str] | None = None) -> dict[str, object]:
            """Forward a message to NEW recipients, quoting the original sender, date, subject
            and body beneath your own optional `body` text. Unlike `reply`/`reply_all`, `to`
            is required here - forwarding has no "obvious" recipient to default to.

            The quoted content comes from the same parsing `get_message` uses (so it is
            already-converted Markdown, not raw HTML), and is untrusted - it was written by
            whoever sent the original message, not by you or the recipients of this forward.
            This does NOT carry the original's attachments forward automatically; pass their
            local paths again via `attachments` if you want them included in the new message
            (this server has no attachment-forwarding shortcut - see `get_attachment` to
            retrieve one first if you do not already have it on disk)."""
            parsed = Mail(backend).read_message(message_id)
            quoted = (
                "---------- Forwarded message ---------\n"
                f"From: {parsed.sender}\nDate: {parsed.date}\n"
                f"Subject: {parsed.subject}\nTo: {parsed.to}\n\n{parsed.body_markdown}"
            )
            full_body = f"{body}\n\n{quoted}" if body else quoted
            subject = ensure_fwd_prefix(parsed.subject)
            raw = _mime.build(to, subject, full_body, cc=cc, bcc=bcc, attachments=attachments,
                              attach_policy=attach_policy)
            return backend.forward_message(raw=raw)
