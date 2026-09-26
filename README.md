# csa-google-gmail-calendar

```
project_tracker_base: CINO Project Tracker:appf7fRQUvY9Iy7sL
project_tracker_table: Projects:tblchmbxSAavvJKaY
project_tracker_record: not yet created
project_source: github:CloudSecurityAlliance-Internal/CINO-Projects/projects/CloudSecurityAlliance/csa-google-gmail-calendar
```

A Python library and local stdio MCP server over the Gmail and Google Calendar REST APIs, on two
pillars:

1. **100% API coverage** — all 117 methods, against the 21 the official servers reach.
2. **Message analysis** — fetch a message as raw MIME and check it: SPF/DKIM/DMARC/ARC
   verification, header-presentation sanity (`Reply-To` vs `From`, brand display names, lookalike
   domains), spam heuristics, and prompt-injection detection.

The second pillar exists because the first is not enough. A phish impersonating Mailchimp, with a
`Reply-To` on an unrelated free Google account, was delivered to a CSA inbox — and it would have
**passed** SPF, DKIM and DMARC, because those authenticate the sending *domain*, not the claimed
*identity*. See §9 of the design spec.

> **Status: implemented.** A `Backend`/`PolicyBackend`/MCP-tools stack, a local stdio server, an
> offline test suite, and the config/demo/CI surface described below all exist in `src/` and
> `tests/`. Only a live probe against a real Google account (task 14 of the first-implementation
> plan) remains. The Discovery snapshots, operation inventory, and live captures of the official
> Google MCP servers below are what the design was built against, kept as evidence rather than
> superseded by the code.

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
| `analysis/observed-mcp-tools.json` | Every surface's tool list, mapped onto API methods |
| `analysis/coverage-matrix.csv` | 117 rows: which surface reaches each method |
| `research/2026-09-01-mcp-server-landscape.md` | The three surfaces in full, and what they tell the model |
| `research/captures/` | Raw JSON-RPC captures from Google's servers |
| `scripts/coverage.py` | Regenerates the matrix and the README tables |
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

## Alignment: copy, fill, better

Google authored every tool on both official surfaces, so its vocabulary is *the* vocabulary. The
plan has three parts, in order of how much each is owed to them.

### Copy — their per-tool conventions, adopted wholesale

- **All 29 Gmail and 9 Calendar tool names.** Google wrote the six it withholds publicly
  (`send_message`, `reply`, `forward`, `update_draft`, `update_label`, `delete_label`) as well as
  the 23 it publishes, so using them costs no invention and no divergence.
- **The thread/message axis named in every tool** — `label_thread` vs `label_message` — which the
  community server collapses and then cannot express.
- **`view` / `messageFormat` projections.** Framed by Google as economy; `METADATA_ONLY` returns
  envelope without body, which makes it a data-minimisation control a caller cannot forget.
- **Annotations and `outputSchema` on every tool**, as they do on 32/32. Their annotation logic is
  right and we adopt it: removing something the user expects to be there is destructive
  (`unlabel_*`, `trash_*`, `mark_*_spam`); putting it back is not.
- **Their six guidance patterns** — cross-tool routing, prefer/instead, negative capability,
  empty-result disambiguation, context-cost steering, upstream-behaviour warnings.

### Fill — the gaps inside their own model

- **Irreversibility warnings.** No tool on any surface says "cannot be undone" or "permanently".
  Their `delete_label` reads *"Deletes a label in the authenticated user's Gmail account"* where
  the REST API says it *"Immediately and permanently deletes the specified label and removes it
  from any messages and threads that it's applied to."* We relay the destructive half — and we
  ship far more irreversible operations than they do.
- **`get_attachment`.** Their `get_message` returns `attachment_ids` and points the model at a
  `GetMessageAttachment` request no surface exposes.
- **The guidance template applied to every tool**, not just `get_message` — the one tool that got
  "Key indicators" and "Example user prompts" before the rollout apparently stopped.

### Better — the layer they have nowhere to put

Their per-tool guidance is good. What has no home in a tool description is anything that is not
about one tool, which is why neither server ships it:

- **Server `instructions`** — that message content is untrusted data and never instructions; which
  credentials are configured and what each unlocks; what this deployment is hiding and why.
