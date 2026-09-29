# CSA-Uploads Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the attachment (send-side) default from `~/Documents/CSA-Outbox` to `~/CSA-Uploads`, so the documented path is one a Windows user can actually reach.

**Architecture:** One constant changes. Nothing else in the server gains logic — no Known Folder lookup, no detection, no fallback, no migration. The server stays a server; the installer (a separate repo, a separate plan) resolves and pins real paths. Every other edit in this plan is a comment, a help string, a doc, or a test that named the old path.

**Tech Stack:** Python 3.10+, pytest, ruff, mypy. No new dependencies.

**Spec:** `CINO-Platform-Engineering/docs/superpowers/specs/2026-09-29-windows-path-and-messaging-parity-design.md` (decisions D1–D6; this plan implements A1 only)

## Global Constraints

- **The server must not gain platform-detection code.** No `os.name` / `sys.platform` branch, no Known Folder API, no `%USERPROFILE%` handling. `expanduser` stays exactly as it is. (Spec D1)
- **The server must never create the attachment directory.** Its existence *is* the opt-in to attaching. (Spec D2)
- **`check_directories_disjoint` must not be weakened or modified.** The new layout is chosen to satisfy it as written; `~/CSA-Uploads` and `~/Downloads` are siblings. (Spec D3)
- **`DEFAULT_DOWNLOAD_DIR` does not change.** It stays `~/Downloads`. (Spec D4)
- New default string, exactly: `~/CSA-Uploads`
- Directory name, exactly: `CSA-Uploads` (capital C, S, A, capital U, one hyphen)
- Coverage gate is 100% on ubuntu; the Windows job gates test outcomes only. Both must stay green.

---

## File Structure

| file | responsibility | change |
|---|---|---|
| `src/csa_google_gmail_calendar/_attachments.py` | the two defaults and the policies that use them | constant + 3 comment blocks |
| `src/csa_google_gmail_calendar/mcp/cli.py` | `--help` text for the attachment flag | 1 help string |
| `tests/test_attachment_dir_defaults.py` | what the defaults are and when they are adopted | 2 paths that go through `from_env()` |
| `tests/test_attachment_allowlist.py` | allowlist behaviour against the default root | 1 path that goes through `from_env()` |
| `tests/conftest.py` | the home-isolation fixture's docstring | 1 prose mention |
| `README.md` | the env var table | 1 row |
| `CHANGELOG.md` | the release entry | new section |

Two tests construct `AttachmentPolicy` with an explicit path rather than reading the default
(`test_attachment_dir_defaults.py` lines ~158 and ~198). Those are testing *nesting* and *late
adoption* generally, not the default, so their paths are arbitrary and **must not** be changed
— changing them would make them look like default tests and hide that they cover something
else.

---

### Task 1: The default, and everything in the server that names it

**Files:**
- Modify: `src/csa_google_gmail_calendar/_attachments.py:95-116` (comment block + constant)
- Modify: `src/csa_google_gmail_calendar/_attachments.py:139` (comment)
- Modify: `src/csa_google_gmail_calendar/_attachments.py:410` (docstring)
- Modify: `src/csa_google_gmail_calendar/mcp/cli.py:120` (help text)
- Test: `tests/test_attachment_dir_defaults.py:42`, `tests/test_attachment_allowlist.py:104`
- Test: `tests/conftest.py:6` (docstring only)

**Interfaces:**
- Consumes: nothing — this is the first task.
- Produces: `_attachments.DEFAULT_ATTACH_DIR == "~/CSA-Uploads"`. Later tasks and the
  CSA-Plugins plan rely on that exact string.

- [ ] **Step 1: Change the tests that go through `from_env()` so they fail**

These two read the default. Change the directory they create.

In `tests/test_attachment_dir_defaults.py`, around line 42:

```python
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        (tmp_path / "CSA-Uploads").mkdir()
        (tmp_path / "Downloads").mkdir()
```

In `tests/test_attachment_allowlist.py`, around line 104:

