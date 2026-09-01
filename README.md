# csa-google-gmail-calendar

```
project_tracker_base: CINO Project Tracker:appf7fRQUvY9Iy7sL
project_tracker_table: Projects:tblchmbxSAavvJKaY
project_tracker_record: TODO - not yet created
project_source: github:CloudSecurityAlliance-Internal/CINO-Projects/projects/CloudSecurityAlliance/csa-google-gmail-calendar
```

A Python library and local stdio MCP server over the Gmail and Google Calendar REST APIs,
targeting **100% API coverage**.

> **Status: API surface enumerated. Nothing implemented.** There is no `src/` yet. This
> repository currently holds the upstream Discovery snapshots, the operation inventory, and the
> live capture of what the official Google MCP servers actually expose. Do not describe any
> feature below as working.

## Scope

| API | Methods | Families | Mutating | 1.0.0 |
|---|---:|---:|---:|---|
| Gmail v1 | 79 | 15 | 49 | yes |
| Calendar v3 | 38 | 8 | 27 | yes |
| **Total** | **117** | **23** | **76** | |

Out of scope, each needing its own snapshot: Gmail Postmaster Tools, the Admin SDK, Google
Workspace Events, and the Apps Script / add-on surfaces.

## Coverage

Regenerate with `python3 scripts/coverage.py --markdown`; per-method detail is
`analysis/coverage-matrix.csv`.

**Google MCP** is `gmailmcp.googleapis.com` and `calendarmcp.googleapis.com`, captured
unauthenticated 2026-09-01. **Anthropic connector** is the claude.ai Gmail and Google Calendar
connectors, captured 2026-08-31. They are **one Google implementation at two exposure levels**,
not two products: all 32 shared tools are schema-identical, and the six tools only the connector
has are Google-authored too (see `analysis/observed-mcp-tools.json`).

<!-- COVERAGE:START -->
**117 API methods. Google's servers reach 21. The claude.ai connectors reach 26. Neither reaches 91.**

| Surface | Gmail tools | Calendar tools | API methods reached |
|---|---:|---:|---:|
| Google MCP (`gmailmcp`/`calendarmcp`) | 23 | 9 | 21 of 117 |
| claude.ai connectors | 29 | 9 | 26 of 117 |
| `csa-google-gmail-calendar` (target) | — | — | 117 of 117 |

### Gmail v1

| Family | Methods | Mutating | Google MCP | Anthropic connector | What is missing |
|---|---:|---:|---|---|---|
| `users` | 3 | 2 | **none** | **none** | **everything** |
| `users.drafts` | 6 | 4 | 3/6 | 5/6 | `delete` |
| `users.history` | 1 | 0 | **none** | **none** | **everything** |
| `users.labels` | 6 | 4 | 2/6 | 4/6 | `get`, `update` |
| `users.messages` | 11 | 9 | 4/11 | 5/11 | `batchDelete`, `batchModify`, `delete`, `import` +2 |
| `users.messages.attachments` | 1 | 0 | **none** | **none** | **everything** |
| `users.settings` | 10 | 5 | **none** | **none** | **everything** |
| `users.settings.cse.identities` | 5 | 3 | **none** | **none** | **everything** |
| `users.settings.cse.keypairs` | 6 | 4 | **none** | **none** | **everything** |
| `users.settings.delegates` | 4 | 2 | **none** | **none** | **everything** |
| `users.settings.filters` | 4 | 2 | **none** | **none** | **everything** |
| `users.settings.forwardingAddresses` | 4 | 2 | **none** | **none** | **everything** |
| `users.settings.sendAs` | 7 | 5 | **none** | **none** | **everything** |
| `users.settings.sendAs.smimeInfo` | 5 | 3 | **none** | **none** | **everything** |
| `users.threads` | 6 | 4 | 5/6 | 5/6 | `delete` |
| **total** | **79** | **49** | **14/79** | **19/79** | **60 methods** |

### Calendar v3

| Family | Methods | Mutating | Google MCP | Anthropic connector | What is missing |
|---|---:|---:|---|---|---|
| `acl` | 7 | 5 | **none** | **none** | **everything** |
| `calendarList` | 7 | 5 | 1/7 | 1/7 | `delete`, `get`, `insert`, `patch` +2 |
| `calendars` | 7 | 6 | **none** | **none** | **everything** |
| `channels` | 1 | 1 | **none** | **none** | **everything** |
| `colors` | 1 | 0 | **none** | **none** | **everything** |
| `events` | 11 | 8 | 5/11 | 5/11 | `import`, `instances`, `move`, `quickAdd` +2 |
| `freebusy` | 1 | 1 | 1/1 | 1/1 | — |
| `settings` | 3 | 1 | **none** | **none** | **everything** |
| **total** | **38** | **27** | **7/38** | **7/38** | **31 methods** |

### Grouped by what it does

| Capability group | API methods | Google MCP | Anthropic connector |
|---|---:|---|---|
| Read mail | 3 | all | all |
| Attachments | 1 | **none** | **none** |
| Drafts | 4 | 3/4 tools — no `update_draft` | all |
| Send | 2 | **none** — no `forward`, `reply`, `send_message` | all |
| Label definitions | 4 | 2/4 tools — no `delete_label`, `update_label` | all |
| Label application | 2 | all | all |
| Trash and spam | 6 | all | all |
| Permanent delete | 4 | **none** | **none** |
| Incremental sync | 2 | **none** | **none** |
| Mailbox settings | 45 | **none** | **none** |
| Calendar read | 4 | all | all |
| Calendar write | 3 | all | all |
| Calendar list & metadata | 11 | 1/11 methods | 1/11 methods |
| Calendar management | 7 | **none** | **none** |
| Calendar ACL | 7 | **none** | **none** |

