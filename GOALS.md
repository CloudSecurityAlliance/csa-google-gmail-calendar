# Goals

Shared goals for CSA's MCP server fleet — library-first, local stdio, one fail-closed seam at the
data boundary, offline-testable — are stated once in the fleet roster (`surfaces/mcp/ROSTER.md` in
the internal CINO-Platform-Engineering repo) and are not restated here. This file records only what
is specific to Gmail and Calendar.

## North Star

Everything a person can do with their own mail and calendar is reachable by a script or an agent —
**and every message that arrives can be checked, not just read.**

Two pillars, and the second is not decoration. A phish impersonating Mailchimp, with a `Reply-To`
on an unrelated free Google account, was delivered to a CSA inbox. It would have **passed** SPF,
DKIM and DMARC, because those authenticate the sending *domain*, not the claimed *identity*. A
server that fetches mail faithfully and says nothing about it hands that message to a model as
though it were trustworthy.

## The gap this exists to close

Measured against both official surfaces on 2026-09-01 — Google's own `gmailmcp.googleapis.com` and
`calendarmcp.googleapis.com`, and the Anthropic connector, which turned out to be *the same
implementation rather than a second one*:

| | Methods |
|---|---:|
| Gmail v1 + Calendar v3 total | **117** |
| Reached by both official surfaces | 21 |
| Connector-only | 5 |
| **Reached by neither** | **91** — of which 32 are reads |

So the official servers reach **22%** of the API they front. The interesting part is not the
percentage but *which* families are missing entirely:

- **`calendar.calendars` (7 methods) — zero coverage.** You can create, update and delete *events*.
  You cannot create or delete a **calendar**.
- **`calendar.acl` (7) — zero coverage.** Who can see your calendar is unreachable.
- **Every Gmail settings family — zero coverage:** `filters` (4), `forwardingAddresses` (4),
  `delegates` (4), `sendAs` (7), `smimeInfo` (5), `cse.*` (11), `settings` (10).

**That last group is the one that matters most, and a sibling project says why.**
[`csa-google-workspace-audit`](https://github.com/CloudSecurityAlliance/csa-google-workspace-audit)'s
ADR-001 identifies the mailbox-persistence audit — *filters, forwarding addresses, delegates,
send-as* — as high security value: they are how mailbox access is retained after a compromise.
Those exact families are reachable by **neither** official server. The capability with the clearest
security consequence is the one nobody has built.

## Near-term

| Goal | Success metric |
|---|---:|
| **The coverage report is generated, never hand-written** | `scripts/coverage.py` regenerates the matrix from the Discovery snapshots plus a live capture. A count in prose that disagrees with the CSV is a defect |
| **Library before server** | The typed client over both APIs, with the `Backend` seam and an offline fake, before any tool is registered |
| **Reads first, and the security-relevant reads first among those** | The 32 unreached read methods, ordered with the mailbox-persistence settings at the front |
| **Message analysis reaches a verdict, not a dump** | Raw MIME fetched and checked: SPF/DKIM/DMARC/ARC, header-presentation sanity (`Reply-To` vs `From`, brand display names, lookalike domains), and a stated conclusion. Validated against the Mailchimp phish, which must not come back clean |

## Medium-term

- **Prompt-injection detection as a returned field, not a filter.** Message bodies are
  attacker-controlled and are handed to a model as content. The sibling servers treat this as an
  open research question ([csa-google-workspace#297](https://github.com/CloudSecurityAlliance/csa-google-workspace/issues/297));
  here it is a product feature, because mail is the surface where hostile content is *expected*
  rather than incidental.
- **Close the mutating surface deliberately.** 76 of 117 methods mutate. They arrive gated and off,
  per family, with the irreversible ones — `delete`, `trash`, anything that sends — treated the way
  `csa-zendesk` treats a public reply.
- **The three gaps nobody else has, named because "nobody has this" is the argument for building it:**

  | Gap | Methods | Why it is unreached |
  |---|---:|---|
  | `calendar.calendars` | 7 | Google's server does events, not calendars. Create/delete a calendar is unreachable anywhere |
  | `calendar.acl` | 7 | Who can see a calendar. No official surface touches it |
  | `gmail.users.settings.filters` | 4 | Filter rules — and the scope trap below |

  **The filters gap has a cost the other two do not.** `settings.filters` and its neighbours
  (`delegates`, `forwardingAddresses`, `getAutoForwarding`) accept exactly four scopes and **none is
  settings-only-read**: `gmail.readonly` reads every message body in every mailbox, and
  `gmail.settings.basic` can *create* filters and forwarding rules. So reaching filters at all means
  taking one of those two, and the honest framing is that this project offers the **capability** —
  list, create, delete a filter for the operating user, under their own credential and their own ACLs.

  Auditing filters across a tenant for signs of compromise is a different job with a different
  credential, and belongs to a future `csa-google-gmail-audit` in the shape GAM occupies today — not
  here, and not in [`csa-google-workspace-audit`](https://github.com/CloudSecurityAlliance/csa-google-workspace-audit),
  which is scoped to log reading.

## Long-term

- **Useful outside CSA.** Any Google account holder gets a checked mailbox, free and open source.
- **The analysis pillar outlives the coverage pillar.** If Google extends its own servers to the
  full API, pillar one shrinks to nothing and that is fine — the fleet's shrink principle. Pillar two
  does not shrink, because Google has no incentive to tell you a message that passed authentication
  is still lying to you.

## Non-goals

Named so they are decisions rather than drift.

- **Gmail Postmaster Tools, the Admin SDK, Workspace Events, Apps Script / add-ons.** Each needs its
  own Discovery snapshot. The Admin SDK was scoped out here and picked up by
  `csa-google-workspace-audit` — a deliberate boundary between siblings, not an oversight.
- **Parity with Google's own servers.** They are the *floor*, measured at 22%, not the target.
- **Being a mail client.** No sync, no local store, no index. Fetch, check, answer.
- **Blocking or quarantining anything.** Analysis returns a verdict; acting on it is the operator's.

## How we would know this failed

1. **A message passes analysis and is still a phish.** The Mailchimp case is the regression test;
   the failure mode is a report that authenticates the domain and stays silent about the identity —
   which is precisely what SPF, DKIM and DMARC already do, and the reason this pillar exists.
2. **The coverage numbers are written by hand and drift.** Four different tool counts across the docs
   is already the observed failure in a sibling repo.
3. **The mutating surface arrives on by default**, because 76 of 117 is most of the API and gating
   it is more work than shipping it.
4. **Analysis is built and the library is not**, leaving the interesting pillar resting on no
   foundation.

## Who benefits

- **CSA** — mail and calendar become scriptable, and inbound mail becomes checkable by an agent
  rather than by a human noticing something is off, which is how the Mailchimp phish was caught.
- **The community** — public and free. The measured gap between Google's own MCP surface and its own
  API is useful to anyone deciding whether to build on the official servers.
- **The fleet** — this is the sibling where hostile content is the expected input rather than an
  edge case, so its injection handling is what the others inherit.