```python
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    outbox = tmp_path / "CSA-Uploads"
    outbox.mkdir()
```

Note `mkdir()` without `parents=True` in both — the point of the new layout is that there is
no parent directory to create.

- [ ] **Step 2: Run them and verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_attachment_dir_defaults.py tests/test_attachment_allowlist.py -q
```

Expected: failures reporting the attachment root is `None` / "does not exist", because the
server still defaults to `~/Documents/CSA-Outbox` and nothing created it.

- [ ] **Step 3: Change the constant**

`src/csa_google_gmail_calendar/_attachments.py`, replacing the constant on line 116:

```python
DEFAULT_ATTACH_DIR = "~/CSA-Uploads"
```

- [ ] **Step 4: Rewrite the comment block above it**

Replace the paragraph beginning `# `~/Documents` may be OneDrive-redirected` (the last
paragraph before the constants) with:

```python
# It sits beside `~/Downloads`, not inside it, and not under `~/Documents`. Both halves are
# deliberate. Inside `~/Downloads` is the configuration `check_directories_disjoint` refuses
# outright, so it could never have been the default. Under `~/Documents` was the default until
# 2026-09-29, and it was wrong on Windows in a way nothing reported: OneDrive's Known Folder
# Move redirects Documents by default, `os.path.expanduser` does not know that and string-joins
# `$HOME/Documents` anyway, and both paths exist - so a person told to create the directory
# made it in the Documents they could see while the server read one they could not. The home
# root is redirected by nothing, on any platform, which is the whole reason it was chosen.
```

- [ ] **Step 5: Fix the remaining two references in the same file**

Line ~139, inside the comment explaining why the two defaults differ:

```python
# every machine, so receiving works immediately. `~/CSA-Uploads` exists on none, so
```

Line ~410, in the `_adopt_pending_default` docstring:

```python
    Called from `resolve()`, so `mkdir ~/CSA-Uploads` takes effect on the next tool
```

- [ ] **Step 6: Fix the CLI help text**

`src/csa_google_gmail_calendar/mcp/cli.py`, line ~120:

```python
                         (default: ~/CSA-Uploads). NEVER created - if the default
```

- [ ] **Step 7: Fix the conftest docstring**

`tests/conftest.py`, line ~6 — prose only, no behaviour:

```python
`test_mcp_cli.py` goes down the serve path and made `~/Documents/CSA-Outbox` on the machine
```

becomes

```python
`test_mcp_cli.py` goes down the serve path and made a directory under `~` on the machine
```

- [ ] **Step 8: Run the full suite and the gates**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy
```

Expected: all pass. If any test still references `CSA-Outbox`, grep for it:
`grep -rn "CSA-Outbox" src tests` should return nothing except the two deliberate
arbitrary-path tests noted in File Structure — verify those two are the nesting and
late-adoption cases before leaving them.

- [ ] **Step 9: Commit**

```bash
git add src tests
git commit -m "fix(windows): default the uploads directory to ~/CSA-Uploads

~/Documents/CSA-Outbox was wrong on Windows in a way nothing reported.
OneDrive Known Folder Move redirects Documents by default; expanduser does
not know that and string-joins \$HOME/Documents anyway. Both paths exist, so
a person told to create the directory made it in the Documents they could
see while the server read one they could not - and attaching stayed off with
attachment_directory_note: null.

~/CSA-Uploads sits beside ~/Downloads rather than inside it, which
check_directories_disjoint would refuse, and under a home root that nothing
redirects on any platform. The server gains no detection code for this: the
path question moves to the installer, which resolves and pins real paths.

