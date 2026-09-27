# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.1] - 2026-09-26

### Fixed

- **`authenticate` could not be called at all.** It is the only tool here with a `Context`
  parameter (it needs `ctx.elicit_url` to hand the consent URL to the client), and every
  in-band call was refused with `does not accept unknown argument(s): ctx`. In-band
  authorization was therefore dead in 0.1.0, leaving `csa-google-gmail-calendar login` in a
  terminal as the only way to authorize.

  `_refuse_unknown_arguments` computes its accepted set from the JSON schema, and the SDK
  strips a `Context` parameter from that schema because no client supplies one — then injects
  `ctx` as a keyword argument at call time. The guard judged the SDK's own injection against a
  schema that by construction could never contain it. Injected names are now derived by
  subtraction from the raw signature and exempted, so a future injected parameter type is
  covered without editing the guard. Nothing is weakened: a client cannot supply a name that
  is absent from the wire schema.

  Not caught by 688 tests at 100% coverage because every test reaches a tool through
  `get_tool(name).fn(...)` and passes `ctx` **positionally**, while the guard inspected only
  `kwargs` — the one calling convention the real transport uses was the one no test used.
  Four regression tests, including one through `ToolManager.call_tool`, the real path.
  ([#19](https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/pull/19))

## [0.1.0] - 2026-09-26

First release. 46 tools, 686 tests at 100% coverage, and **9/9 checks against a real
Google account** — scope minimisation, HTML-only body conversion, the attachment allowlist
refusing a real symlink escape, a self-addressed send with an attachment, and
`find_free_time` naming a calendar it could not read rather than reporting it free.

### Added

- The configuration surface: `describe_configuration` (enabled capabilities, granted vs.
  required OAuth scopes, the active flavour and exactly what it hides, the configured
  attachment directory's path), `demonstration_plan` (an ordered walkthrough of every
  registered tool that writes only to the authenticated user's own mailbox and a scratch
  calendar event, never an address from an argument), and `report_a_problem` (a filable bug
  report containing no message/thread/event ids and no credentials).
- `list_history`, `get_profile`, and `whoami` - the two remaining spec §5 "Keeping up" tools
  plus a narrow, address-only convenience over the same Gmail profile call.
- `CSA_GGC_FLAVOUR=core|google|full`: restricts which tools are *registered* on top of whatever
  `CSA_GGC_CAPABILITIES` already allows. `core` is spec §5's 31-tool minimum; `google` is a
  conservative literal-name match against what Google's own official servers publish; `full`
  (default) restricts nothing further.
- CI (`.github/workflows/ci.yml`): lint (`ruff check`), type-check (`mypy`), the unit suite
  (`pytest --cov --cov-fail-under=90`) across Python 3.10-3.14, and a security gate
  (`pip-audit` + `bandit`).
- `release.yml`: PyPI Trusted Publishing (OIDC) with PEP 740 attestations, split into a
  credential-free `build` job and a minimal `publish` job that runs no project code.
- `FakeBackend.list_history` now has a seedable `history` store, so a change being reported is
  a testable branch, not only "nothing changed" (deferred from task 3).

### Fixed

- `bandit -r src` false positives on constant names/paths that merely contain the substring
  "token" (`B105`) and on two import-time sanity-check `assert`s (`B101`), suppressed inline
  with a reason - the first time this repository's dependency-security gate has been run in CI.

## [0.1.0] - 2026-09-25

Initial implementation: `Backend`/`PolicyBackend`/`Policy` seam, Gmail read/compose/send with
local-path attachments, Calendar events and invitation responses, the OAuth lifecycle
(`authenticate`/`auth_status`/`logout`, plus the `login` CLI command), and an offline test suite
against `FakeBackend`. See `docs/superpowers/specs/2026-09-01-csa-google-gmail-calendar-design.md`
for the design this implements.
