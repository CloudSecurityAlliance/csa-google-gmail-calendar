# First live probe — 2026-09-26

Every offline verdict on this branch came from `FakeBackend`. This probe is the only thing that
can tell us the double was right about Google.

## Running it

```bash
export CSA_GGC_LIVE=1
export CSA_GGC_CLIENT_SECRETS=~/.csa_google_workspace/client_secret.json
.venv/bin/python experiments/2026-09-26-first-live-probe/probe.py
```

A browser opens for consent on first run. The token is written to
`~/.csa_google_gmail_calendar/token.json` at mode 0600.

## What it does to the account

**Self-addressed only.** It sends one message to the authenticated user with a small attachment,
creates one calendar event three days out, downloads the attachment it just sent, and then
**trashes the message and deletes the event**. Nothing is addressed to anyone else, and no address
from any argument is used as a recipient.

It creates its sandbox — attachment directory, download directory, a canary file and a symlink —
under `tempfile.mkdtemp()`, never in a real directory.

## Why the output is safe to paste into a public repo

Redaction is by construction, not by remembering. `_safe` is the only route a value takes into the
report, and it has two guards: anything address-shaped is replaced, and anything longer than 120
characters is truncated. The second is the guard against what nobody thought of — a probe detail
is a *shape* (`3 gaps, created hour excluded`), so prose-length text is content that arrived by
accident. Ids appear only as truncated SHA-256 prefixes: enough to correlate two lines, useless to
a reader.

Message bodies, subjects and recipient lists never leave the process.

## The nine checks

| # | Check | Why it cannot be done offline |
|---|---|---|
| 1 | granted scopes ⊆ requested scopes | only Google decides what the consent actually granted |
| 2 | no full-mailbox scope without `mail.delete` | same |
| 3 | a real body converts rather than coming back empty | real mail is HTML-only far more often than a fixture is |
| 4 | a symlink to a **real** file outside the root is refused | csa-zendesk's lesson: probing a control with a non-existent target cannot distinguish the control firing from a 404 |
| 5 | the legitimate file inside the root is still readable | a control that refuses everything passes every refusal test |
| 6 | a self-addressed message with an attachment arrives intact | round-trips MIME assembly through Google's own parser |
| 7 | a download lands in the download dir, not the attach dir | the cross-task defect the whole-branch review found |
| 8 | an event is created and reads back | `FakeBackend` cannot tell us Google accepted the body |
| 9 | `find_free_time` excludes the busy hour and names an unreadable calendar | the all-unreadable disclosure is the one control whose limit is advisory |

## Still not covered

Check 9 proves the data is *present*. Whether a **model reads it** before proposing a time is the
part no offline or scripted test can reach — that needs a model in the loop, and it is the
honest limit of this control.

Responding to an invitation from *another* organiser is also untested here: it needs a second
account, and the `self`-attendee marker and self-invite refusal are exactly what it would exercise.