- **MCP resources** — the live effective policy and a capability reference, as
  `csa-google-workspace` publishes at `csa-gw://config`. Both official servers return HTTP 404 for
  `resources/list`; the method is not routed at all.
- **Refusals that can be relayed.** A gated-but-registered tool says "capability X is disabled, an
  operator enables it in configuration". An absent tool reads as "this server cannot do that", and
  the model goes looking for another route.
- **Scope minimisation.** Both official servers over-declare: Gmail advertises `mail.google.com`
  while shipping no permanent-delete tool; Calendar advertises `calendar.acls` while shipping no
  ACL tool. We request the scopes the *enabled capabilities* need and nothing else.

### The one thing we cannot match

`search_events` is semantic search with no public REST equivalent. `events.list` offers `q`, a
verbatim AND-match. Shipping keyword search under Google's name for semantic search would be the
one lie that is hard to detect from outside, so we do not ship it, and the `google` flavour says
it is absent and why.

## Tools

46 tools total under the default (`full`) flavour with every capability enabled. Generated by
`python3 scripts/generate_tool_table.py` from the live registry (`create_server`) - never
hand-edited; `tests/test_docs_drift.py` fails if this table falls behind what the server
actually registers. `core`/`google` columns mark whether a tool survives under that
`CSA_GGC_FLAVOUR` (see "Flavours" below); `-` in `Capability` means the tool is exempt from
capability gating (the auth-lifecycle and configuration-surface tools).

