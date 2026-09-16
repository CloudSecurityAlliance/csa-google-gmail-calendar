# TODO — csa-google-gmail-calendar

Index of **all** open work, one line per item, per the CINO todo-index convention. Detail lives in
the linked file or GitHub issue; nothing else needs searching.

Created 2026-09-16.

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
  moving target and the captures are from 2026-09-01.

## Repo standards

- [ ] **Apply [`PUBLIC-GITHUB-REPO-STANDARDS.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/PUBLIC-GITHUB-REPO-STANDARDS.md)** once there is code to gate.
- [ ] **Decide vendored versus fetched Discovery snapshots**, as for the sibling repos.

## Open questions

- [ ] **Does a filter forward action require the destination to be pre-verified?** The allowlist
  design in `GOALS.md` assumes it does. Probe before relying on it.
