# Prior art: what already exists, and what it names things

Captured 2026-08-31. Machine-readable: `observed-mcp-tools.json`.

## Read this before using any number below

**What was probed live: the claude.ai Gmail and Google Calendar CONNECTORS.**
**What was NOT probed: Google's own `gmailmcp.googleapis.com` and `calendarmcp.googleapis.com`.**

These are different products, and the sibling repo already proved it for Drive.
`csa-google-workspace/research/drive-mcp-servers-and-api-surface.md` read both surfaces and
found the claude.ai Drive connector publishes **11** tools while Google's `drivemcp.googleapis.com`
publishes **8** — "the same implementation, minus three", omitting `update_file`, `share_file`
and `trash_file`. The Drive connector observed in this session has exactly those 11.

So the working assumption for Gmail and Calendar is the same relationship — connector as a
superset of Google's server — but it is **assumed, not verified**. Reaching Google's own servers
requires Developer Preview Program enrollment, `gcloud services enable`, an OAuth client, and
connecting the remote endpoints. Until that happens, every statement here about *Google's*
tool list comes from Google's documentation, and only statements about the *connector* come
from a probe.

## The Workspace MCP family is eight servers, not two

From `developers.google.com/workspace/guides/configure-mcp-servers`, 2026-08-31:

| Product | Endpoint | Whose territory |
|---|---|---|
| Gmail | `https://gmailmcp.googleapis.com/mcp/v1` | **ours** |
| Calendar | `https://calendarmcp.googleapis.com/mcp/v1` | **ours** |
| Drive | `https://drivemcp.googleapis.com/mcp/v1` | `csa-google-workspace` |
| Docs | `https://docsmcp.googleapis.com/mcp/v1` | `csa-google-workspace` |
| Sheets | `https://sheetsmcp.googleapis.com/mcp/v1` | `csa-google-workspace` |
| Slides | `https://slidesmcp.googleapis.com/mcp/v1` | `csa-google-workspace` |
| Chat | `https://chatmcp.googleapis.com/mcp/v1` | neither, yet |
| People | `https://people.googleapis.com/mcp/v1` | neither, yet |

All eight are gated behind Developer Preview enrollment and per-service `gcloud services enable`.

## The three surfaces observed

| | claude.ai Gmail connector | claude.ai Calendar connector | `@gongrzhe/server-gmail-autoauth-mcp` |
|---|---|---|---|
| Probed live | yes | yes | yes |
| Tools | **29** | **9** | **30** |
| Hosting | remote, Anthropic-fronted | remote, Anthropic-fronted | local stdio |
| Server instructions | **none** | **none** | none |
| MCP resources | **none** | **none** | none |
| Google's page documents | 11 tools | 8 tools | n/a |

Both connectors ship **no server instructions and no MCP resources** — a bare tool list. Worth
noting because `csa-google-workspace`, `csa-skilljar` and `csa-mcp` all ship substantial
instructions, and `csa-google-workspace` additionally publishes `csa-gw://config` and
`csa-gw://help/configuration` as resources. That is a real differentiator and it is free.

## Finding 1 — the doc-vs-wire delta is real but does not mean what I first said

Google's pages list 11 Gmail tools and 8 Calendar tools. The connectors expose 29 and 9.

**That gap is not evidence that Google ships undocumented tools.** Given the Drive precedent, the
likelier reading is that Google's page accurately describes Google's server and the connector is
a superset. The 18 extra Gmail tools may belong to the connector alone.

What can be said without a probe of Google's server: **somebody ships these, and they are the
destructive half.**

```
send_message  reply  forward              ← sends mail to third parties
trash_message  trash_thread  untrash_*    ← destructive
mark_*_spam    unmark_*_spam              ← trains a classifier, moves mail out of view
apply_sensitive_message_label             ← Trash/Spam behind a euphemism
apply_sensitive_thread_label
delete_label  update_label                ← delete_label is irreversible
update_message_labels   get_draft  update_draft
```

Calendar's one extra is `search_events`.

