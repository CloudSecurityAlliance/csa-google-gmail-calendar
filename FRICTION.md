# Friction

What cost time here, so it costs it once. Bugs go to
[issues](https://github.com/CloudSecurityAlliance/csa-google-gmail-calendar/issues); this is for
the friction that recurs because nothing in the repo remembers it.

## F1 — A local gate weaker than CI reports green on work CI will reject (#32)

`RELEASING.md` passed `--cov-fail-under=90`; CI enforced **100**. A checklist that is a *subset*
of CI's is a checklist that passes on work CI will refuse — and this repo's own release notes
already warned about exactly that shape for the security gate, after a bandit finding reached CI
instead of a laptop.

It had not bitten only because the measured number sits at **99.62%**: comfortably over 90, and
under 100.

**Raising it to 100 would have been the wrong repair.** `auth.py` keys on `_WINDOWS`, so 100% is
reachable by the *union* of platforms and by neither alone. A Windows maintainer following a
`--cov-fail-under=100` checklist gets a red line that is correct and means nothing is wrong,
which trains people to ignore it.

The rule that survives: **a shortfall is never answered by lowering the number.** It is answered
by adding tests, or a `# pragma: no cover` naming what covers the line instead — and a
platform-branch line is exactly the case a pragma does *not* fit, because the other platform
does execute it.

## F2 — The checklist assumed a platform it never named (#32)

Every line used `.venv/bin/python`, which does not exist on Windows (`.venv/Scripts/python.exe`).
Not the coverage number — the **interpreter path in every line**. Said once now, since nothing
else differs.

## F3 — `--no-cov` is not "measure without gating"

While fixing F1 the first attempt used `--no-cov` and the prose claimed you would still see which
lines your platform leaves unexecuted. False: `--no-cov` disables coverage altogether and
`--cov-report=term-missing` then prints *"WARNING: Coverage disabled via --no-cov switch!"*
instead of a report. `--cov-fail-under=0` is the flag that matches the intent.

Caught by running the command that had just been documented, which is the only reason it did not
ship.

## F4 — A public README carried internal identifiers because the template did (#13)

An Airtable base id, a table id and a path into a private repo sat in the front matter of a
**public** README. Filed rather than fixed, deliberately, because *"one repo quietly diverging
from a fleet convention is worse than the convention being wrong consistently."*

Two measurements dissolved that: the block was in **three of five** public CSA repos and absent
from two that **never had it**, and **nothing consumed it** — zero hits across both CSA orgs in
`*.py`, `*.sh`, `*.ps1`, `*.yml`, `*.yaml`, `*.json`. So removing it from all three at once
converged rather than diverged, and broke no tooling link.

The transferable half: **"it is a convention" is a measurable claim.** Measure it before letting
it block a fix.

## F5 — `ls -l` cannot tell a hardened credential from an unhardened one

On Windows every file in `~/.csa_google_gmail_calendar/` renders `-rw-r--r--` in Git Bash,
including the ones `icacls` has narrowed to owner-only. The mode is a rendering; the ACL is the
fact.

Use `file_is_owner_only()`, which this repo ships, or `icacls` directly. Anything reading
`st_mode` is measuring something that does not govern access — and `os.chmod` there honours only
the read-only bit, so it cannot fix what it appears to report.

## F6 — Two directories that must not be allowed to become one

`CSA_GGC_DOWNLOAD_DIR` deliberately does **not** default to `CSA_GGC_ATTACH_DIR`, nor to a
subdirectory of it. With a single directory, a downloaded file becomes re-uploadable — content
that arrived from outside, **wearing a name the user trusts.**

Recorded as friction because the simplification is tempting every time someone writes a backup
or cleanup policy for these paths, and the reason it is wrong lives in `_attachments.py` rather
than anywhere a policy author would look. Now also in
[`BACKUP-RESOURCES.md`](BACKUP-RESOURCES.md).
