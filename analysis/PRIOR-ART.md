# Prior art: what already exists, and what it names things

Captured 2026-08-31. Three servers were observed **live** — connected to a running client,
tool schemas read off the wire — rather than read from documentation. Where the wire and the
documentation disagree, this file records both and says which is which.

Machine-readable: `official-mcp-tools.json`.

## The three

| | Google Gmail MCP | Google Calendar MCP | `@gongrzhe/server-gmail-autoauth-mcp` (community) |
|---|---|---|---|
| Hosting | Google-hosted remote | Google-hosted remote | local stdio |
| Endpoint | `gmailmcp.googleapis.com/mcp/v1` | `calendarmcp.googleapis.com/mcp/v1` | n/a |
| Status | Developer Preview Program | Developer Preview Program | released |
| Tools documented | 11 | 8 | — |
| **Tools actually shipped** | **29** | **9** | **30** |
| Self-hostable | no | no | yes |
| Credentials leave the machine | yes | yes | no |

The community server is included because it is the one that ships the families Google's own
server omits — filters, attachments, batch operations — and because its naming is a third,
incompatible vocabulary for the same API.

## Finding 1 — the documentation undercounts both official servers

Google's `configure-mcp-server` pages list 11 Gmail tools and 8 Calendar tools. The live
servers expose 29 and 9.

**18 Gmail tools ship undocumented**, and they are not a rounding error — they are the
destructive half of the server:

```
send_message  reply  forward              ← sends mail to third parties
trash_message  trash_thread               ← destructive
untrash_message  untrash_thread
mark_message_spam    mark_thread_spam     ← trains a classifier, moves mail out of view
unmark_message_spam  unmark_thread_spam
apply_sensitive_message_label             ← Trash/Spam behind a euphemism
apply_sensitive_thread_label
delete_label  update_label                ← delete_label is irreversible
update_message_labels
get_draft  update_draft
```

Calendar ships one undocumented tool, `search_events`.

The documented list is the read-and-draft subset. Anyone sizing the blast radius of these
servers from the public page will conclude they cannot send mail or destroy anything. They can.

## Finding 2 — the documented scopes cannot run the documented tools

**Calendar is the flat contradiction.** The page lists exactly three scopes, and all three are
read-only:

```
calendar.calendarlist.readonly
calendar.events.freebusy
calendar.events.readonly
```

The same page says the server can "Create, update, and delete events", and the live server
exposes `create_event`, `update_event`, `delete_event` and `respond_to_event`. No combination
of three `.readonly` and `.freebusy` scopes authorizes a write. Either the scope list is
incomplete or the tool list is — the documentation does not permit both to be true.

**Gmail understates by one scope.** The page lists `gmail.readonly` and `gmail.compose`.
Those cover reading, drafting and sending. They do **not** cover label mutation, trash, or
spam — `users.messages.modify` and `users.threads.modify` require `gmail.modify`, which the
page never mentions, and which is one step below full-mailbox `https://mail.google.com/`.

*Consequence for us: treat Google's published scope lists as marketing, and derive the scope
each operation actually needs from the `scopes` array in the Discovery document. That array is
generated from the same source as the server, so it cannot drift from reality the way prose can.*

## Finding 3 — the official servers reference tools that are not there

Three references in the Gmail server's own tool descriptions and schemas point at RPCs absent
from the exposed tool list:

| Referenced | Named in | Present |
|---|---|---|
| `batch_apply_sensitive_message_labels` | `apply_sensitive_message_label` description | no |
| `batch_apply_sensitive_thread_labels` | `apply_sensitive_thread_label` description | no |
| `GetMessageAttachment` | `Attachment.id` schema description | no |

The third is the consequential one: `get_message` returns `attachment_ids` and tells the model
they "can be retrieved in a separate `GetMessageAttachment` request", but no such tool is
exposed. **The official Gmail MCP server cannot download an attachment.** It can only tell you
one exists.

## Finding 4 — three servers, three vocabularies, same API

The same underlying operation has three names. Any alignment decision has to pick one and say so.

| Operation | Google Gmail MCP | Community server | Gmail REST |
|---|---|---|---|
| read one message | `get_message` | `read_email` | `users.messages.get` |
| find mail | `search_threads` | `search_emails` | `users.threads.list` |
| read a thread | `get_thread` | `get_thread` | `users.threads.get` |
| compose a draft | `create_draft` | `draft_email` | `users.drafts.create` |
| send | `send_message` | `send_email` | `users.messages.send` |
| add labels | `label_message` | `modify_email` | `users.messages.modify` |
| list labels | `list_labels` | `list_email_labels` | `users.labels.list` |
| to trash | `trash_message` / `apply_sensitive_message_label` | `delete_email` | `users.messages.trash` |

Two things are worth stealing and one is worth refusing.

**Steal: the thread/message split.** Google names the axis in every tool — `label_thread` vs
`label_message`, `trash_thread` vs `trash_message` — and the descriptions explain when each is
right ("Trashing at the thread level ensures all current messages in the thread are moved to
Trash"). The community server collapses the axis and the caller cannot express the difference.

**Steal: `view` / `messageFormat` projections.** `THREAD_VIEW_METADATA_ONLY`, `PLAIN_TEXT`,
`MINIMAL`, `METADATA_ONLY` let the caller ask for less. Google's own description gives the
reason — "We recommend using `PLAIN_TEXT` to prevent context exhaustion" — and it doubles as a
data-minimisation control: `METADATA_ONLY` returns envelope without body.

**Refuse: `delete_email` meaning trash.** The community server names a reversible move to Trash
`delete_email`, and names permanent deletion nothing at all. A model reading that tool list will
believe it has destroyed something it has not, and will not find the tool that actually does.
Google's `trash_message` / `apply_sensitive_message_label` naming is imperfect —
"sensitive label" is a euphemism for Trash and Spam — but at least `trash` says trash.

## Finding 5 — what nobody exposes

Against 117 REST methods, the official servers touch four Gmail families of fifteen, and three
Calendar families of eight. See `API-SURFACE.md` for the full gap. The short version:

- **Gmail settings — 45 of Gmail's 79 methods — is exposed by nobody.** Filters, forwarding
  addresses, delegates, send-as identities, S/MIME and client-side-encryption keys.
- **Calendar ACL — 7 methods — is exposed by nobody.** Who can read or write a calendar.
- The community server is alone in exposing filters (`list_filters`, `get_filter`,
  `create_filter`, `delete_filter`), attachments (`download_attachment`) and batch operations.

## Still to do

- Survey the wider third-party field (npm/PyPI/GitHub) the way `csa-zendesk` did; only the one
  community server was observed live here, and it was observed because it happened to be
  connected, not because it was selected as representative.
- Re-check both official servers when they leave Developer Preview. A preview tool list is not
  a commitment, and the gap between the page and the wire may close in either direction.
