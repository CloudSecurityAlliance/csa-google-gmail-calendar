# ADR-001: A tool name is a claim, so destruction is named as destruction

**Date:** 2026-09-25
**Status:** Accepted
**Applies to:** the Gmail message-disposal tools, and the naming rule generally

## Context

The community Gmail server this project surveyed exposes `delete_email`, which moves a message to
Trash, and **names permanent deletion nowhere**. The landscape survey recorded the consequence:

> A model reading that believes it destroyed something it did not, and cannot find the tool that
> would.

The failure runs in the direction that matters. A caller told *"deleted"* stops looking — so a
request to genuinely destroy something ends with the data still present and the requester told
otherwise. The opposite error, thinking a reversible action was irreversible, makes someone
cautious; this one makes them wrong and confident.

The capability model already separates these correctly: `mail.write` carries trash/untrash,
`mail.delete` carries the four permanent destroys and is **off by default**. What was never settled
is what the tools are *called* — and the capability boundary is invisible to a model choosing a
tool by its name.

## Decision

**Four names, each stating exactly what it does, and no name overlapping another's meaning.**

| Tool | Does | Reversible | Capability |
|---|---|---|---|
| `archive_email` | removes the `INBOX` label; the message stays in All Mail | fully | `mail.write` |
| `trash_email` | moves to Trash, where Google keeps it ~30 days | fully, via `untrash_email` | `mail.write` |
| `delete_email_permanently` | destroys one message; not recoverable | **no** | `mail.delete`, off by default |
| `empty_trash` | destroys everything currently in Trash | **no** | `mail.delete`, off by default |

**Archive is the default disposal.** A model asked to clear, tidy, file or "get rid of" mail
archives it. Trashing is a step a caller asks for by name, and permanent deletion is a step a
**human** asks for — `AUTONOMY-POLICY.md` puts *delete records* at **L0**, *"stays there unless a
very specific scoped ADR promotes"*, and this ADR does not promote it.

**No tool is called `delete_email`.** The name the incumbent uses is the one most likely to be
reached for out of habit, and its meaning there is wrong. Leaving it unused is deliberate: a caller
who types it gets a refusal naming the four above, which is a better outcome than a name that
silently means something adjacent.

## Rationale

The general rule this instance serves: **a tool name is a claim made to a reader who cannot check
it.** A model picks tools by name far more often than it reads descriptions, so a name that
overstates is not a documentation defect, it is a behavioural one.

This is the third instance the fleet has recorded of the same shape:

- `delete_email` claiming destruction it does not perform (this survey)
- `csa-zendesk`'s `auth_status` reporting a working credential as *expired*
- `csa-skilljar`'s `check_access` reporting a configuration as usable when no tool could be called

Each is a **diagnostic or disposal surface that lied**, and in every case the reader's next action
depended on the claim. `csa-zendesk#47` is the worked example of the cost: a wrong status sent
someone into an unnecessary login, which timed out, which left a dead callback link that surfaced
much later as an unrelated-looking browser error.

**Reversibility belongs in the name, not only in an annotation.** `destructive_hint` is real and
should be set, but a hint is read by a host, while the name is read by the model doing the choosing.

## Rejected alternatives

- **Keep `delete_email` for familiarity, with `permanent` as a flag.** Rejected: it puts the
  irreversible path one boolean away from the reversible one, and a flag is exactly what an injected
  instruction can set. The same reasoning that gave `csa-zendesk`'s `reply_publicly` no `public`
  parameter applies here — **a dangerous mode should be a different tool, not an argument**.
- **`delete_email` meaning permanent, matching the API method name.** Rejected as *unwarranted
  here*, not wrong: it is accurate and it collides with every reader's expectation formed by the
  incumbent, which is the specific confusion this ADR exists to remove.
- **Omit permanent deletion entirely.** Tempting, and rejected: a mailbox tool that cannot destroy
  is incomplete for the one use case that needs it most — a mistakenly received message containing
  data nobody should hold. Refusing by default is the right posture; refusing always is a different
  claim we cannot honestly make.
- **`empty_trash` only, with no single-message destroy.** Rejected: it forces the wide operation
  when the narrow one was wanted, which is the opposite of blast-radius discipline.
