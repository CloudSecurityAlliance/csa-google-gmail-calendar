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

## First implementation

- [ ] **Execute [the first-implementation plan](docs/superpowers/plans/2026-09-25-gmail-calendar-first-implementation.md)** — 14 tasks: Gmail read/compose/send with attachments, Calendar events and invitation responses. Scope decided 2026-09-25: allowlisted local attachment paths, direct send allowed, no filters or settings.
- [ ] **[#8](https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/issues/8) Attachments have no upload endpoint** — resolved in the plan as `CSA_GGC_ATTACH_DIR` (Task 5/6); close once those land.
- [x] **[#9](https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/issues/9) There is no receive** ✅ **2026-09-26 (partial)** — `users.watch` stays omitted deliberately (needs a Cloud project, a Pub/Sub topic, a public HTTPS endpoint this stdio server cannot offer). `list_history`'s statefulness is resolved: `FakeBackend` now has a seedable `history` store (task 13), so "something changed" is a tested branch, not only "nothing did" — see `backend.py`'s `list_history` and `tests/test_backend_contract.py`. Still open: nothing in this codebase automatically APPENDS a history record when a message/label changes (a caller seeds `history=` directly); a full incremental-sync simulation is future work, not required for the tool to be correct or tested.
- [ ] **[#11](https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/issues/11) `narrowest_scope` sorts by string length** — fixed by the lattice in Task 1; regenerate the inventory and coverage matrix in the same commit.
- [x] **Repo-wide `pytest --cov` gate** ✅ **2026-09-26** — was ~65% mid-plan (Task 4), now 91%+ against `fail_under = 90` (Task 13). `_auth_flow.py`/`_login.py`'s genuinely-interactive lines (a real browser, a real WSGI redirect body) are `# pragma: no cover` with a comment naming what covers them (Task 14's gated live suite); everything else in both files now has real unit tests, not a lowered gate.
- [ ] **Task 14: the gated live probe against a real Google account** — the one remaining task in
  [the first-implementation plan](docs/superpowers/plans/2026-09-25-gmail-calendar-first-implementation.md).
  Everything else (library, MCP server, config/demo/CI surface) is implemented and unit-tested
  against `FakeBackend`.
- [ ] **There is no `delete_label`/`update_label` tool** — `Backend`'s own Protocol has neither
  method. A label `create_label` makes (including the one `demonstration_plan` creates for its own
  demo run, uniquely named per run to stay safe to repeat) cannot be removed or renamed through
  this server. Spec's own "copy" section lists `update_label`/`delete_label` among the tools this
  project intends to ship; neither has landed yet.
- [ ] **`google` flavour is a conservative, literal tool-NAME match** against
  `research/captures/2026-09-01-*.json`, not a semantic one (`_flavours.py`'s own module
  docstring has the full reasoning). It under-represents Google's real published surface where
  this project's naming differs for the same operation (`reschedule_event` vs. their
  `update_event`, `find_free_time` vs. their `suggest_time`, and every tool ADR-001 expanded past
  what their surface names). Revisit if `google` needs to be exact rather than conservative.

## Repo standards

- [x] **Apply [`PUBLIC-GITHUB-REPO-STANDARDS.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/PUBLIC-GITHUB-REPO-STANDARDS.md)** ✅ **2026-09-26** —
  `.github/workflows/ci.yml` (lint/type/test matrix 3.10-3.14/security, SHA-pinned actions) and
  `release.yml` (Trusted Publishing + PEP 740 attestations, credential-free `build` job,
  minimal `publish` job) both added; `RELEASING.md`/`CHANGELOG.md` written. **Still open:**
  branch protection and the PyPI pending publisher are GitHub/PyPI-side, one-time, operator
  setup (`RELEASING.md`'s own "One-time setup" section) — not something a commit to this repo
  can configure.
- [ ] **Decide vendored versus fetched Discovery snapshots**, as for the sibling repos.

## Open questions

- [ ] **Does a filter forward action require the destination to be pre-verified?** The allowlist
  design in `GOALS.md` assumes it does. Probe before relying on it.