Refs the windows-path-and-messaging-parity spec, A1."
```

---

### Task 2: The user-facing documentation

**Files:**
- Modify: `README.md:488` (the `CSA_GGC_ATTACH_DIR` row)
- Modify: `CHANGELOG.md` (new entry at the top)

**Interfaces:**
- Consumes: `DEFAULT_ATTACH_DIR == "~/CSA-Uploads"` from Task 1.
- Produces: nothing code depends on.

- [ ] **Step 1: Verify the doc-drift test covers this**

```bash
.venv/Scripts/python.exe -m pytest tests/test_docs_drift.py -q
```

This suite asserts every env var the code reads is in the README and vice versa. It checks
*presence*, not the default value — so it will stay green through this task. That is worth
knowing rather than assuming: the README default column is **not** guarded by a test, which is
why Step 2 has to be done by hand and checked by eye.

- [ ] **Step 2: Update the README row**

In the env var table, the `CSA_GGC_ATTACH_DIR` row's default column changes from
`~/Documents/CSA-Outbox` to `~/CSA-Uploads`, and the sentence about OneDrive redirection is
deleted rather than reworded — it described a syncing preference, and the new location has
neither the sync nor the divergence. The rest of the row (never created, attaching is off
until it exists, takes effect without a restart) is unchanged and still correct.

- [ ] **Step 3: Add the CHANGELOG entry**

At the top, above the most recent released section:

```markdown
## [Unreleased]

### Changed
- **The uploads directory default moved to `~/CSA-Uploads`**, from
  `~/Documents/CSA-Outbox`. On Windows the old default was wrong in a way nothing
  reported: OneDrive's Known Folder Move redirects Documents by default,
  `os.path.expanduser` string-joins `$HOME/Documents` regardless, and both paths
  exist — so somebody told to create the outbox created it in the Documents they
  could see, while the server read one they could not, and attaching stayed off.

  The new location sits beside `~/Downloads` rather than inside it — inside is the
  configuration `check_directories_disjoint` refuses — and under a home root that
  nothing redirects on any platform.

  **If you have files in `~/Documents/CSA-Outbox`, move them to `~/CSA-Uploads`.**
  Nothing migrates them: the server still never creates or moves this directory,
  because its existence is what turns attaching on. If attaching goes quiet after
  this upgrade, that is why, and the startup message names the new path.
```

- [ ] **Step 4: Verify nothing else names the old path**

```bash
grep -rn "CSA-Outbox" README.md src tests
```

Expected: only the two arbitrary-path tests from File Structure. `CHANGELOG.md` keeps its
historical mentions — those describe releases that really did default there, and rewriting
them would make the history lie.

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: the uploads directory is ~/CSA-Uploads, and say what to move

The README's OneDrive sentence is deleted rather than reworded: it framed
redirection as a syncing preference, and the real problem was that the two
paths diverged. The new location has neither.

CHANGELOG keeps its historical mentions of ~/Documents/CSA-Outbox - those
describe releases that really did default there, and editing them would make
the history lie."
```

---

## Self-Review

**Spec coverage.** This plan implements A1 only, which is the whole of the spec's server-side
work. D1 (no platform code) is enforced by the Global Constraints and by there being no task
that adds any. D2 (never creates) is untouched — no task modifies creation behaviour. D3 is
satisfied by the chosen path and explicitly protected by a constraint against editing
`check_directories_disjoint`. D4 (`DEFAULT_DOWNLOAD_DIR` unchanged) is a constraint. D5 (told,
not migrated) is Task 2 Step 3 — the CHANGELOG tells people what to move and says nothing
migrates it. D6 belongs to the CSA-Plugins plan, not this one.

**Placeholder scan.** No TBDs. Every step names a file and a line range or shows the literal
replacement text. The two "do not change" tests are named with their reason rather than left
to judgement.

**Type consistency.** One identifier crosses tasks: `DEFAULT_ATTACH_DIR`, spelled identically
in both, with the value `"~/CSA-Uploads"` given verbatim in the Global Constraints and in Task
1 Step 3.

**One thing this plan cannot verify, stated rather than hidden.** The README's default column
is not covered by `test_docs_drift.py`, which checks that variables are *mentioned*, not that
the values are right. So Task 2 Step 2 is the one edit in this plan with no test behind it.
Closing that is stream C work (a test asserting the documented default equals
`DEFAULT_ATTACH_DIR`), deferred with the rest of stream C because its natural home is CI in a
repo that has none yet.
