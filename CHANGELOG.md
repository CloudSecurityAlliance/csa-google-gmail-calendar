# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.0] - 2026-09-27

### Changed

- **Creating a default directory now takes effect on the next call, with no restart.**

  The attachment policies are built once, when the server starts, and the same object is handed
  to every tool. So a default that was absent at startup stayed absent for the whole process
  lifetime - and the server went on refusing *after somebody did exactly what it told them to*,
  with a message naming a directory that by then existed. That is a worse failure than the
  original absence, because the remedy appears not to work.

  A defaulted root that was missing is now re-checked when a tool actually needs it. `mkdir
  ~/Documents/CSA-Outbox` and the next `send_message(attachments=[...])` works.

  **Still creates nothing.** This only looks again. An explicitly configured directory that does
  not exist still raises at construction, unchanged - that is a typo, and failing loudly at
  startup is the whole reason the check lives there.

- **Disjointness is re-asked when a directory is adopted late**, not assumed. The startup check
  ran while that root did not exist, so it proved nothing about it. A directory appearing later
  can collide with the other side - `CSA_GGC_DOWNLOAD_DIR=~/Documents` with the default outbox
  nested inside it is the ordinary way that happens - and adopting without checking would reopen
  the exact hole `check_directories_disjoint` exists to close, just later and more quietly. A
  colliding late directory is refused and says why; a pair that cannot be stat'd is not adopted,
  on the same "cannot prove it safe" rule the startup check already holds.

### Internal

- The overlap test is factored into one `_roots_overlap` used by both the startup check and late
  adoption, so the two cannot drift. They ask an identical question, and a second hand-written
  copy of `is_relative_to` plus `samefile` is precisely how a case-variant hole got into the
  shell script that mirrors this logic.

  Refactoring it introduced and then caught a real regression: running the test twice - once
  outside the `try` for nesting, once inside it for identity - moved the `samefile` stat out from
  under its handler, so a directory that vanished mid-check escaped as a bare `OSError` instead
  of the refusal this function promises. The existing test for that case failed immediately.

## [0.3.0] - 2026-09-26

### Changed

- **A default directory is never created.** 0.2.0 created a defaulted root that did not exist;
  this does not. If `~/Downloads` or `~/Documents/CSA-Outbox` is absent, that direction is
  **off**, the server **starts anyway**, and it **says which directory to make**.

  The defaults themselves are unchanged (`~/Downloads`, `~/Documents/CSA-Outbox`), and either
  variable still overrides.

  Creating the directory made the feature work by writing into somebody's home because a
  program started - a side effect nobody sanctioned, for a path nobody chose. **Safe by default
  beats working by surprise.** Three options existed for a missing default - create it, refuse
  to start, or leave the direction off - and only the third neither writes unbidden nor
  punishes someone for a path they never picked.

  It also gives the send side a better opt-in than any flag would. `~/Downloads` exists on
  essentially every machine, so receiving attachments works immediately. `~/Documents/CSA-Outbox`
  exists on none, so **sending a local file stays off until a person creates that directory** -
  and creating it is a deliberate act that means "outgoing attachments, from here". The more
  dangerous direction is the one that requires the gesture.

- **An explicitly configured directory that does not exist still raises**, unchanged. That is a
  typo, and failing loudly at startup is the whole reason the check lives in the constructor.
  The split is between a path the operator asserted and one this project picked.

- **The refusal no longer says "no attachment directory is configured" when one is.** With a
  default present-but-missing that sentence was false, and it sent the reader to set a variable
  when making a directory is the shorter fix. The refusal is now the same sentence as the
  startup warning, naming the path.

### Added

- `describe_configuration` reports `attachment_directory_note` and `download_directory_note` -
  *why* a directory is null, when there is a reason worth stating. `null` alone cannot
  distinguish "the default is missing" from "nothing is configured", and the remedies differ.

- A startup warning per unusable default, on stderr beside the existing ones. A feature being
  off is tolerable; a feature being off with no statement of why is not.

### Notes

The test that asserts the two defaults are disjoint now creates both directories first, on
purpose: a missing default yields `root is None`, and `check_directories_disjoint` returns early
on a `None` root - so without those mkdirs the test would have passed by never running the check
it exists to run.

## [0.2.0] - 2026-09-26

### Changed

- **Both attachment directories now have defaults, so attachments work on a fresh install.**
  Previously `CSA_GGC_ATTACH_DIR` and `CSA_GGC_DOWNLOAD_DIR` were unset by default, which meant
  both halves of the feature were off. That is spec §3's default posture applied to the
  filesystem and it is defensible; its practical effect was that nobody used attachments.

  | variable | default |
  |---|---|
  | `CSA_GGC_DOWNLOAD_DIR` | `~/Downloads` |
  | `CSA_GGC_ATTACH_DIR` | `~/Documents/CSA-Outbox` |

  **They are two different directories on purpose, and could not be one.**
  `check_directories_disjoint` refuses at server construction when the two roots are the same or
  one nests in the other, so defaulting both to `~/Downloads` would stop the server starting.
  The reason behind that check applies harder to a default than to a misconfiguration: a
  stranger emails you a file, you save it, and it is now inside the root `send_message` attaches
  from. Shipping that as the default would be worse than shipping it as a footgun, because
  nobody would have chosen it. So the send-side default is a CSA-specific directory that
  incoming mail never writes to - empty until a person puts something in it, which is exactly
  the property that makes it safe to read from by default. ([#23](https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/issues/23))

- **A defaulted root that does not exist is created; an explicitly configured one still is
  not.** The existing behaviour - refuse a missing root at construction, so a typo fails loudly
  at startup rather than as "file not found" on the first call - is kept exactly for paths an
  operator set. A path *this project* picked is different: absence just means first run, and
  failing someone over a directory they never chose would make the default worse than none.

- **The disjointness refusal now says which path you chose.** With defaults on both sides, a
  collision can involve a directory the reader never set (`CSA_GGC_DOWNLOAD_DIR=~/Documents`
  collides with the default attach root nested inside it). The message distinguishes
  `CSA_GGC_ATTACH_DIR (...)` from `the default for CSA_GGC_ATTACH_DIR (...)`, because otherwise
  the remedy looks like changing a variable that is not in the environment.

### Added

- `describe_configuration` reports `attachment_directory_is_default` and
  `download_directory_is_default`. Same reasoning as `client_project`: an operator should not
  have to infer which configuration is live, and the remedy differs - an unwanted default is
  changed by *setting* the variable, an unwanted explicit value by changing what it is set to.

- `tests/conftest.py` redirects `HOME`/`USERPROFILE` to a temporary directory for every test.
  Found the hard way: because a defaulted root is created, `cli.main([])` in `test_mcp_cli.py`
  went down the serve path and made `~/Documents/CSA-Outbox` **on the machine running the
  suite**. Per-test redirection would work and would have to be remembered by every future test
  touching the serve path, the config defaults or the token cache - so it is done once, for
  everything.

### Notes

On a managed Windows machine `~/Documents` may be OneDrive-redirected. The default still
resolves to a real directory, so it works; it does mean an outbox can sync, which the README now
says rather than leaving to be discovered.

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