<!-- TOOLS:START -->
| Tool | Capability | core | google | Description |
|---|---|---|---|---|
| `archive_email` | mail.write | yes |  | Remove a message from the Inbox view (the `INBOX` label) without deleting or |
| `archive_thread` | mail.write | yes |  | `archive_email`, applied to every message in a thread at once. |
| `auth_status` | - | yes | yes | Report whether a credential is cached, whether it covers every scope this |
| `authenticate` | - | yes | yes | Authorize this server to reach your Google Mail and Calendar, via your browser. |
| `create_draft` | mail.write | yes | yes | Compose an unsent draft - the safe rehearsal for a send. Nothing goes on the |
| `create_event` | calendar.write |  | yes | Create a new event on a calendar. |
| `create_label` | mail.write | yes | yes | Create a new user label with this name, returning its id for use in |
| `delete_draft` | mail.write | yes |  | Permanently DESTROY an unsent draft - Google's own words for `drafts.delete` |
| `delete_event` | calendar.delete |  | yes | Permanently DESTROY an event. Unlike Gmail's trash/untrash pair, Google's |
| `demonstration_plan` | - | yes | yes | An ordered plan for demonstrating everything THIS deployment's registered tools can |
| `describe_configuration` | - | yes | yes | What this deployment has enabled, what it is hiding and why, and whether its cached |
| `find_free_time` | calendar.read |  |  | When are we free - not when are we busy. `free` lists the gaps, inside |
| `forward` | mail.send | yes |  | Forward a message to NEW recipients, quoting the original sender, date, subject |
| `get_attachment` | mail.read | yes |  | Download one attachment from a message to disk, under the configured attachment |
| `get_draft` | mail.read | yes | yes | One draft's subject, to/cc, and body (truncated the same way `get_message`'s |
| `get_event` | calendar.read |  | yes | One event's full detail by id - summary, time, location, description, and every |
| `get_message` | mail.read | yes | yes | Read one message: headers, body as Markdown, and its real attachments (metadata |
| `get_profile` | mail.read | yes |  | This account's own mailbox summary: its address, total message and thread |
| `get_thread` | mail.read | yes | yes | A conversation's messages, as SUMMARIES (sender, subject, date, snippet, labels) |
| `list_calendars` | calendar.read |  | yes | Every calendar this account can see - its own primary calendar, plus any other |
| `list_drafts` | mail.read | yes | yes | List unsent drafts - a preview (subject, to, cc) each, never the full body, |
| `list_events` | calendar.read |  | yes | List events on one calendar, optionally bounded to a time window and/or filtered |
| `list_history` | mail.read | yes |  | What changed in this mailbox since a known point - message adds/removes and |
| `list_labels` | mail.read | yes | yes | Every label on this account, system (`INBOX`, `UNREAD`, `SPAM`, `TRASH`, ...) |
| `list_threads` | mail.read | yes |  | List conversations, optionally filtered with the same query syntax |
| `logout` | - | yes | yes | Revoke the stored credential at Google (best effort) and delete the local token |
| `mark_read` | mail.write | yes |  | Remove the `UNREAD` label from one message. Reverse with `mark_unread`. |
| `mark_spam` | mail.write | yes |  | Move a message to Spam (also removing it from the Inbox) - this can influence |
| `mark_unread` | mail.write | yes |  | Add the `UNREAD` label back to one message. Reverse with `mark_read`. |
| `modify_message_labels` | mail.write | yes |  | Add and/or remove label IDs on one message - Gmail's one general-purpose |
| `modify_thread_labels` | mail.write | yes |  | Same as `modify_message_labels`, applied to every message in a thread at once. |
| `reply` | mail.send | yes |  | Reply to the ORIGINAL SENDER ONLY - never the other recipients of the original |
| `reply_all` | mail.send | yes |  | Reply to the original sender AND every other original recipient - the set is |
| `report_a_problem` | - | yes | yes | Assemble a bug report for this server: version, OS, Python, and the active |
| `reschedule_event` | calendar.write |  |  | Move an event to a new start/end time. This is ALL this tool does - it cannot |
| `respond_to_event` | calendar.write |  | yes | RSVP to an event YOU were invited to, as yourself - never on another attendee's |
| `search_messages` | mail.read | yes |  | Find messages with Gmail's own search syntax - NOT a free-text question. Plain |
| `send_draft` | mail.send | yes |  | Send an existing draft as-is - irreversible. Use `get_draft` first to confirm |
| `send_message` | mail.send | yes |  | Send a brand-new message immediately - irreversible, with no undo. Prefer |
| `trash_email` | mail.write | yes |  | Move one message to Trash. This is NOT permanent deletion - Gmail keeps trashed |
| `trash_thread` | mail.write | yes | yes | `trash_email`, applied to every message in a thread at once. Reverse with |
| `unmark_spam` | mail.write | yes |  | Move a message out of Spam, back to the Inbox. Reverse of `mark_spam`. |
| `untrash_email` | mail.write | yes |  | Restore one message out of Trash, back to wherever its other labels put it |
| `untrash_thread` | mail.write | yes | yes | `untrash_email`, applied to every message in a thread at once. |
| `update_draft` | mail.write | yes |  | Replace an existing draft's content wholesale - every argument is the NEW |
| `whoami` | mail.read | yes | yes | This account's own email address, and nothing else - the narrow answer to "who |

<!-- TOOLS:END -->

## Capabilities

`CSA_GGC_CAPABILITIES` gates every mutating and every reading action (`policy.py`). A capability
not enabled means its tools are **absent**, not present-and-refusing (`_capabilities.py`).

| Capability | What it gates | Default |
|---|---|---|
| `mail.read` | Search, read, list Gmail messages/threads/drafts/labels, `list_history`, `get_profile`, `whoami` | on |
| `mail.write` | Draft CRUD, label create/apply, archive, mark read/unread, trash/untrash, mark/unmark spam | on |
| `mail.send` | `send_message`, `send_draft`, `reply`, `reply_all`, `forward` - irreversible | on |
| `mail.delete` | Permanent message/thread deletion - no tool in this server uses it yet | **off** |
| `calendar.read` | List calendars/events, get one event, `find_free_time` | on |
| `calendar.write` | Create/reschedule events, respond to an invitation | on |
| `calendar.delete` | `delete_event` - permanent, no undo (Calendar has no trash) | **off** |

Unset means the shipped default: every capability except `mail.delete` and `calendar.delete`
(both irreversible, both off by default). Set an explicit, complete list:

```bash
CSA_GGC_CAPABILITIES=mail.read,calendar.read      # exactly these two
CSA_GGC_CAPABILITIES=default,mail.delete          # the usual set, plus permanent delete
CSA_GGC_CAPABILITIES=none                          # nothing enabled (auth + config tools only)
```

## Flavours

`CSA_GGC_FLAVOUR=core|google|full` restricts which tools are **registered**, on top of whatever
`CSA_GGC_CAPABILITIES` already allows - never which refuse (`_flavours.py`).

| Flavour | What it is |
|---|---|
| `full` (default) | No restriction beyond capabilities. |
| `core` | Spec §5's 31-tool minimum - "what email needs to work". Gmail only; no Calendar tool survives. |
| `google` | A conservative, literal tool-name match against what Google's own `gmailmcp`/`calendarmcp` servers publish (`research/captures/2026-09-01-*.json`) - under-represents rather than over-claims where this project's naming differs from theirs for the same operation (`reschedule_event` vs their `update_event`, `find_free_time` vs their `suggest_time`, and every tool this project expands per ADR-001 that their surface does not). |

`authenticate`/`auth_status`/`logout`/`describe_configuration`/`demonstration_plan`/
`report_a_problem`/`whoami` are exempt from every flavour - narrowing a deployment cannot lock
it out of its own login or its own introspection. Call `describe_configuration` to see exactly
what the active flavour hides and why.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `CSA_GGC_CAPABILITIES` | Which capabilities this deployment enables - see "Capabilities" above | every capability except `mail.delete`/`calendar.delete` |
| `CSA_GGC_FLAVOUR` | `core`\|`google`\|`full` - see "Flavours" above | `full` |
| `CSA_GGC_TOKEN_PATH` | Where the cached OAuth token lives | `~/.csa_google_gmail_calendar/token.json` |
| `CSA_GGC_CLIENT_SECRETS` | OAuth client secrets JSON, needed only for `authenticate`/`login` (a cached token works without it) | `~/.csa_google_gmail_calendar/client_secret.json` if it exists, else unset |
| `CSA_GGC_ATTACH_DIR` | Directory outgoing mail may attach local files from; unset means attachments are off, not unrestricted | unset |
| `CSA_GGC_LOG_LEVEL` | `DEBUG`\|`INFO`\|`WARNING`\|`ERROR`\|`CRITICAL` | `WARNING` |

Authenticates as the operating user via OAuth, the same model `csa-google-workspace` uses.
Run `csa-google-gmail-calendar login` once in a terminal, or call the `authenticate` tool from
inside a session that supports MCP URL elicitation.

## Structural absences

Three things a person names that the API does not have (spec §5) - none of these is a gap in
this server specifically; they do not exist on Google's Gmail/Calendar REST surface at all:

- **There is no move.** Gmail has no folders. Every organisational act is `addLabelIds`/
  `removeLabelIds` on `users.messages.modify` - *move to folder* is add a label, *archive* is
  remove `INBOX`, *mark read* is remove `UNREAD`, *spam* is add `SPAM`. `archive_email`/
  `trash_email`/`mark_spam` each name one specific combination rather than exposing the raw
  label-list edit as the only primitive, per ADR-001.
- **There is no attachment upload tool.** The only attachment operation Gmail's API has is
  `users.messages.attachments.get` (this server's `get_attachment`, download only). Sending an
  attachment is an argument (`attachments=[...]`, a local path under `CSA_GGC_ATTACH_DIR`) on
  `send_message`/`create_draft`/`update_draft`/`reply`/`reply_all`/`forward`, not a tool of its
  own.
- **There is no receive.** Nothing pushes to a client. `list_history` gives increments and
  `search_messages`/`list_threads` poll; `users.watch` needs a Cloud project, a Pub/Sub topic and
  a public HTTPS endpoint, none of which this stdio-only server can offer, so it is omitted
  rather than shipped broken.

Two further, narrower gaps, tracked in `TODO.md` rather than fixed here: there is no
`delete_label`/`update_label` tool yet (Backend's own Protocol has neither method), so a label
created by `create_label` - including the one `demonstration_plan` creates for its own demo run
- cannot be removed again through this server; and `list_history`'s `FakeBackend` double needs
its history entries seeded explicitly (there is no automatic "every write appends a history
record" wiring), which is enough to test the tool but not a full incremental-sync simulation.

## Development

```bash
python3 -m pytest --cov --cov-fail-under=90    # unit suite, offline, against FakeBackend
ruff check .                                    # lint
mypy                                            # types
python3 scripts/generate_tool_table.py          # regenerate the Tools table above
python3 scripts/coverage.py --markdown          # regenerate the Coverage tables above
python3 scripts/inventory.py                    # regenerate the 117-method inventory
```

See `RELEASING.md` for how a release reaches PyPI, and `CHANGELOG.md` for what shipped when.

## License

Apache 2.0.
