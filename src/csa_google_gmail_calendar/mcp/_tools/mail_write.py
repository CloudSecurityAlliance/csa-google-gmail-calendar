"""The Gmail organising/composing tier (spec §5): draft CRUD (`create_draft`, `update_draft`,
`delete_draft`), label/state changes (`modify_message_labels`, `modify_thread_labels`,
`archive_email`, `archive_thread`, `mark_read`, `mark_unread`, `create_label`), and disposal
(`trash_email`, `trash_thread`, `untrash_email`, `untrash_thread`, `mark_spam`, `unmark_spam`) -
16 tools, all gated `policy.MAIL_WRITE` except none (every one of these is a reversible write;
see `policy._GATES`'s own note on why `delete_draft` sits here rather than behind
`MAIL_DELETE` despite being irreversible).

ADR-001 naming, enforced by `tests/test_mcp_capabilities.py::test_no_tool_is_named_delete_email`:
`archive_email`, `trash_email`, `untrash_email` (never a bare `delete_email` - Google's own
permanent delete needs `https://mail.google.com/` and is gated `policy.MAIL_DELETE`, off by
default, and no tool in this task calls it at all).
"""
from __future__ import annotations

from mcp.server import MCPServer

from ... import _mime
from ... import policy as policy_mod
from ..._attachments import AttachmentPolicy
from ...backend import Backend
from ._base import DESTRUCTIVE, WRITE, tool


