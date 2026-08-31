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

## Why this exists

Google ships official Gmail and Calendar MCP servers. They are real, they are competent, and
they are the thing to align with rather than ignore. But they are **Google-hosted remote
endpoints gated behind the Developer Preview Program**, and between them they reach **26 of
these 117 methods**.

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
- **The official Gmail server cannot download an attachment.** It returns `attachment_ids` and
  a description pointing at a `GetMessageAttachment` request that is not among its tools.

Two further findings shape the design more than the coverage number does:

- **Google's published scope lists disagree with Google's shipped servers.** The Calendar page
  lists three read-only scopes for a server that creates and deletes events. The Gmail page
  omits `gmail.modify` for a server that trashes mail. Scopes must be derived from the
  Discovery document's per-method `scopes` array, never from prose.
- **The documentation undercounts both servers.** 11 Gmail tools documented, 29 shipped; 8
  Calendar documented, 9 shipped. The 18 undocumented Gmail tools are the destructive half —
  send, reply, forward, trash, spam, delete_label.

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
| `analysis/PRIOR-ART.md` | The official servers and one community server, captured live |
| `analysis/operation-inventory.csv` | 117 rows, one per method, with per-method scopes |
| `analysis/official-mcp-tools.json` | 38 official tool names, mapped onto API methods |
| `scripts/inventory.py` | Regenerates the inventory from `specs/` |

Google publishes **Discovery documents**, not OpenAPI, and unlike Zendesk it links to them —
they are the same artifact its client libraries are generated from, so they are authoritative
rather than best-effort. They are still snapshots of a moving target; both were revised within
a week of capture. Re-fetch and diff before trusting them.

## Prior art was captured live, not read

The tool inventories in `analysis/` were taken off the wire from running MCP connections, not
transcribed from documentation. That is why they disagree with Google's pages, and the
disagreement is itself recorded. Re-check when the servers leave Developer Preview.

## Alignment

Where Google's official tools and ours do the same thing, ours will carry Google's name —
`get_message`, `search_threads`, `label_thread`, `create_event`, `suggest_time`. Two of its
conventions are worth adopting outright: the **thread/message axis named in every tool**, and
the **`view` / `messageFormat` projections** that let a caller ask for envelope without body.
Departures from Google's naming will be listed here with a reason, once there are any.

One official tool cannot be reproduced: `search_events` is semantic search with no public REST
equivalent. Saying so is better than shipping a keyword search wearing the same name.

## Configuration

Nothing to configure yet. The shipped design authenticates with OAuth as the operating user, as
`csa-google-workspace` does.

```bash
python3 scripts/inventory.py    # 117 methods
```

## License

Apache 2.0.