<!-- COVERAGE:END -->

The shape of the gap, in one line each:

- **Google's Gmail server cannot send mail.** No `send_message`, `reply` or `forward`. It creates
  drafts it cannot send, and cannot modify one afterwards.
- **Nobody downloads an attachment**, though `get_message` returns `attachment_ids` and points at
  a `GetMessageAttachment` request no surface exposes.
- **Nobody touches mailbox settings** — 45 methods, 57% of Gmail. Filters, forwarding, delegates,
  send-as, S/MIME, client-side encryption.
- **Nobody permanently deletes anything.** Trash is as far as either surface goes.
- **Nobody reads calendar ACLs** — who can see or edit a calendar, 7 methods.
- **Nobody does incremental sync**, so every read is a full listing.


## Why this exists

Google ships official Gmail and Calendar MCP servers — two of **eight** Workspace MCP servers —
and they are the thing to align with rather than ignore. But they are **Google-hosted remote
endpoints gated behind the Developer Preview Program**. The closest surfaces we have probed, the
claude.ai Gmail and Calendar connectors, reach **26 of these 117 methods** between them; on the
Drive precedent Google's own servers reach the same or fewer.

The gap is not evenly distributed. It is concentrated in exactly the families a security
organisation cares about:

- **`users.settings.*` is 45 of Gmail's 79 methods — 57% of the API — and no MCP server
  exposes any of it.** Filters, forwarding addresses, delegates, send-as identities, S/MIME,
  client-side-encryption keys. A filter that forwards to an external address and archives the
  original is the canonical mailbox-persistence mechanism after an account compromise. Reading
  that subtree is a detection capability; writing to it is close to the most dangerous thing an
  agent can do to a mailbox.
- **Calendar `acl` is 7 methods, 5 of them mutating, exposed by nobody.** Who can read and
  write a calendar.
- **The Gmail connector cannot download an attachment.** It returns `attachment_ids` and a
  description pointing at a `GetMessageAttachment` request that is not among its tools.

Two further findings shape the design more than the coverage number does:

- **Google's published scope lists disagree with Google's own capability claims.** The Calendar
  page lists three read-only scopes on the page that says the server creates and deletes events.
  The Gmail page omits `gmail.modify` while describing trash. Scopes must be derived from the
  Discovery document's per-method `scopes` array, never from prose.
- **Ten Gmail methods cannot be called with user OAuth at all** — `updateAutoForwarding`,
  `delegates.*`, `forwardingAddresses.create/delete`, `sendAs.create/delete/verify` require a
  service account with domain-wide delegation. That includes `delegates.list`, so *you cannot
  enumerate who else can read a mailbox using that mailbox's own token.*

## Architecture

Fourth in the line after [`csa-skilljar`](https://github.com/CloudSecurityAlliance/csa-skilljar),
[`csa-google-workspace`](https://github.com/CloudSecurityAlliance/csa-google-workspace) and
`csa-zendesk`, on the same spine:

```
Backend (Protocol)   the seam - keyword-only args, returns raw upstream envelopes
    ^ wrapped by
PolicyBackend        capability gating; FAILS CLOSED - an ungated method is refused
    ^ consumed by
Client               thin typed library surface (the public product)
    ^ consumed by
mcp/_tools/*.py      per-family register_*(app, get_client) producers
```

Enforcement lives in the wrapper around the seam, not in the tools, so a library embedder gets
the same guarantee an MCP client does.

## What is here

| Path | What |
|---|---|
| `specs/` | Two upstream Discovery snapshots + `PROVENANCE.md` (URLs, sha256, revisions) |
| `analysis/API-SURFACE.md` | **Start here.** The enumeration and the five findings |
| `analysis/PRIOR-ART.md` | The two claude.ai connectors and one community server, captured live; what is still unprobed |
| `analysis/operation-inventory.csv` | 117 rows, one per method, with per-method scopes |
| `analysis/observed-mcp-tools.json` | 38 connector tool names, mapped onto API methods |
| `scripts/inventory.py` | Regenerates the inventory from `specs/` |

Google publishes **Discovery documents**, not OpenAPI, and unlike Zendesk it links to them —
they are the same artifact its client libraries are generated from, so they are authoritative
rather than best-effort. They are still snapshots of a moving target; both were revised within
a week of capture. Re-fetch and diff before trusting them.

## Prior art was captured live — but from the connectors, not from Google

The tool inventories in `analysis/` were taken off the wire from running MCP connections. Those
connections are the **claude.ai Gmail and Calendar connectors**, which are not the same product as
Google's `gmailmcp`/`calendarmcp` servers: `csa-google-workspace` verified that for Drive, where
the connector ships 11 tools and Google's server 8.

**Google's own servers have not been captured.** Doing so needs Developer Preview enrollment,
`gcloud services enable`, and an OAuth client. Until then, claims about Google's tool list rest on
Google's documentation; only claims about the connectors rest on a probe. `PRIOR-ART.md` marks
which is which.

## Alignment

Where the observed tools and ours do the same thing, ours will carry the observed name —
`get_message`, `search_threads`, `label_thread`, `create_event`, `suggest_time`. Two of its
conventions are worth adopting outright: the **thread/message axis named in every tool**, and
the **`view` / `messageFormat` projections** that let a caller ask for envelope without body.
Departures from Google's naming will be listed here with a reason, once there are any.

One observed tool cannot be reproduced: `search_events` is semantic search with no public REST
equivalent. Saying so is better than shipping a keyword search wearing the same name.

## Configuration

Nothing to configure yet. The shipped design authenticates with OAuth as the operating user, as
`csa-google-workspace` does.

```bash
python3 scripts/inventory.py    # 117 methods
```

## License

Apache 2.0.
