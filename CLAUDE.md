# CLAUDE.md

## What this repository is

`csa-google-gmail-calendar` — a Python library and **local stdio** MCP server over the Gmail v1
and Calendar v3 REST APIs, targeting 100% API coverage. Fourth in the line after `csa-skilljar`,
`csa-google-workspace` and `csa-zendesk`, on the same spine.

> **There is no `src/`. Nothing is implemented.** This repository currently holds the upstream
> Discovery snapshots, the operation inventory, the live captures of every competing MCP surface,
> and the design that those constrain. **Never describe a feature here as working.**

## Where things live

- **`specs/`** — the two upstream Discovery snapshots plus `PROVENANCE.md` (URLs, sha256,
  revisions). Google publishes Discovery documents, **not OpenAPI**.
- **`analysis/`** — `API-SURFACE.md` is the start-here. `PRIOR-ART.md` is the competing surfaces
  and what is still unprobed. `operation-inventory.csv` is 117 rows, one per API method, with
  per-method scopes. `coverage-matrix.csv` is which surface reaches which method.
  `observed-mcp-tools.json` is every observed tool, mapped onto API methods.
- **`research/`** — `2026-09-01-mcp-server-landscape.md` is the full account of all three
  surfaces. `captures/` holds the raw JSON-RPC responses; treat them as evidence, do not edit.
- **`scripts/`** — `inventory.py` regenerates the inventory from `specs/`. `coverage.py`
  regenerates the matrix and splices the README coverage tables between its `COVERAGE` markers.
- **`docs/superpowers/specs/`** — the design. Authoritative over anything in chat.

## Critical facts — check these before writing code

1. **Discovery documents are a recursive `resources` tree, not a flat `paths` map.** Gmail has
   exactly one top-level resource (`users`) with everything beneath it. A non-recursive reader
   reports **0 operations for Gmail** and is not obviously wrong until you look at the number.

2. **Every method carries its own `scopes` array**, and it is an **any-of** set, not a category.
   17 distinct scope-sets per API, one spanning four families and four HTTP verbs. You cannot read
   a capability off a method's scope list. Sorting scopes by string length puts `calendar` first,
   and `calendar` is the *broadest* scope in the set — narrowness must be a declared partial
   order, never an inferred one.

3. **Google's scope boundaries are a FLOOR for our capability model, not the definition.** They
   are demonstrably incoherent in places: `sendAs.create` needs `gmail.settings.sharing` but
   `sendAs.patch` does not, and `cse.keypairs.obliterate` — which permanently destroys a key pair
   and makes encrypted mail unreadable — is reachable with `gmail.settings.basic`, the same scope
   as setting a vacation responder. Adopt Google's line where it is stricter than ours, override
   where it is looser, and record every override with a reason. The policy test asserts
   `our_gate >= google_gate`, never equality.

4. **Ten Gmail methods cannot be called with user OAuth at all.** `updateAutoForwarding`,
   `delegates.{create,delete,get,list}`, `forwardingAddresses.{create,delete}`,
   `sendAs.{create,delete,verify}` require a **service account with domain-wide delegation**.
   `delegates.list` is among them, so *you cannot enumerate who else can read a mailbox using that
   mailbox's own token.* This makes the server **two-credential**, as `csa-skilljar` is across the
   two Skilljar APIs: user OAuth reaches 107 methods, DWD reaches the other 10 and is **optional
   and absent by default**.

5. **`SmimeInfo` carries `encryptedKeyPassword` on a READ path** (`smimeInfo.get`/`list`). Withhold
   the field, keep the tool — `csa-skilljar`'s webhook-secret precedent. CSE keypair reads are
   fine: they return `pem` (public) and an opaque external KACLS reference, no private key.

6. **There is no `deprecated` flag in Discovery.** `inventory.py` detects deprecation by
   string-matching `description`, which is prose and will drift. Any claim this repo makes about a
   method being current is only as good as the snapshot date in `specs/PROVENANCE.md`.

## The competing surfaces — do not confuse them

`mcp__claude_ai_*` is the **claude.ai connector namespace**, and a connector is **not** Google's
server. `csa-google-workspace/research/drive-mcp-servers-and-api-surface.md` established this for
Drive: connector 11 tools, Google's `drivemcp` 8. The same mistake was made here on 2026-08-31 and
corrected in commit `679839e`.

For Gmail and Calendar the relationship is now measured, not assumed: **Google authored all of
them.** All 32 shared tools are schema-identical, every `inputSchema` carries a protobuf
request-message description, and the six connector-only Gmail tools use the same naming. Google
publishes 23 Gmail tools; the connector gets 29; Calendar is 9 on both.

**`tools/list` on Google's endpoints needs no credential.** Re-capture costs one HTTP request:

```bash
curl -s -X POST https://gmailmcp.googleapis.com/mcp/v1 -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Invariants that fail silently — check when editing

- **`scripts/coverage.py`'s `TOOL_MAP` is hand-built and must stay that way.** A tool is not an
  endpoint: Google expands `users.messages.modify` into seven tools and collapses nothing, so the
  relation is many-to-many in both directions. The script fails loudly if a tool has no mapping or
  a mapping names no tool — keep both checks.
- **The README coverage tables are generated.** Edit `scripts/coverage.py`, then re-run with
  `--markdown`. Hand-editing between the `COVERAGE` markers is silently overwritten.
- **Family names in the inventory carry no API prefix** (`users.settings`, `acl`), while
  `operation_id` does (`gmail.users.settings.getVacation`). Mixing them produces a table of zeroes
  that looks plausible.

## Data hygiene — this repo will touch real mail and real calendars

- **Never commit API response bodies.** Record counts and shapes, never rows. Message bodies,
  subjects, sender addresses, attendee lists and calendar event titles are all personal data.
- **Never commit credentials.** `.env`, `token*.json`, `credentials.json`, `client_secret*.json`
  are gitignored *in this repo*, not only in someone's global config.
- **Redact in `__repr__`.** Domain objects holding message or attendee data need hand-written
  reprs; embedders log these and the default dump is a data leak.
- The raw captures in `research/captures/` are **tool schemas from public unauthenticated
  endpoints**, not user data. That is the only reason they are committed.

## Upstream drift — re-check before trusting this repo's map

Both Discovery documents were revised within a week of capture. Both Google MCP servers are in
Developer Preview and their published tool list is not a commitment. Re-fetch and diff:

```bash
python3 scripts/inventory.py     # 117 methods from specs/
python3 scripts/coverage.py --markdown   # matrix + README tables
```

## Working in this repo

- **Probe beats docs.** Google's own pages are wrong in ways that matter: the Calendar page lists
  three read-only scopes for a server that creates and deletes events, and both pages undercount
  their own tool lists. Every number in this repo should trace to a snapshot, a capture, or a
  script — never to prose.
- **Say what is unverified.** `PRIOR-ART.md` marks which claims rest on a probe and which on
  documentation. Keep that distinction when adding to it.
- Branch and PR for every change, including docs-only. Never commit to `main`.
