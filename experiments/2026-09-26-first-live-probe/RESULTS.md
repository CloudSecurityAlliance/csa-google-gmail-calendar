# First live probe — RESULTS

**9 of 9 checks passed** against a real Google Workspace account, project
`csa-gmail-calendar-mcp`, 2026-09-26.

Every prior verdict on this branch came from `FakeBackend`. This is the first evidence that the
double was right about Google.

## What passed

| # | Check | What it establishes |
|---|---|---|
| 1 | granted scopes ⊆ requested | **6 scopes, none beyond the 6 requested.** Scope minimisation is real, not a claim in a docstring |
| 2 | no full-mailbox scope | `https://mail.google.com/` absent, `mail.delete` being off by default |
| 3 | real bodies convert | **10 messages: 2 HTML-sourced**, 8 plain, 2 disclosed a transformation |
| 4 | symlink to a real outside file | refused **on the resolved path**, not the supplied name |
| 5 | legitimate in-root file | still readable — the control is not simply refusing everything |
| 6 | self-addressed send with attachment | delivered, attachment intact, read back |
| 7 | download destination | landed in the download dir; **attach dir untouched** |
| 8 | event created | reads back with the times set |
| 9 | `find_free_time` | created hour excluded; **1 calendar named unreadable** rather than reported free |

## What only live testing could establish

**Two of ten recent messages were HTML-only.** A fifth of real mail, in a sample of ten. Before
the MIME-walk work those bodies came back empty — and no fixture would have told us the ratio.

**Check 7 is the cross-task vulnerability**, verified against Google rather than a double. A
stranger's attachment filename could previously overwrite a file in the directory outgoing mail
attaches *from*, so a later send would put attacker bytes on the wire under the user's name. It
was found by the whole-branch review because each half was correct alone.

**Check 9 is the one control whose limit is advisory.** `find_free_time` names a calendar it could
not read instead of silently treating it as free. It does the right thing with a real API's error
shape; whether a *model* reads that field before proposing a time remains untestable here.

## Two probe bugs it found in itself

Both mine, both fixed, and the first is the more interesting:

- **The probe defaulted to a sibling server's OAuth client.** Consent *succeeded* — valid client,
  real account, six scopes — and failed only on the first API call. It also left a Gmail grant on
  the Drive app, revoked by hand. The probe now refuses a client whose project does not look like
  its own, and the servers name their project in `auth_status`, `describe_configuration` and the
  `login` output. Written up as `insights/a-credential-should-say-what-it-is.md` in
  CINO-Platform-Engineering.
- A wrong `Calendar.create` signature, which cascaded into a false failure on check 9.

## Still not covered

Responding to an invitation from **another organiser** — it needs a second account, and it is
exactly what the `self`-attendee matching and the self-invite refusal guard. And whether a model
reads `unreadable_calendars` before proposing a time.

## Redaction

Redacted by construction, not by care: `_safe` is the only route any value takes into this report.
It refuses address-shaped strings and truncates anything over 120 characters, on the reasoning that
a probe detail is a *shape* and prose-length text is content that arrived by accident. Ids appear
as truncated SHA-256 prefixes. No message body, subject or recipient list left the process.