*Open question, resolvable only by connecting Google's endpoints: does `gmailmcp.googleapis.com`
ship the destructive 18, or is the documented 11 the whole of it?* That question decides how much
of the alignment surface is actually Google's.

## Finding 2 — Google's own page contradicts itself, independent of which server you probe

This one needs no probe; it is internal to the documentation.

The Calendar page lists exactly three scopes, and all three are read-only:

```
calendar.calendarlist.readonly
calendar.events.freebusy
calendar.events.readonly
```

The same page says the server can "Create, update, and delete events". No combination of three
`.readonly` and `.freebusy` scopes authorizes a write. Either the scope list is incomplete or the
capability description is — the page does not permit both to be true.

The Gmail page lists `gmail.readonly` and `gmail.compose`, which cover reading, drafting and
sending but **not** label mutation, trash or spam. Those need `gmail.modify`, which the page
never mentions.

*Consequence: derive the scope each operation needs from the `scopes` array in the Discovery
document, never from prose. That array is generated from the same source as the API.*

## Finding 3 — the connector references tools it does not have

Three references in the Gmail connector's own descriptions and schemas point at RPCs absent from
its tool list:

| Referenced | Named in | Present |
|---|---|---|
| `batch_apply_sensitive_message_labels` | `apply_sensitive_message_label` description | no |
| `batch_apply_sensitive_thread_labels` | `apply_sensitive_thread_label` description | no |
| `GetMessageAttachment` | `Attachment.id` schema description | no |

The third is the consequential one: `get_message` returns `attachment_ids` and tells the model
they "can be retrieved in a separate `GetMessageAttachment` request", but no such tool exists.
**The connector cannot download an attachment.** It can only say one is there.

## Finding 4 — three surfaces, three vocabularies, same API

| Operation | claude.ai Gmail connector | Community server | Gmail REST |
|---|---|---|---|
| read one message | `get_message` | `read_email` | `users.messages.get` |
| find mail | `search_threads` | `search_emails` | `users.threads.list` |
| read a thread | `get_thread` | `get_thread` | `users.threads.get` |
| compose a draft | `create_draft` | `draft_email` | `users.drafts.create` |
| send | `send_message` | `send_email` | `users.messages.send` |
| add labels | `label_message` | `modify_email` | `users.messages.modify` |
| list labels | `list_labels` | `list_email_labels` | `users.labels.list` |
| to trash | `trash_message` / `apply_sensitive_message_label` | `delete_email` | `users.messages.trash` |

**Steal: the thread/message axis named in every tool.** `label_thread` vs `label_message`,
`trash_thread` vs `trash_message`, with descriptions explaining when each is right. The community
server collapses the axis and the caller cannot express the difference.

**Steal: the `view` / `messageFormat` projections.** `THREAD_VIEW_METADATA_ONLY`, `PLAIN_TEXT`,
`MINIMAL`, `METADATA_ONLY`. Framed as economy — "to prevent context exhaustion" — but
`METADATA_ONLY` returns envelope without body, which makes it a data-minimisation control.

**Refuse: `delete_email` meaning trash.** The community server names a reversible move to Trash
`delete_email` and names permanent deletion nothing at all. A model reading that list believes it
has destroyed something it has not, and cannot find the tool that would.

## Finding 5 — what nobody exposes

Against 117 REST methods, the connectors touch four Gmail families of fifteen and three Calendar
families of eight. Google's own servers reach the same or fewer. See `API-SURFACE.md`.

- **Gmail settings — 45 of 79 methods — exposed by nobody.** Filters, forwarding addresses,
  delegates, send-as, S/MIME, client-side encryption.
- **Calendar ACL — 7 methods — exposed by nobody.**
- The community server alone exposes filters, `download_attachment`, and batch operations.

## Still to do

1. **Capture Google's own Gmail and Calendar servers.** Everything marked "unverified" above
   depends on it, and Finding 1's open question cannot close without it.
2. Survey the wider third-party field the way `csa-zendesk` did. The one community server here was
   observed because it happened to be connected, not because it was chosen as representative.
3. Re-check when the servers leave Developer Preview.
