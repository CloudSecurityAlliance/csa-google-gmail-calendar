# TODO — csa-google-gmail-calendar

Index of **all** open work, one line per item, per the CINO todo-index convention. Detail lives in
the linked file or GitHub issue; nothing else needs searching.

Created 2026-09-16.

## Naming and disposal

- [x] **Name destruction as destruction** ✅ **2026-09-25** —
  [ADR-001](DECISIONS-ADR/ADR-001-a-tool-name-is-a-claim.md). Four tools, none called
  `delete_email`: `archive_email` (default disposal), `trash_email` / `untrash_email` (reversible),
  `delete_email_permanently` and `empty_trash` (both `mail.delete`, off by default, L0 per
  `AUTONOMY-POLICY.md`). **Archive is what "get rid of it" means**; trashing is asked for by name;
  permanent deletion is asked for by a human.
- [ ] **Check the rest of the vocabulary against the same rule before it is built.** ADR-001 fixes
  the instance the survey found. The rule it states — *a tool name is a claim made to a reader who
  cannot check it* — has not been applied to the other ~70 planned tools. The survey's other
  finding is the place to start: **none of the five surveyed surfaces warns a model before an
  irreversible action**, so there is no incumbent to copy here and no prior art to inherit.

## Build

- [ ] **The library, before any tool.** Typed client over Gmail v1 and Calendar v3 with the `Backend`
  seam and an offline fake. There is no `src/` yet.
- [ ] **The 32 unreached read methods**, security-relevant settings families first.
- [ ] **Message analysis to a verdict** — raw MIME, SPF/DKIM/DMARC/ARC, header-presentation sanity.
  The Mailchimp phish is the regression test.
- [ ] **The three gaps nobody reaches** — `calendar.calendars` (7), `calendar.acl` (7),
  `gmail.users.settings.filters` (4). Detail and the scope trap in [`GOALS.md`](GOALS.md).

## Drift

- [ ] **Schedule `scripts/coverage.py` and diff its output** —
  [CINO-PE #49](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/issues/49).
  It already regenerates the matrix from the Discovery snapshots; it needs a cadence and a diff.
- [ ] **Watch the official servers' tool lists too**, not just the APIs. The 22% coverage figure is a
  moving target. Last checked 2026-09-16: both tool lists unchanged, schemas moved — see the drift
  re-check in [`research/2026-09-01-mcp-server-landscape.md`](research/2026-09-01-mcp-server-landscape.md).
- [ ] **`apply_sensitive_*_label` may be on the way out.** As of 2026-09-16 Google's descriptions
  steer models to `trash_*` / `mark_*_spam` instead. `scripts/coverage.py` maps both onto
  `.trash` / `.modify`; re-check that mapping if the tools disappear.

## Repo standards

- [ ] **Apply [`PUBLIC-GITHUB-REPO-STANDARDS.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/PUBLIC-GITHUB-REPO-STANDARDS.md)** once there is code to gate.
- [ ] **Decide vendored versus fetched Discovery snapshots**, as for the sibling repos.

## Open questions

- [ ] **Does a filter forward action require the destination to be pre-verified?** The allowlist
  design in `GOALS.md` assumes it does. Probe before relying on it.
