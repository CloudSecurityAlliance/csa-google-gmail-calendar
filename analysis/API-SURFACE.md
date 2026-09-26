# API surface: Gmail v1 and Calendar v3

**Start here.** Generated 2026-08-31 from the Discovery snapshots in `specs/`
(`gmail:v1` rev 20260824, `calendar:v3` rev 20260826). Regenerate with
`python3 scripts/inventory.py`; the row-level detail is `operation-inventory.csv`.

Nothing is implemented. This file is the enumeration and the findings that will
constrain the design, not a description of a product.

## The count

**117 methods across 23 families. 76 of them mutate state.**

The "reachable via claude.ai connector" column maps each family onto the tool inventory in
`observed-mcp-tools.json` — a method counts as reachable if some connector tool calls it, even
indirectly. **26 of 117 methods are reachable. 91 are not.**

That column measures the **claude.ai connectors**, which are the only MCP surfaces probed live.
Google's own `gmailmcp`/`calendarmcp` servers have not been captured; on the Drive precedent they
reach the same or fewer. See `PRIOR-ART.md` for why the two are not the same product.

### Gmail v1 — 79 methods, 15 families

| family | methods | mutating | reachable via claude.ai connector |
|---|---:|---:|---|
| `users` | 3 | 2 | **none** |
| `users.drafts` | 6 | 4 | 5/6 |
| `users.history` | 1 | 0 | **none** |
| `users.labels` | 6 | 4 | 4/6 |
| `users.messages` | 11 | 9 | 5/11 |
| `users.messages.attachments` | 1 | 0 | **none** |
| `users.settings` | 10 | 5 | **none** |
| `users.settings.cse.identities` | 5 | 3 | **none** |
| `users.settings.cse.keypairs` | 6 | 4 | **none** |
| `users.settings.delegates` | 4 | 2 | **none** |
| `users.settings.filters` | 4 | 2 | **none** |
| `users.settings.forwardingAddresses` | 4 | 2 | **none** |
| `users.settings.sendAs` | 7 | 5 | **none** |
| `users.settings.sendAs.smimeInfo` | 5 | 3 | **none** |
| `users.threads` | 6 | 4 | 5/6 |
| **total** | **79** | **49** | **19/79** |

### Calendar v3 — 38 methods, 8 families

| family | methods | mutating | reachable via claude.ai connector |
|---|---:|---:|---|
| `acl` | 7 | 5 | **none** |
| `calendarList` | 7 | 5 | 1/7 |
| `calendars` | 7 | 6 | **none** |
| `channels` | 1 | 1 | **none** |
| `colors` | 1 | 0 | **none** |
| `events` | 11 | 8 | 5/11 |
| `freebusy` | 1 | 1 | 1/1 |
| `settings` | 3 | 1 | **none** |
| **total** | **38** | **27** | **7/38** |

## Findings that constrain the design

Five, in the order they will bite.

### 1. Gmail is mostly a settings API, and settings is where the security lives

`users.settings.*` is **45 of Gmail's 79 methods — 57% of the API** — and no MCP server
observed exposes any of it. That subtree is not miscellaneous configuration. It is:

| family | methods | what it controls |
|---|---:|---|
| `users.settings.filters` | 4 | rules that auto-archive, auto-delete, auto-forward |
| `users.settings.forwardingAddresses` | 4 | where a copy of every message goes |
| `users.settings.delegates` | 4 | who else can read and send as this mailbox |
| `users.settings.sendAs` | 7 | which addresses this account can send as |
| `users.settings.sendAs.smimeInfo` | 5 | S/MIME signing certificates |
| `users.settings.cse.identities` | 5 | client-side-encryption identities |
| `users.settings.cse.keypairs` | 6 | client-side-encryption key pairs |
| `users.settings` | 10 | vacation, IMAP/POP, auto-forwarding, language |

A filter that forwards mail to an external address and immediately archives it is the
canonical mailbox-persistence mechanism after an account compromise, and it is invisible
in the Gmail UI unless you go looking. **Reading this subtree is a detection capability.**
Writing to it is close to the most dangerous thing an agent could do to a mailbox — a
single `users.settings.updateAutoForwarding` call exfiltrates everything that arrives from
that moment on, silently, and survives a password reset.

