# Gmail and Calendar MCP servers: what exists, and what it tells the model

**Date:** 2026-09-01 · **Method:** Google's servers walked by unauthenticated JSON-RPC
`tools/list` against the public endpoints; the claude.ai connectors read from a live client
session on 2026-08-31; the community server read from a live local stdio connection. Raw
artifacts in `captures/`. Probe beats docs.

## What was probed

| Surface | Endpoint / package | Tools | Probed |
|---|---|---:|---|
| Google Gmail MCP | `https://gmailmcp.googleapis.com/mcp/v1` | **23** | 2026-09-01, unauthenticated |
| Google Calendar MCP | `https://calendarmcp.googleapis.com/mcp/v1` | **9** | 2026-09-01, unauthenticated |
| claude.ai Gmail connector | Anthropic-fronted | **29** | 2026-08-31, live session |
| claude.ai Calendar connector | Anthropic-fronted | **9** | 2026-08-31, live session |
| Community Gmail server | `@gongrzhe/server-gmail-autoauth-mcp` v1.1.11, **ArtyMcLabin fork** | **30** | local stdio |

Google's documentation claims 11 Gmail and 8 Calendar tools. Both undercount.

**`tools/list` needs no credential.** Both Google endpoints answer `initialize` and `tools/list`
over anonymous HTTP 200. Developer Preview enrollment, `gcloud services enable`, and an OAuth
client gate *invocation*, not *enumeration*. That is what makes this file cheap to regenerate.

## Google and Anthropic ship one implementation, not two

Established by diffing the captured schemas. **All 32 shared tools are schema-identical** — same
property names, same required fields, no divergence anywhere both expose a tool.

Supporting evidence:

- Every `inputSchema` carries a protobuf request-message description — `"Request message for
  CreateDraft RPC."` — on 23/23 Gmail and 9/9 Calendar.
- `x-google-enum-descriptions`, a Google API schema extension, appears throughout both (10
  occurrences Gmail, 30 Calendar).
- Model-facing guidance that looks like client tuning is in **Google's** text: `OneMCP`,
  `gduser1@workspacesamples.dev`, "to prevent context exhaustion", "Key indicators include",
  "Example user prompts are".
- The six connector-only Gmail tools use the same protobuf naming (Send, Reply, Forward,
  UpdateDraft, UpdateLabel, DeleteLabel RPC), so Google authored those too.

**Google wrote all 29 Gmail tools and all 9 Calendar tools.** The public endpoint publishes 23;
the connector gets 29. Calendar is published identically to both.

### The six Google withholds

```
send_message   reply   forward      no outbound mail at all
update_draft                        create a draft, then never change it
update_label   delete_label         no rename, no delete
```

**Google's Gmail server cannot send mail.** Why is not observable: `gmail.compose` would
authorize sending and the server already declares it, so scope is not the reason. The withheld set
is exactly outbound mail plus irreversible label operations, which reads as preview-stage risk
reduction with a vetted client getting more. *Inference, not fact.*

Same shape `csa-google-workspace` found for Drive — the connector publishes 11, Google's server
the same 8 minus `update_file`, `share_file`, `trash_file`.

## Google Gmail MCP — 23 tools, with Google's own annotations

`R` = `readOnlyHint`, `D` = `destructiveHint`, `I` = `idempotentHint`. Every tool on both servers
carries annotations **and** an `outputSchema`.

| Tool | R | D | I | API method |
|---|:-:|:-:|:-:|---|
| `search_threads` | ✓ | | ✓ | `users.threads.list` |
| `get_thread` | ✓ | | ✓ | `users.threads.get` |
| `get_message` | ✓ | | ✓ | `users.messages.get` |
| `get_draft` | ✓ | | | `users.drafts.get` |
| `list_drafts` | ✓ | | | `users.drafts.list` |
| `list_labels` | ✓ | | ✓ | `users.labels.list` |
| `create_draft` | | | | `users.drafts.create` |
| `create_label` | | | | `users.labels.create` |
| `label_message` | | | ✓ | `users.messages.modify` |
| `unlabel_message` | | ✓ | ✓ | `users.messages.modify` |
| `update_message_labels` | | | ✓ | `users.messages.modify` |
| `label_thread` | | | ✓ | `users.threads.modify` |
| `unlabel_thread` | | ✓ | ✓ | `users.threads.modify` |
| `trash_message` | | ✓ | ✓ | `users.messages.trash` |
| `untrash_message` | | | ✓ | `users.messages.untrash` |
| `trash_thread` | | ✓ | ✓ | `users.threads.trash` |
| `untrash_thread` | | | ✓ | `users.threads.untrash` |
| `mark_message_spam` | | ✓ | ✓ | `users.messages.modify` |
| `unmark_message_spam` | | | ✓ | `users.messages.modify` |
| `mark_thread_spam` | | ✓ | ✓ | `users.threads.modify` |
| `unmark_thread_spam` | | | ✓ | `users.threads.modify` |
| `apply_sensitive_message_label` | | ✓ | ✓ | `users.messages.trash` / `.modify` |
| `apply_sensitive_thread_label` | | ✓ | ✓ | `users.threads.trash` / `.modify` |