def register_mail_write_tools(app: MCPServer, backend: Backend, policy_obj: policy_mod.Policy,
                              attach_policy: AttachmentPolicy | None = None) -> None:
    # --- draft CRUD ---------------------------------------------------------------------

    if policy_obj.allows("create_draft"):
        @tool(app, annotations=WRITE)
        def create_draft(to: list[str], subject: str, body: str,
                         cc: list[str] | None = None, bcc: list[str] | None = None,
                         attachments: list[str] | None = None,
                         html_body: str | None = None) -> dict[str, object]:
            """Compose an unsent draft - the safe rehearsal for a send. Nothing goes on the
            wire until `send_draft` is called on the id this returns; review or edit it first
            with `get_draft`/`update_draft`.

            `attachments` are local file paths, resolved under the directory configured by
            `CSA_GGC_ATTACH_DIR` (refused, naming that variable, if it is not set) - there is
            no attachment UPLOAD endpoint in Gmail's API, so a file becomes part of the
            message's MIME body right here, before it is ever sent. Large attachments are
            refused before being read into memory if they would push the assembled message over
            Gmail's 25 MB limit.

            To send immediately instead of drafting first, use `send_message`."""
            raw = _mime.build(to, subject, body, cc=cc, bcc=bcc, attachments=attachments,
                              attach_policy=attach_policy, html_body=html_body)
            return backend.create_draft(raw=raw)

    if policy_obj.allows("update_draft"):
        @tool(app, annotations=WRITE)
        def update_draft(draft_id: str, to: list[str], subject: str, body: str,
                         cc: list[str] | None = None, bcc: list[str] | None = None,
                         attachments: list[str] | None = None,
                         html_body: str | None = None) -> dict[str, object]:
            """Replace an existing draft's content wholesale - every argument is the NEW
            complete message, not a delta on top of what was there (Gmail's own `drafts.update`
            works the same way: it replaces, it does not merge). Get the current content first
            with `get_draft` if you need to keep parts of it."""
            raw = _mime.build(to, subject, body, cc=cc, bcc=bcc, attachments=attachments,
                              attach_policy=attach_policy, html_body=html_body)
            return backend.update_draft(draft_id=draft_id, raw=raw)

    if policy_obj.allows("delete_draft"):
        @tool(app, annotations=DESTRUCTIVE)
        def delete_draft(draft_id: str) -> dict[str, object]:
            """Permanently DESTROY an unsent draft - Google's own words for `drafts.delete`
            are "immediately and permanently deletes... does not simply trash it," and this
            tool is honest about that rather than reusing softer language from elsewhere in
            this server. There is no undo and no `untrash_draft`. If you only want it out of
            the way, there is no reversible alternative for a draft specifically - trash/untrash
            exists for received messages and threads, not drafts."""
            return backend.delete_draft(draft_id=draft_id)

    # --- labels and state ----------------------------------------------------------------

    if policy_obj.allows("modify_message_labels"):
        @tool(app, annotations=WRITE)
        def modify_message_labels(message_id: str, add: list[str] | None = None,
                                  remove: list[str] | None = None) -> dict[str, object]:
            """Add and/or remove label IDs on one message - Gmail's one general-purpose
            mutation underneath `archive_email`, `mark_read`, `trash_email`, `mark_spam` and
            friends, all of which are exactly this call with specific label ids baked in.
            Prefer those named tools when they say what you mean; reach for this one directly
            only for a label this server has no dedicated tool for (a user-created label from
            `list_labels`, for instance). Gmail has no folders - "move to X" is always
            add/remove labels, never a move."""
            return backend.modify_message_labels(message_id=message_id, add=add, remove=remove)

    if policy_obj.allows("modify_thread_labels"):
        @tool(app, annotations=WRITE)
        def modify_thread_labels(thread_id: str, add: list[str] | None = None,
                                 remove: list[str] | None = None) -> dict[str, object]:
            """Same as `modify_message_labels`, applied to every message in a thread at once."""
            return backend.modify_thread_labels(thread_id=thread_id, add=add, remove=remove)

    if policy_obj.allows("archive_message"):
        @tool(app, annotations=WRITE)
        def archive_email(message_id: str) -> dict[str, object]:
            """Remove a message from the Inbox view (the `INBOX` label) without deleting or
            trashing it - it stays fully readable via `search_messages`/`get_thread` and any
            other label it carries. Reversible with `modify_message_labels(add=["INBOX"])`;
            this is not the same operation as `trash_email`, which marks it for eventual
            deletion instead."""
            return backend.archive_message(message_id=message_id)

    if policy_obj.allows("archive_thread"):
        @tool(app, annotations=WRITE)
        def archive_thread(thread_id: str) -> dict[str, object]:
            """`archive_email`, applied to every message in a thread at once."""
            return backend.archive_thread(thread_id=thread_id)

    if policy_obj.allows("mark_read"):
        @tool(app, annotations=WRITE)
        def mark_read(message_id: str) -> dict[str, object]:
            """Remove the `UNREAD` label from one message. Reverse with `mark_unread`."""
            return backend.mark_read(message_id=message_id)

    if policy_obj.allows("mark_unread"):
        @tool(app, annotations=WRITE)
        def mark_unread(message_id: str) -> dict[str, object]:
            """Add the `UNREAD` label back to one message. Reverse with `mark_read`."""
            return backend.mark_unread(message_id=message_id)

    if policy_obj.allows("create_label"):
        @tool(app, annotations=WRITE)
        def create_label(name: str) -> dict[str, object]:
            """Create a new user label with this name, returning its id for use in
            `modify_message_labels`/`modify_thread_labels`. There is no tool to rename or
            delete a label in this server's current tool set - use `list_labels` to check
            whether one with this name already exists before creating a duplicate."""
            return backend.create_label(name=name)

    # --- disposal --------------------------------------------------------------------------

    if policy_obj.allows("trash_message"):
        @tool(app, annotations=WRITE)
        def trash_email(message_id: str) -> dict[str, object]:
            """Move one message to Trash. This is NOT permanent deletion - Gmail keeps trashed
            mail for about 30 days before auto-purging it, and it can be restored any time
            before then with `untrash_email`. This server has no tool that deletes a received
            message permanently (that needs a broader, off-by-default capability this
            deployment may not have granted at all)."""
            return backend.trash_message(message_id=message_id)

    if policy_obj.allows("trash_thread"):
        @tool(app, annotations=WRITE)
        def trash_thread(thread_id: str) -> dict[str, object]:
            """`trash_email`, applied to every message in a thread at once. Reverse with
            `untrash_thread`, within Gmail's ~30-day Trash retention."""
            return backend.trash_thread(thread_id=thread_id)

    if policy_obj.allows("untrash_message"):
        @tool(app, annotations=WRITE)
        def untrash_email(message_id: str) -> dict[str, object]:
            """Restore one message out of Trash, back to wherever its other labels put it
            (typically the Inbox). Only works within Gmail's Trash retention window; past that
            the message is gone and this tool has nothing to restore."""
            return backend.untrash_message(message_id=message_id)

    if policy_obj.allows("untrash_thread"):
        @tool(app, annotations=WRITE)
        def untrash_thread(thread_id: str) -> dict[str, object]:
            """`untrash_email`, applied to every message in a thread at once."""
            return backend.untrash_thread(thread_id=thread_id)

    if policy_obj.allows("mark_spam"):
        @tool(app, annotations=WRITE)
        def mark_spam(message_id: str) -> dict[str, object]:
            """Move a message to Spam (also removing it from the Inbox) - this can influence
            Gmail's own spam classifier for future mail from the same sender, not just this one
            message. Reverse with `unmark_spam`."""
            return backend.mark_spam(message_id=message_id)

    if policy_obj.allows("unmark_spam"):
        @tool(app, annotations=WRITE)
        def unmark_spam(message_id: str) -> dict[str, object]:
            """Move a message out of Spam, back to the Inbox. Reverse of `mark_spam`."""
            return backend.unmark_spam(message_id=message_id)
