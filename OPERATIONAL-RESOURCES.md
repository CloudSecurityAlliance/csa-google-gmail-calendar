# Operational resources

Nothing is hosted. Measured: 36 source files, **no listener** — no `uvicorn`, no `bind()`, no
`socket.socket`. The server is a local stdio process launched by an MCP client, so there is no
uptime, no rota and nothing to page.

The resources below are **dependencies whose change breaks this project**, which is the same
operational question asked about a different kind of thing. Each row says what its change looks
like, because every one of them fails quietly rather than loudly.

| Resource | What depends on it | What its change looks like |
|---|---|---|
| **Gmail + Calendar REST APIs** | Every tool. Egress only | A field or scope moves and tools fail at the seam. Nothing here watches upstream — CINO-Platform-Engineering#49 is the fleet-level version of that gap |
| **Google Cloud project `csa-gmail-calendar-mcp`** | The OAuth client every install uses | Delete or unpublish it and **every cached token stops refreshing** — and the failure arrives as "could not refresh cached credentials", naming nothing. The sibling lived this: csa-google-workspace#495, #510 |
| **The consent screen's name** | What a member sees when authorising | It is a user-visible claim about who is asking. Changing it changes what they are agreeing to |
| **PyPI + Trusted Publishing (OIDC)** | Installation. No long-lived token; attestations required | The supply-chain surface |
| **GitHub + branch protection** | That `main` means something. This repo is **public** | A mistake here is published, not merely committed — there is no window in which it is recoverable |
| **`CSA_GGC_DOWNLOAD_DIR` on each operator's machine** | Where mailbox attachments land | Not ours to manage, and it accumulates other people's email content. See [`BACKUP-RESOURCES.md`](BACKUP-RESOURCES.md) |

## The recurring work

Reactive by design; there is no schedule. Three standing triggers:

1. **Google changes a scope or deprecates an endpoint.** The only detector is a failing call.
2. **A credential or project is rotated.** This has a hard deadline attached, because every
   member is locked out until the new client is distributed — and `authenticate` cannot fix it,
   only replacing the client can.
3. **A fleet decision this repo cites is superseded.** `DECISIONS-ADR/` entries reference
   CINO-Platform-Engineering decisions by number, and a superseded one leaves a citation
   asserting a standard that no longer holds. Nothing checks this.

## Coverage is gated in CI and not locally, which is deliberate

`RELEASING.md`'s local command passes `--cov-fail-under=0`; CI enforces **100** on ubuntu. That
is not a relaxation — `auth.py` keys on `_WINDOWS`, so 100% is reachable by the **union** of
platforms and by neither alone, and a local threshold is therefore either weaker than CI's or
permanently red (#32).

**The Windows job measures no coverage at all**, which means the `icacls` paths are pragma'd on
ubuntu and unmeasured on Windows. That is CINO-Platform-Engineering#172, where the measurement
and the three options live.

## Not here

Capacity, alerting, dashboards, on-call, cost. There is nothing running. The only state is on
each operator's machine and belongs to them — [`BACKUP-RESOURCES.md`](BACKUP-RESOURCES.md).