Those two facts point the same way: the read/write split in this subtree matters more than
anywhere else in either API, and the two halves should not share a capability.

### 2. Calendar ACL is the same shape, one family over

`acl` is 7 methods, 5 of them mutating, exposed by nobody. It decides who can read and
write a calendar, and `acl.insert` with `role: "owner"` or a `scope.type: "default"` entry
makes a calendar world-readable. Same asymmetry as Gmail settings: reading it answers a
governance question, writing it is a grant of access.

### 3. Google's published scope lists disagree with Google's own capability claims

Recorded in full in `PRIOR-ART.md` §2, and it needs no probe — the contradiction is internal to
Google's documentation. The Calendar page lists three read-only scopes on the same page that says
the server creates and deletes events; the Gmail page omits `gmail.modify` while describing label
and trash operations.

*Design consequence:* never take a scope list from prose. Every method in
`operation-inventory.csv` carries its own `scopes` column, taken from the Discovery
document — the same artifact Google generates its client libraries from. That column is
the authorization fact to bind a policy layer to. **Gmail declares 14 distinct scopes and
Calendar 17**, which is a finer-grained thing to bind to than Zendesk's 54 but far better
than Skilljar's all-or-nothing key.

Note the trap in that column: sorting scopes by string length puts `calendar` first, and
`calendar` is the *broadest* scope in the set. Narrowness has to be a declared partial
order, not an inferred one.

One more trap sits a level up: `narrowest_scope` says what the API would *accept* for that one
method in isolation, not what this server should *request* given everything a capability
turns on — `events.get`'s narrowest listed scope cannot read a private calendar and
`events.insert`'s cannot patch an event someone else organised, so `auth._CAPABILITY_SCOPES`
deliberately requests a broader, ranked-at-or-above scope for both (carry-forward-task-9.md).

### 4. There is no `deprecated` flag, so drift will be silent

Discovery documents carry no deprecation marker. `scripts/inventory.py` detects it by
string-matching `description`, which is prose and will drift. Any claim this repository
makes about a method being current is only as good as the snapshot date in
`specs/PROVENANCE.md`. Re-fetch and diff before trusting it.

### 5. One observed tool cannot be reproduced

`search_events` — shipped by the claude.ai Calendar connector, undocumented by Google, and of
unverified presence on Google's own server — performs **semantic** search over the primary
calendar. No public Calendar
API method does semantic search — `events.list` offers `q`, a verbatim AND-match over
title, description, location and attendees. Whatever Google runs behind
`calendarmcp.googleapis.com` is not in the REST surface.

This is the one place where a local server is strictly worse than the hosted one, and it
should be said plainly rather than papered over with a keyword search wearing the same
name. Its converse is the structural advantage: every alternative is remote — Google's eight
servers are hosted endpoints gated behind the Developer Preview Program, and the claude.ai
connectors are fronted by Anthropic — so mailbox and calendar content leaves the machine either
way. A local stdio server keeps the credential and the content on it. That is the trade, and neither side of it is
free.

## Pagination

Google is consistent where Zendesk was not: a `pageToken` parameter means cursor paging,
and the Discovery document declares it honestly. **15 methods paginate** — 6 in Gmail, 9 in
Calendar. No offset paging anywhere, no undeclared paging found.

Two incremental-sync mechanisms exist and neither is exposed by any observed server:

- `users.history.list` (Gmail) — the change feed since a `historyId`.
- `syncToken` on the Calendar list methods, plus `events.watch` / `channels.stop` for push.

Anything that monitors a mailbox or calendar over time wants these rather than repeated
full listings.

## Not in these specs

Both Discovery documents describe the **v1/v3 REST APIs only**. Out of scope here, and each
would need its own snapshot: Gmail Postmaster Tools, the Admin SDK (domain-wide delegation,
audit logs), Google Workspace Events, and the Apps Script / Add-on surfaces that the
`gmail.addons.*` scopes in the scope column belong to.
