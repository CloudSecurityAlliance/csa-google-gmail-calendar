# Security Resources

The security surface of this project: what it exposes, to whom, and how it is protected.

**Last reviewed:** 2026-09-16 · **Next review:** 2026-12-16

## Summary

**No network surface.** A Python library and a local stdio MCP server, run by an operator on their own
machine. Nothing listens, nothing is deployed. The exposure is a **credential**, a **published
artifact**, and — uniquely in this fleet — an **adversarial input stream**.

That last one is why this file is not a formality. Every sibling reads untrusted content. This one
reads mail, where the content is chosen by an attacker specifically to be acted on by the reader.

## Exposure surface inventory

| Surface | Type | Exposure tier | Cloudflare | Auth | Notes |
|---|---|---|---|---|---|
| `github.com/CloudSecurityAlliance/csa-google-gmail-calendar` | public repo | `public-unauthed` | n/a | none | Analysis and design. No credentials, no CSA identifiers |
| Local stdio MCP server | process on an operator's machine | `internal-staff` | n/a | inherits the operator's shell | Not network-reachable. No listener |
| PyPI package | published artifact | `public-unauthed` | n/a | n/a | **Not yet published.** When it is, `PUBLIC-GITHUB-REPO-STANDARDS.md` applies |
| Gmail / Calendar APIs | outbound only | n/a | n/a | per-user OAuth | Acts as the operating user, so Google's own ACLs are the ceiling |
| **Message content** | **inbound data** | n/a | n/a | n/a | **Attacker-controlled. The real surface** |

**Cloudflare is not applicable** to any row — no row is a CSA-operated inbound network surface. That
is a conclusion, not an omission.

## Access model

Per-user OAuth: the server acts as the operating user, so every call runs under that user's own ACLs
and Google enforces the real ceiling. A failure of our capability layer exposes the user to their own
access, which is recoverable — unlike the sibling `csa-google-workspace-audit`, which holds a
domain-wide-delegation credential with no ceiling beneath it.

Ten methods requiring domain-wide delegation are unreachable here by construction, including
`updateAutoForwarding` and `forwardingAddresses.create`. The residual write worth constraining is a
**filter that forwards to an already-verified address and archives the original** — the canonical
mailbox-persistence mechanism after an account compromise — which the configured destination
allowlist exists to refuse and to alert on.

## Data classification

**Nothing is stored.** No cache, no index, no local mail store — fetch, check, answer. There is
therefore no `DATA-RESOURCES.md`; that is an explicit N/A.

Message bodies and calendar contents are **PII-bearing and pass through memory in transit**. Two
rules follow, and the second inverts an instinct:

- Analysis output reports on *the operation*, never reproduces *the content*, when raising log detail.
- Debug logging of message content is a **persistence step for an injection payload** into a client
  cache directory we cannot see or purge, under the client's retention policy rather than ours.

## Known gaps and accepted risks

| Gap | Status | Owner |
|---|---|---|
| **Nothing watches the upstream surface.** Coverage captured 2026-09-01; `scripts/coverage.py` computes the matrix but is not scheduled | Open — [CINO-PE #49](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/issues/49) | Kurt Seifried |
| **The allowlist design rests on an unprobed assumption** — that a filter forward requires a pre-verified destination | Open — tracked in `TODO.md` | Kurt Seifried |
| **Delegation is undetectable from the user's own credential.** `delegates.list` needs DWD, so this server cannot report who else reads a mailbox | Accepted — belongs to a future `csa-google-gmail-audit` | Kurt Seifried |
| **`PUBLIC-GITHUB-REPO-STANDARDS.md` unapplied** — no branch protection, no required CI gates, no CI | Open — tracked in `TODO.md` | Kurt Seifried |

## Review schedule

Reviewed 2026-09-16. Next review 2026-12-16, or **immediately on first release**, whichever is
sooner — publishing to PyPI changes the artifact row from "not yet published" to a live supply-chain
surface.
