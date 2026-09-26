# Security

## Reporting a vulnerability

Open a [GitHub issue](https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/issues) for
anything that is not itself sensitive. For a finding that should not be public before it is fixed,
email **security@cloudsecurityalliance.org** with `csa-google-gmail-calendar` in the subject.

We will acknowledge within five working days. There is no bounty.

## What is in scope

The library and MCP server in `src/` are implemented (46 tools; only a live probe against a real
Google account remains before this reads "battle-tested"). The whole surface is in scope,
particularly:

- **An error in the read/write or capability/scope classification** (`policy.py`,
  `_capabilities.py`) - a method or tool reachable under a capability it should not need is the
  most valuable finding here.
- **A refusal that discloses more than it should**, or a `describe_configuration`/
  `report_a_problem` output that leaks a token, a credential, or message/thread/event content
  where a path or a shape was meant instead.
- **A design in `GOALS.md` that does not hold**, particularly the filter-destination allowlist and
  the assumption that a filter forward action requires a pre-verified destination - still relevant
  once the settings-family tools it describes are built.

The message-analysis pillar especially: **a message that passes analysis and is still a phish is
a security defect, not a feature request.**

## The unusual property of this project

Its input is adversarial by design. Every other server in the CSA MCP fleet reads content that is
merely untrusted; this one reads **mail**, where an attacker chooses the content precisely in order
to be read by whoever opens it — now including a model.

Two consequences shape the code:

- **Message content reaching a model is attacker-controlled**, so prompt-injection detection is a
  returned field rather than a filter, and analysis never renders message content as though it were
  the system's own words.
- **Filter actions carry a configured destination allowlist and a loud refusal.** This does not bind
  the credential — anyone holding the token bypasses this server. It binds the agent, which is the
  realistic attacker, and the refusal is the only evidence that will ever be produced.

## Deliberate limitations

Ten Gmail methods — `updateAutoForwarding`, `delegates.*`, `forwardingAddresses.create/delete`,
`sendAs.create/delete/verify` — cannot be called with user OAuth at all and are out of reach here by
construction.

That includes `delegates.list`, so **this server cannot enumerate who else can read a mailbox using
that mailbox's own token.** Delegation is invisible from the credential the victim holds. Auditing it
needs domain-wide delegation and belongs to a separate project.
