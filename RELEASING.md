# Releasing

Publishing is **CI-only, off a published GitHub Release, via PyPI Trusted Publishing (OIDC)**.
No long-lived API token exists to leak. Nothing is ever published from a laptop.

The chain each release preserves, per
[`PUBLIC-GITHUB-REPO-STANDARDS.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/PUBLIC-GITHUB-REPO-STANDARDS.md) §6:

```
PR → protected main → GitHub Release → CI build → approved, attested OIDC publish
```

Every link is only as strong as branch protection.

## One-time setup

Do these once, in this order. Until all three are done, a release run will fail at the publish
step - which is the correct failure, not a bug.

### 1. PyPI pending publisher

`csa-google-gmail-calendar` does not exist on PyPI yet, so there is no project to attach a
publisher to. PyPI calls this a **pending publisher**: configure it first, and the project is
created by the first successful upload. At <https://pypi.org/manage/account/publishing/>:

| Field | Value |
|---|---|
| PyPI Project Name | `csa-google-gmail-calendar` |
| Owner | `CloudSecurityAlliance` |
| Repository name | `csa-google-gmail-calendar` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

**The environment name is not optional, and this is the one people leave blank.** If it is
blank, PyPI accepts an OIDC token from *any* environment in that repo+workflow. The workflow
still says `environment: pypi`, the approval gate still looks configured - and it is enforced
only by a line of YAML inside the repository being published, which anyone able to edit the
workflow can delete. PyPI notices and emails *"Trusted Publisher … can be made more secure"* on
every publish that skips it.

If you ever need to change the binding: **add the constrained publisher first, confirm it, then
remove the unconstrained one** - so there is never a window with no working publisher.

### 2. GitHub Environment `pypi`

Settings → Environments → `pypi` → **Required reviewers**. This makes a release pause for an
explicit approval before anything is uploaded.

Be precise about what this buys, because the honest description is narrower than "required
reviewer" sounds. It gives a **pause**, an **audit record** of who approved and when, and
**decoupling** of publishing from merging. It does *not* give separation of duties if the
approver is the same principal that opened the PR, merged it and cut the release - that is one
principal agreeing with itself. Claim the first three; do not claim the fourth.

### 3. Branch protection on `main`

Required status checks: `lint`, `test (3.10)`, `test (3.11)`, `test (3.12)`, `test (3.13)`,
`test (3.14)`, `security`.

---

## Cutting a release

1. **Land everything through a PR** and let `main` go green.

2. **Bump the version in one place** -
   `src/csa_google_gmail_calendar/__init__.py`'s `__version__`. `pyproject.toml` reads it from
   there by static AST parse, so there is no second place to forget. Land the bump and the
   CHANGELOG entry as an ordinary PR.

3. **Promote `[Unreleased]` in `CHANGELOG.md`** to the version and date.

4. **Check locally before tagging:**
   ```bash
   .venv/bin/python -m pytest --cov --cov-report=term-missing --cov-fail-under=90
   .venv/bin/python -m ruff check .
   .venv/bin/python -m mypy
   # The security gate, which CI runs and a checklist that omits it is a checklist that
   # reports green on work CI will reject.
   .venv/bin/python -m bandit -r src
   .venv/bin/python -m pip_audit --skip-editable
   ```

5. **Create the GitHub Release.** This creates the tag *and* fires `release.yml`:
   ```bash
   gh release create v0.2.0 --title v0.2.0 \
     --notes-file <(sed -n '/## \[0.2.0\]/,/^## /p' CHANGELOG.md | head -n -1)
   ```
   **Tag == version.** `v0.2.0` must equal `__version__`. The tag is the provenance anchor:
   `git checkout v0.2.0` must reproduce exactly what shipped.

6. **Approve the pending deployment.** The `publish` job waits on the `pypi` environment:
   ```bash
   RUN=$(gh run list --workflow=release.yml --limit 1 --json databaseId -q '.[0].databaseId')
   ENV_ID=$(gh api repos/CloudSecurityAlliance/csa-google-gmail-calendar/environments/pypi --jq .id)
   gh api --method POST \
     "repos/CloudSecurityAlliance/csa-google-gmail-calendar/actions/runs/$RUN/pending_deployments" \
     --input - <<JSON
   {"environment_ids":[$ENV_ID],"state":"approved","comment":"why this is safe to ship"}
   JSON
   ```
   Use `--input -`. `gh api -f 'environment_ids[]=…'` breaks under zsh, which glob-expands the
   brackets.

7. **Verify the publish actually landed, and that it is attested.**
   ```bash
   # PEP 740 provenance lives at the integrity endpoint. The project JSON endpoint has NO
   # `provenance` key whether or not the release is attested, so checking there proves nothing
   # either way.
   WHEEL=$(curl -s https://pypi.org/pypi/csa-google-gmail-calendar/0.2.0/json | python3 -c \
     "import json,sys; print([u['filename'] for u in json.load(sys.stdin)['urls'] if u['filename'].endswith('.whl')][0])")
   curl -s "https://pypi.org/integrity/csa-google-gmail-calendar/0.2.0/$WHEEL/provenance" | head -c 400

   # --no-cache-dir always: pip caches the index OUTSIDE any venv, so a fresh venv is not a
   # fresh view. PyPI's CDN edges also lag independently, so retry rather than concluding the
   # publish failed.
   python -m pip download --no-cache-dir --no-deps -d /tmp/verify csa-google-gmail-calendar==0.2.0
   ```

---

## Invariants

- **`publish` never checks out this repository and never installs a dependency of ours.** It
  downloads the artifacts `build` already produced and uploads them. Any step added there that
  runs project code, or a dependency of it, reintroduces the exact risk the two-job split exists
  to remove.
- **`security` (pip-audit + bandit) runs unpinned**, deliberately - it audits what a real
  `pip install csa-google-gmail-calendar` resolves to today, not a frozen closure nobody
  installs. A CVE disclosed after the last merge is still caught on the release path even if CI
  ran green weeks ago.
- **The sdist guard** in `release.yml` fails the build if a token, credential-shaped file, or the
  `analysis`/`research`/`docs` directories reach the built artifact - see that step's own
  comments for why `.py` files are excluded from the word match.
- **Coverage stays a real gate.** `--cov-fail-under=90` is not lowered to make a release pass;
  a shortfall is a reason to add tests or to mark a specific line `# pragma: no cover` with a
  comment naming what covers it instead (see `pyproject.toml`'s `[tool.coverage.report]` and
  `_auth_flow.py`/`_login.py` for the two lines that currently are).
