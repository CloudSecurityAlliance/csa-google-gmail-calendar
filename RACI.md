# RACI

| Role | Who |
|---|---|
| **Responsible** | Kurt Seifried |
| **Accountable** | Kurt Seifried |
| **Consulted** | CSA staff using the server against their own mailboxes — as users, not a standing body |
| **Informed** | Anyone installing it; `security@cloudsecurityalliance.org` for anything in [`SECURITY.md`](SECURITY.md)'s scope |

One name in the first two rows is accurate rather than a placeholder. Naming a committee that
does not meet reads as answered, which is worse than reading as concentrated.

## What the concentration costs

The mitigations that exist, named because they are the answer:

- **Decisions are one file each, with their rejected alternatives** — [`DECISIONS-ADR/`](DECISIONS-ADR/).
- **Friction is recorded rather than remembered** — [`FRICTION.md`](FRICTION.md).
- **The exposure surface is a written inventory**, not a conversation —
  [`SECURITY-RESOURCES.md`](SECURITY-RESOURCES.md).

What stays genuinely concentrated:

- **The Google Cloud project.** Rotating or replacing the OAuth client is a Console operation one
  person currently knows, and it has a hard deadline when it happens: every member is locked out
  until the new client is distributed, and `authenticate` cannot fix it.
- **Which of the open findings matter most**, and in what order. [`TODO.md`](TODO.md) indexes
  them; the ranking is judgement.
- **The right retention for a download directory.** Nobody but the operator can decide it, and
  until [`BACKUP-RESOURCES.md`](BACKUP-RESOURCES.md) was written nobody had said the question
  existed.

## Escalation

- **A security issue** → GitHub Private Vulnerability Reporting, or
  `security@cloudsecurityalliance.org`. [`SECURITY.md`](SECURITY.md) is reachable by an external
  reporter with no CSA access, which is the property that matters for a public repo.
- **Anything else** → an issue on this repo. `report_a_problem` assembles a filable report with
  no message ids, no addresses and no credentials in it.