The annotation logic is coherent: removing something the user expects to be there is destructive
(`unlabel_*`, `trash_*`, `mark_*_spam`); putting it back is not (`untrash_*`, `unmark_*_spam`).
`create_draft` and `create_label` are the only non-idempotent writes.

## Google Calendar MCP — 9 tools

| Tool | R | D | I | API method |
|---|:-:|:-:|:-:|---|
| `list_calendars` | ✓ | | ✓ | `calendarList.list` |
| `list_events` | ✓ | | ✓ | `events.list` |
| `get_event` | ✓ | | ✓ | `events.get` |
| `search_events` | ✓ | | ✓ | **none — semantic search** |
| `suggest_time` | ✓ | | ✓ | `freebusy.query` + slot selection |
| `create_event` | | | | `events.insert` |
| `update_event` | | | ✓ | `events.patch` |
| `respond_to_event` | | | ✓ | `events.patch` (attendee `responseStatus`) |
| `delete_event` | | ✓ | ✓ | `events.delete` |

`search_events` has no public REST equivalent. `events.list` offers `q`, a verbatim AND-match over
title, description, location and attendees — not semantic. **This is the one tool a local server
cannot reproduce**, and saying so is better than shipping keyword search under the same name.

## Community Gmail server — 30 tools

`@gongrzhe/server-gmail-autoauth-mcp` v1.1.11 by package identity, running from the
**ArtyMcLabin fork**, so this list may include fork additions not in upstream.

```
search_emails  read_email  get_thread  list_inbox_threads  get_inbox_with_threads
draft_email  update_draft  send_draft  delete_draft  send_email  reply_all
create_label  update_label  delete_label  get_or_create_label  list_email_labels
modify_email  modify_thread  batch_modify_emails
delete_email  batch_delete_emails
download_attachment  download_email
create_filter  create_filter_from_template  get_filter  list_filters  delete_filter
report_phishing  batch_report_phishing
```

It is the only surface here that exposes **filters**, **attachment download**, and **batch
operations**. It is also the only one that gets naming badly wrong: `delete_email` moves a message
to Trash and nothing in the list performs a permanent delete. A model reading it will believe it
destroyed something it did not, and cannot find the tool that would.

## Scope declarations exceed what the tools can use

From each server's `/.well-known/oauth-protected-resource/mcp/v1`:

| Server | Declares | Ships |
|---|---|---|
| Gmail | `mail.google.com`, `gmail.modify`, `gmail.compose`, `gmail.readonly`, `gmail.metadata` | no permanent-delete tool, though `mail.google.com` is the scope Google reserves for exactly that |
| Calendar | 12 scopes incl. `calendar`, `calendar.acls`, `calendar.calendars`, `calendar.settings.readonly` | **zero** ACL, calendar-management or settings tools |

A client granting the declared set grants materially more than the tools can exercise. It also
settles the documentation contradiction from machine-readable evidence: Gmail's server declares
`gmail.modify` while its page lists only `gmail.readonly` and `gmail.compose`.

Both declare `authorization_servers: ["https://accounts.google.com/"]` and **do not support
dynamic client registration** — `claude mcp add` fails with *"Incompatible auth server"* until
given a pre-registered `--client-id`.

## What the incumbents tell the model — and what they cannot

**At server level: nothing.** Neither Google server sends an `instructions` field in `initialize`;
`resources/list` and `prompts/list` return HTTP 404 — the methods are not routed at all. The
connectors likewise expose no instructions and no resources.

**At tool level: substantial.** 10,672 characters of tool description across 32 tools (mean 333,
max 1,464), ~46KB of `inputSchema` with per-parameter descriptions, `outputSchema` on 32/32,
annotations with human-readable titles on 32/32. Six patterns are recognisable:

| Pattern | Tools | Example |
|---|---:|---|
| Cross-tool routing | 12 desc / 5 schema | *"Use this tool to discover the `id` of a label before calling `label_thread`…"* |
| Prefer / instead | 8 | *"Use `trash_thread` when trashing a thread, even if it currently contains only 1 message."* |
| Negative capability | 3 | *"This tool does not support retrieving drafts."* |
| Empty-result disambiguation | 3 | *"An empty JSON object `{}` represents zero matching items, not an error."* |
| Context-cost steering | 2 (in the *parameter*) | *"We recommend using `PLAIN_TEXT` to prevent context exhaustion."* |
| Upstream-behaviour warning | 1 | `search_threads` explains Gmail matches messages before threads, so `-is:starred` still returns threads containing starred mail |

