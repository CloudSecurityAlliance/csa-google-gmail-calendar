# Business Case

## Executive summary

Google ships its own MCP servers for Gmail and Calendar. Measured against the APIs they front, they
reach **21 of 117 methods — 22%**. The Anthropic connector adds five and is otherwise the same
implementation. Ninety-one methods are reached by neither, and thirty-two of those are reads.

The missing 78% is not a long tail of obscurity. It is **every Gmail settings family** — filters,
forwarding addresses, delegates, send-as, S/MIME, client-side encryption — and **calendar
administration**: you can create, update and delete an *event*, but you cannot create or delete a
*calendar*, and no surface touches calendar ACLs at all.

This project closes that gap, and adds the thing no official server has any reason to build: a
**check on the message itself** — authentication, header-presentation sanity, and prompt-injection
detection — because a server that fetches mail faithfully and says nothing about it hands hostile
content to a model as though it were trustworthy.

**Benefit categories:** Synergy (primary) · Required / Compliance (primary) · Brand (secondary) ·
Research / Exploration (secondary)

## CSA value

**Mail and calendar become scriptable rather than clickable.** Scheduling, triage, and the
mail-derived work that currently happens by hand become things an agent can do.

**Inbound mail becomes checkable.** A phish impersonating Mailchimp, with a `Reply-To` on an
unrelated free Google account, reached a CSA inbox and **would have passed SPF, DKIM and DMARC** —
those authenticate the sending domain, not the claimed identity. It was caught because a human
noticed. That is not a repeatable control.

**It is the capability layer a future mailbox audit needs.** `delegates.list` requires domain-wide
delegation, so nobody can enumerate who else reads a mailbox using that mailbox's own token —
delegation is invisible from the credential the victim holds.

## Why not the alternatives

| Alternative | Why not |
|---|---|
| **Google's own Gmail / Calendar MCP servers** | 22% of their own API. Every settings family absent; no calendar administration; the Gmail server cannot even download an attachment it returns an id for |
| **The Anthropic connector** | The same implementation, plus five methods. Not a second option |
| **Third-party Gmail MCP servers** | Surveyed in `research/2026-09-01-mcp-server-landscape.md`. None reach the settings families either, and none check the message |
| **Writing scripts against the API directly** | Works, and is what we would do without this — which is the point. The library is the product; the server is delivery. Direct scripting gets the capability and none of the safety layer |

## The security component is half the product

The capability gap is the reason to start. The reason to *finish* is that mail is the surface where
hostile content is expected rather than incidental:

- **Message analysis returns a verdict, not a dump** — SPF/DKIM/DMARC/ARC, plus the header-presentation
  checks those three do not cover: `Reply-To` versus `From`, brand display names, lookalike domains.
  The Mailchimp phish is the regression test and must not come back clean.
- **Prompt-injection detection is a returned field**, not a filter. The sibling servers treat this as
  an open research question; here it is a feature, because the input is adversarial by default.
- **A configured destination allowlist on filter actions, with a loud refusal.** It does not bind the
  credential — anyone with the token bypasses this server entirely. It binds the agent, which is the
  realistic attacker, and a refused forward is a logged, attributable event. An attacker using the
  raw token produces no signal at all.

Google has no incentive to tell you that a message which passed authentication is still lying to you.
That asymmetry is durable, which is why this half of the project does not shrink when the first half
does.

## Operational burden

Low. A library and a local stdio server; no deployment, no stored mail, no index. The recurring cost
is watching the upstream surface — both Google's APIs and the official servers' coverage, since the
22% figure is a moving target — which `scripts/coverage.py` already computes and mostly needs
scheduling.

## AI enablement

The project is for agents first: the tool surface is the contract, and the message-analysis pillar
exists specifically because an agent reading mail cannot apply the judgement a human applies to a
suspicious sender. It also raises the floor for every other CSA AI workflow that touches mail.
