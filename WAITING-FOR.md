# Waiting for

Blockers with someone else's name on them. Work that is merely unfinished is in
[`TODO.md`](TODO.md); the distinction is whether this project can move it alone.

| Waiting on | What for | Cost of waiting |
|---|---|---|
| **A decision (Kurt)** | CINO-Platform-Engineering#172 — does the Windows job gate coverage, and at what number? | This repo's Windows job measures **no** coverage, so the `icacls` paths are pragma'd on ubuntu and unmeasured on Windows. The measurement and three options are on that issue |
| **A macOS machine** | CSA-Plugins#156 — macOS will not create a missing attachment directory and Windows now offers to | A parity gap the fix created. Until it is settled, the same install behaves differently on the two platforms at the point a person is least likely to check |
| **A macOS machine** | CSA-Plugins#170/#171 — `register_other_clients` has never executed on POSIX, only `bash -n`'d | Registration with Codex and Gemini is verified on Windows only, on the platform most CSA members do not use |
| **Google** | #35 — reminders and show-as-free on events. Both exist in the API; what is missing is confidence about the write path | Feature gap, not a defect |
| **Nothing — recorded so it is not rediscovered** | #13 is **closed**. The public README no longer carries internal Airtable ids or a private-repo path | Fixed across all three affected repos at once, since fixing one would have been a divergence rather than a fix |

## Not waiting on anything

- **#32 is closed.** The local release checklist no longer gates coverage at a number weaker
  than CI's, and it now says which platform it assumes — `.venv/bin/python` does not exist on
  Windows.
- **The credential-at-rest hardening works**, verified on Windows 2026-10-02: current files
  report `owner_only=True`, and a pre-fix backup reports `False`. See
  [`BACKUP-RESOURCES.md`](BACKUP-RESOURCES.md).
