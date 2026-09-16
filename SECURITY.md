# Security

## Reporting a vulnerability

Open a [GitHub issue](https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/issues) for
anything that is not itself sensitive. For a finding that should not be public before it is fixed,
email **security@cloudsecurityalliance.org** with `csa-google-gmail-calendar` in the subject.

We will acknowledge within five working days. There is no bounty.

## What is in scope

There is no `src/` yet — this repository currently holds the API inventory, the coverage analysis and
the design. Security-relevant findings today are:

- **An error in the read/write or scope classification.** A method marked safe that is not is the most
  valuable finding here.
- **A design in `GOALS.md` that does not hold**, particularly the filter-destination allowlist and the
  assumption that a filter forward action requires a pre-verified destination.

Once there is code, the whole surface is in scope, and the message-analysis pillar especially: **a
message that passes analysis and is still a phish is a security defect, not a feature request.**

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