`get_message` alone carries *"Key indicators include…"* and *"Example user prompts are…"* at 1,464
chars — four times the mean. It reads like a template applied once and never rolled out.

### The blind spot: zero irreversibility warnings

**Not one tool, in any description or schema, says "cannot be undone", "permanently", or
"irreversible."** For Google's 23 that is defensible — trash is as destructive as they get. It is
not defensible for the connector's `delete_label`, whose entire description is:

> *"Deletes a label in the authenticated user's Gmail account."*

while Gmail's REST documentation for the same method says it *"Immediately and permanently deletes
the specified label and removes it from any messages and threads that it's applied to."* The
destructive half is not relayed. A model reading that tool cannot know it strips the label off
every message it touches.

### Why the gap exists

Their per-tool guidance is good. What they have nowhere to put is anything that **is not about one
tool**: that a capability is disabled and how an operator enables it; that message content is
untrusted data rather than instructions; what this deployment is hiding and why. Those are
structurally inexpressible in a per-tool description, which is likely why neither ships them.

`csa-google-workspace/mcp/_flavours.py` already names the consequence: a gated-but-registered tool
refuses with something relayable, an absent tool reads as *"this server cannot do that"* and the
model goes looking for another route. Restriction that announces itself is a restriction;
restriction that is silent is a missing feature.

## What no surface does

Against 117 REST methods (see `../analysis/coverage-matrix.csv`): Google reaches 21, the
connectors 26, **and 91 are reached by nobody.**

- **Mailbox settings — 45 methods, 57% of Gmail.** Filters, forwarding addresses, delegates,
  send-as, S/MIME, client-side encryption.
- **Calendar ACL — 7 methods.** Who can read or write a calendar.
- **Calendar management — 7 methods.** Creating and deleting calendars.
- **Attachments — 1 method**, referenced by a tool description that points at a tool nobody ships.
- **Permanent delete — 4 methods.** Trash is as far as anyone goes.
- **Incremental sync — 2 methods** plus `syncToken`, so every read is a full listing.

## Re-capturing this

```bash
for h in gmailmcp calendarmcp; do
  curl -s -X POST "https://$h.googleapis.com/mcp/v1" -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python3 -m json.tool
  curl -s "https://$h.googleapis.com/.well-known/oauth-protected-resource/mcp/v1"
done
```

No credential required. Both servers are in Developer Preview and the published tool list is not a
commitment — if the withheld-six inference is right, the public list grows as the preview matures.
Re-run and diff before trusting anything above.

## Drift re-check, 2026-09-16

Re-ran the capture above. **Both tool lists are unchanged: 23 Gmail, 9 Calendar, same names,
same order.** Every mapping and count in this document still holds, and so does the 21-of-117
figure. Fresh captures are `research/captures/2026-09-16-*-tools-list.json`. What moved is
inside the schemas:

- **`create_label` gained `labelListVisibility` and `messageListVisibility` inputs**, and both
  they and `labelType` now appear in the `create_label` / `list_labels` output. This is
  `users.labels.create` getting closer to its REST body — a deepening of a method already
  counted as reached, not a new method.
- **`apply_sensitive_message_label` and `apply_sensitive_thread_label` are being talked down.**
  Both descriptions now open *"Prefer `trash_*` or `mark_*_spam` instead"*, and `label_message`
  / `label_thread` were rewritten to point at the specific tools rather than at the sensitive-label
  pair. The tools still exist; Google is routing models away from them. Worth watching as a
  removal candidate, since this repo maps both onto `.trash` / `.modify`.
- **`get_thread` now states `RAW` format is not supported**, closing a gap the enum left open.
- **`search_threads` gained query-construction guidance** — prefer unquoted keywords, use `OR`
  and grouping, avoid copying long subjects verbatim. Prose only; the schema is unchanged.
- **Calendar event output gained an expanded `label` object** (`id`, `displayName`,
  `backgroundColor`) on all seven event-returning tools. Note this is a *server-side expansion*:
  Calendar v3 REST returns `Event.eventLabelId`, a bare string, with the label bodies living on
  the calendar under `LabelProperties.eventLabels`. Both were already in the 2026-08-31 snapshot
  and neither changed. The MCP server denormalizes; the REST API did not gain anything.

Neither Discovery document gained or lost a method or a scope in the same window — see
[`specs/PROVENANCE.md`](../specs/PROVENANCE.md).
