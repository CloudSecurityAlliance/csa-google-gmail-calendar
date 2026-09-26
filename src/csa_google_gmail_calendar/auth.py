"""OAuth installed-app flow, and the scope set derived from which capabilities are enabled.

Ported from ../csa-google-workspace/src/csa_google_workspace/auth.py (471 lines), a shipped,
audited OAuth implementation for the same provider — the cached-token reading, re-consent
detection, and token-file hardening below are that project's, carried across with the
reasoning that produced them intact.

It diverges from the source in three deliberate places, each called out where it happens:

1. `scopes_for` takes the `enabled` capability set this server derives from `policy.py`, not a
   `read_only: bool`. The sibling project's `CSA_GW_READ_ONLY` posture flag and its
   `has_write_scope` / dual-cache-file machinery are dropped entirely — this project already
   has a finer-grained mechanism (the capability set) that supersedes a single on/off flag, and
   `policy.PolicyBackend` is what refuses a call the granted OAuth scopes would technically
   still allow. See carry-forward-task-9.md for why `_CAPABILITY_SCOPES` requests more than
   `scopes.narrowest()` would compute for some methods: the narrowest listed scope for a
   Discovery method is what the API accepts for THAT method alone, not what this server should
   request across everything a capability turns on, and two of the narrowest answers
   (`calendar.events.public.readonly`, `calendar.events.owned`) are useless for how this server
   actually calls Calendar.
2. `needs_reconsent` generalizes the source's Drive-shaped ".readonly"-suffix trick to
   `scopes.RANK`, because that trick cannot express Gmail's naming (see the function's own
   docstring for why).
3. `_write_token` writes atomically (temp file + `os.replace`), where the source truncates the
   real file in place. This one is a genuine fix rather than a port choice — the source project
   should get it too; see `_write_token`'s docstring.
"""
from __future__ import annotations

import errno
import json
import os
import subprocess  # nosec B404 - icacls only, fixed argv, no shell; see `_harden`
import sys
import tempfile

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import policy, scopes
from .exceptions import AuthError

_BASE = "https://www.googleapis.com/auth/"

# Spec §4 / carry-forward-task-9.md: the scope EACH capability should REQUEST, which is not
# always the narrowest scope `scopes.narrowest()` would compute for a single Discovery method.
# The two answer different questions — narrowest() says what the API's own scope list would
# accept for one method considered alone; this map says what this server should request given
# everything one capability turns on. They diverge because the narrowest scope for an isolated
# method can be useless in how this server actually calls it: `events.get`'s narrowest listed
# scope is `calendar.events.public.readonly`, which cannot read a private (i.e. almost every
# real) calendar; `events.insert`'s is `calendar.events.owned`, which 403s on `respond_to_event`
# patching an event somebody ELSE organised — exactly the path that method exists for. So the
# check this map has to satisfy is never equality against the narrowest-scope column: it is
# that every scope below is ranked in `scopes.RANK`, and ranks AT OR ABOVE the narrowest scope
# of every Discovery method the owning capability gates. `tests/test_auth.py` asserts the first
# half (every scope here is a real, ranked scope); the second half was verified by hand against
# `analysis/operation-inventory.csv` when this map was written and is not re-derived at runtime,
# because doing so would require loading the Discovery documents into this module.
_CAPABILITY_SCOPES: dict[str, tuple[str, ...]] = {
    policy.MAIL_READ:       (f"{_BASE}gmail.readonly",),
    policy.MAIL_WRITE:      (f"{_BASE}gmail.modify",),
    policy.MAIL_SEND:       (f"{_BASE}gmail.send",),
    policy.MAIL_DELETE:     ("https://mail.google.com/",),
    policy.CALENDAR_READ:   (f"{_BASE}calendar.events.readonly",
                             f"{_BASE}calendar.calendarlist.readonly",
                             f"{_BASE}calendar.freebusy"),
    policy.CALENDAR_WRITE:  (f"{_BASE}calendar.events",),
    policy.CALENDAR_DELETE: (f"{_BASE}calendar.events",),
}


# Fix round 1, item 1: two capabilities can each contribute a scope where one is a strict
# superset of the other — MAIL_READ's `gmail.readonly` is wholly contained in MAIL_WRITE's
# `gmail.modify` ("the whole mailbox, short of permanent delete" — scopes.py's own comment on
# it); CALENDAR_READ's `calendar.events.readonly` is wholly contained in CALENDAR_WRITE's/
# CALENDAR_DELETE's `calendar.events`. Requesting both grants nothing the broader one doesn't
# already grant, and it shows up on the one screen a user actually reads: the OAuth consent
# screen lists every scope requested, so a redundant entry is a second line asking for
# something the enabled capabilities do not need — exactly the over-declaration spec §4
# criticises both official Google servers for, done in miniature.
#
# This is deliberately NOT derived from `scopes.RANK` alone. `scopes.py`'s own docstring warns
# that RANK is a totalisation of only a PARTIAL order — it exists so `narrowest()` can pick
# among OR-alternative candidates for one Discovery method, not to answer "does A grant
# everything B does" for two arbitrary ranked scopes. `gmail.send` sits between
# `gmail.readonly` and `gmail.modify` in RANK, but Google gives sending its own scope
# deliberately: `gmail.modify` does NOT include it, so a rank-order collapse ("drop anything
# ranked below the highest scope present") would incorrectly drop `gmail.send` whenever
# `gmail.modify` is also requested. Likewise `calendar.freebusy` ranks below `calendar.events`
# but is not implied by it — free/busy is a distinct, narrower read than the full event body.
# So the two facts below are declared, not inferred, and checked against `scopes.RANK` only as
# a sanity bound: the dominant scope must genuinely outrank what it dominates, which would
# catch a typo'd or swapped pair at import time rather than at a consent screen.
_SUBSUMES: dict[str, tuple[str, ...]] = {
    f"{_BASE}gmail.modify": (f"{_BASE}gmail.readonly",),
    f"{_BASE}calendar.events": (f"{_BASE}calendar.events.readonly",),
}
for _dominant, _dominated in _SUBSUMES.items():
    for _d in _dominated:
        assert scopes.RANK[_dominant] > scopes.RANK[_d], (
            f"{_dominant!r} must outrank {_d!r} in scopes.RANK to be declared as subsuming it")
del _dominant, _dominated, _d


def scopes_for(enabled: frozenset[str]) -> list[str]:
    """Spec §4: the scopes the ENABLED capabilities need, nothing more.

    Both official servers over-declare — Gmail advertises the full-mailbox scope while
    shipping no permanent-delete tool. A client granting the declared set grants more than
    the tools can exercise, and this is the function that stops us doing the same. The
    per-capability union in `_CAPABILITY_SCOPES` can still contain one scope that a broader
    one already covers (see `_SUBSUMES`); this collapses those before the set reaches a
    consent screen, request URL, or manifest.
    """
    wanted: set[str] = set()
    for capability in sorted(enabled):
        wanted.update(_CAPABILITY_SCOPES.get(capability, ()))
    for dominant, dominated in _SUBSUMES.items():
        if dominant in wanted:
            wanted.difference_update(dominated)
    return sorted(wanted)


def _scope_family(scope: str) -> str:
    """Which of scopes.py's two RANK orderings `scope` belongs to. Comparing privilege only
    makes sense within one api's ordering — a Calendar grant satisfying a Gmail requirement
    (or vice versa) would be nonsense even if their RANK integers happened to coincide, since
    each is a separate 0-based sequence over a different API's scopes."""
    if scope == "https://mail.google.com/":
        return "gmail"          # top of _GMAIL_ORDER, not a googleapis.com/auth/ URL
    tail = scope[len(_BASE):] if scope.startswith(_BASE) else scope
    return tail.split(".", 1)[0]


def needs_reconsent(granted: list[str], required: list[str]) -> bool:
    """A required scope is satisfied by an identical grant, or by a grant that ranks AT LEAST
    as privileged within the same api family — e.g. a granted `gmail.modify` satisfies a
    required `gmail.readonly`, and a granted `calendar.events` satisfies a required
    `calendar.events.readonly`.

    PORT NOTE (task 9, see task-9-report.md): the source project (csa-google-workspace)
    detected this by stripping a literal ".readonly" suffix and checking whether the bare
    base ("drive.readonly" -> "drive") was granted — which is exactly Drive's own read-only/
    read-write naming convention, and only that convention. Gmail does not follow it: its
    read-write scope is `gmail.modify`, not `gmail` (stripping ".readonly" from
    "gmail.readonly" yields "gmail", which Google does not issue as a scope), so the string
    trick silently never fires for Gmail — this project's own required test (a granted
    `gmail.modify` must satisfy a required `gmail.readonly`) fails under a verbatim port. The
    RULE this function encodes is unchanged from the source and is the one task-9-brief.md
    names ("a granted read-write scope satisfies a required read-only one"); only the
    MECHANISM changes, from string-suffix matching to the privilege lattice `scopes.py`
    already built for exactly this comparison, restricted to one api family at a time via
    `_scope_family` so a Calendar grant can never satisfy a Gmail requirement or vice versa.
    A scope outside `scopes.RANK` (unranked, or one this project simply does not request)
    cannot be reasoned about this way and always falls through to "needs reconsent" — the
    conservative direction.
    """
    granted_set = set(granted or [])
    for scope in required:
        if scope in granted_set:
            continue
        if scope in scopes.RANK and any(
            g in scopes.RANK and _scope_family(g) == _scope_family(scope)
            and scopes.RANK[g] >= scopes.RANK[scope]
            for g in granted_set
        ):
            continue  # a granted scope at or above this one in the same api's lattice
        return True
    return False


class ScopesMissingError(AuthError):
    """A cached token exists and is loadable, but is short of scopes.

    Its own type, not a message, because the CALLER needs to word things differently: "your
    login is fine but one scope short" is a different instruction from "you have not logged in",
    and the fix is the same command while the explanation is not. `scopes` is the difference,
    shortened to leaf names for reading - the full URLs are noise in a terminal.
    """

    def __init__(self, missing: list[str]) -> None:
        self.scopes = list(missing)
        leaves = ", ".join(s.rsplit("/", 1)[-1] for s in self.scopes)
        super().__init__(
            f"cached credentials lack {len(self.scopes)} required scope(s): {leaves}. The token "
            f"itself is present and valid - it was issued before this version needed that "
            f"scope - so this is a re-consent, not a lost login.")


def _read_cached(token_path: str, required: list[str], *,
                 explain_missing_scopes: bool = False) -> Credentials | None:
    """The token cache, or None if absent / scope-stale — both meaning 'consent is needed'.

    `explain_missing_scopes` picks which of two callers is asking, and they genuinely want
    opposite things:

    * the **interactive** path (`load_credentials`) treats a scope-short token as "go and get
      consent" and falls through to the browser flow, so it wants a bare `None`. Raising here
      broke that fallback, which is why this is a flag rather than a change of behaviour;
    * the **non-interactive** path (`load_cached_credentials`, the stdio MCP server) cannot
      prompt, so `None` becomes a message a human reads — and it must say the token is present
      and one scope short rather than absent.
    """
    if not os.path.exists(token_path):
        return None
    try:
        creds = Credentials.from_authorized_user_file(token_path)
    except (ValueError, GoogleAuthError) as e:
        # Generic message: don't interpolate the cause (may echo token material). The
        # original is preserved via `from e` for debugging (#19).
        raise AuthError("could not load cached credentials") from e
    granted = list(creds.scopes or [])
    missing = [s for s in required if s not in set(granted)]
    if missing and explain_missing_scopes:
        # NAME THE MISSING SCOPES. Returning a bare None here was the whole of the defect: a
        # token that is present, valid, and one scope short is indistinguishable from no token
        # at all, and the caller's message then said "no usable token" about a file sitting
        # right there. Every scope this server adds in a future release does this again.
        raise ScopesMissingError(missing)
    if missing:
        return None                     # interactive caller: fall through to consent
    return creds


def _refresh(creds: Credentials) -> None:
    try:
        creds.refresh(Request())
    except (ValueError, GoogleAuthError) as e:
        # Fix round 1, item 3: a revoked or expired refresh token is the single most common
        # way a working setup stops working (the user revoked app access, an admin action, a
        # password change), and the remedy is the same regardless of which of those it was:
        # authenticate again. Say so, rather than leaving the caller with only "could not
        # refresh" and no next step. Still generic and still doesn't interpolate `e` - the
        # remedy costs nothing to state and carries no token material, unlike the cause would.
        raise AuthError(
            "could not refresh cached credentials - the refresh token is expired or was "
            "revoked. Re-authenticate: run this package's interactive OAuth login "
            "(auth.load_credentials) again to obtain a new token.") from e


_WINDOWS = os.name == "nt"

# The principals an owner-only file may name on Windows. The current user, plus the two
# root-equivalents: excluding SYSTEM or Administrators would stop nothing, because an
# administrator can take ownership of any file - exactly as `root` reads a 0o600 file on POSIX.
# Tolerating them is therefore the faithful analogue of 0o600, not a concession.
#
# What does the discriminating work is the INHERITANCE test below, not this set. A file that
# merely sits in a well-permissioned directory carries those same three principals as INHERITED
# ACEs (marked `(I)` by icacls), and an inherited ACL is the directory's, not ours - it changes
# the moment the file moves or the directory is re-permissioned. So "no inherited ACEs" is what
# separates a file we hardened from one that happens to be somewhere safe, and without it this
# predicate would answer True for every freshly created file and never fail.
_WINDOWS_ROOT_EQUIVALENTS = ("NT AUTHORITY\\SYSTEM", "BUILTIN\\Administrators")


def _current_windows_principal() -> str:
    return f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")


def _icacls(*args: str) -> subprocess.CompletedProcess:
    # Fixed argv, no shell, and every path is one this process constructed.
    return subprocess.run(["icacls", *args], capture_output=True, text=True,  # nosec B603 B607
                          check=False)


def file_is_owner_only(path: str) -> bool | None:
    """Is `path` readable only by the user who owns it? `None` when that cannot be determined.

    Asked as a QUESTION rather than asserted as `0o600`, because `0o600` is the POSIX *answer*
    and hard-coding it is how the Windows gap survived: `chmod` there sets only the read-only
    bit, so `os.stat` keeps reporting `0o666` however often you harden the file.

    `None` is not `False`. A file that is absent, or an `icacls` that could not run, is *unknown*,
    and reporting unknown as protected is the dangerous direction - the same asymmetry
    `labels.py` and `_inventory.py` are built on.
    """
    if not os.path.exists(path):
        return None
    if not _WINDOWS:
        return os.stat(path).st_mode & 0o077 == 0
    acl = _read_acl(path)
    if acl is None:
        return None
    principals, inherited = acl
    if inherited:
        return False                # an inherited ACE: the ACL is the directory's, not ours
    if not principals:
        return None                 # icacls said nothing we could read; unknown, not secure
    return not _strays(principals)


def _read_acl(path: str) -> tuple[list[str], bool] | None:
    """(explicit principals, any inherited ACE) from icacls, or None if it could not be read.

    One parser, because `file_is_owner_only` and `_harden` ask the same question of the same
    output and a second copy is how they would drift apart.
    """
    result = _icacls(path)
    if result.returncode != 0:
        return None
    principals: list[str] = []
    inherited = False
    for raw in result.stdout.splitlines():
        line = raw.removeprefix(path).strip()
        if not line or line.startswith("Successfully processed"):
            continue
        if "(I)" in line:
            inherited = True
            continue
        # `DOMAIN\user:(F)` - rsplit, because a principal itself contains no colon but a path
        # prefix would. Everything after the last colon is the rights mask.
        principals.append(line.rsplit(":", 1)[0].strip())
    return principals, inherited


def _is_own_logon_session(principal: str) -> bool:
    r"""Is this the LOGON SESSION SID - the owner's own session, not a third party?

    Windows puts `S-1-5-5-<x>-<y>` in the default DACL of files created by some processes, and
    icacls displays it as `NT AUTHORITY\LogonSessionId_0_<id>`. Whether it appears depends on the
    creating process's token: measured 2026-09-15 on one machine, a file created by a process
    launched from PowerShell carries it and the same code from Git Bash does not.

    **It is tolerated rather than removed, and the reason is what it identifies.** A logon session
    SID is held by exactly the processes in ONE interactive logon of ONE user - it is strictly
    NARROWER than "the owner", not wider, so it grants nothing the owner does not already have.
    It also cannot outlive its usefulness to anyone else: the next logon gets a different SID, so
    a stale ace grants nothing at all.

    And it could not be removed even if we wanted to. `icacls /remove:g` on that display name
    fails with **1332, ERROR_NONE_MAPPED** - the name does not resolve back to a SID - which is
    itself the evidence that it is not an ordinary principal. Trying and failing silently is what
    this codebase calls a fallback that drops the property (CLAUDE.md invariant 12), so the
    decision is made explicitly here instead.
    """
    return principal.upper().startswith("NT AUTHORITY\\LOGONSESSIONID_")


def _strays(principals: list[str]) -> list[str]:
    """Principals on the ACL that are neither the owner, a root-equivalent, nor its own session."""
    allowed = {_current_windows_principal().lower(),
               *(p.lower() for p in _WINDOWS_ROOT_EQUIVALENTS)}
    return [p for p in principals
            if p.lower() not in allowed and not _is_own_logon_session(p)]


def _unexpected_principals(path: str) -> list[str]:
    acl = _read_acl(path)
    return _strays(acl[0]) if acl else []


def _harden(path: str, fd: int | None = None) -> None:
    """Restrict `path` to its owner, by whatever mechanism the platform actually has.

    On Windows `chmod`/`fchmod` are no-ops for the owner/group/other bits, so the POSIX calls
    below "succeeded" while changing nothing - `THREAT_MODEL.md` T5 cited them as its mitigation
    and on Windows the evidence did not hold. `icacls /inheritance:r /grant:r <user>:F` is the
    real equivalent: it drops the inherited ACL and leaves exactly one ACE.
    """
    if _WINDOWS:
        result = _icacls(path, "/inheritance:r", "/grant:r", f"{_current_windows_principal()}:F")
        # AND THEN REMOVE WHATEVER ELSE IS THERE. `/inheritance:r` drops only INHERITED aces and
        # `/grant:r` replaces only the ace for the principal named, so anything explicit that
        # Windows itself put on the file SURVIVES BOTH. The process default DACL is the source:
        # launched from PowerShell a new file carries `NT AUTHORITY\LogonSessionId_0_<id>:(RX)`,
        # launched from Git Bash it does not. Measured 2026-09-15, same machine, same code.
        #
        # So without this the resulting ACL depended on which shell started the process - and a
        # security mechanism whose outcome varies with the parent's token is not one. Removing
        # the strays makes `_harden` deterministic, which is the property being bought here.
        # SYSTEM and Administrators are left alone: excluding them stops nothing (an admin takes
        # ownership, exactly as root reads a 0o600 file) and removing SYSTEM breaks backup and AV.
        if result.returncode == 0:
            for principal in _unexpected_principals(path):
                _icacls(path, "/remove:g", principal)
        if result.returncode != 0:
            # Warn rather than refuse: failing the write would leave a user unable to log in at
            # all because an ACL tool was unavailable, and they would still have no token. The
            # warning goes to stderr, which is safe under stdio (only stdout carries JSON-RPC).
            print(f"Warning: could not restrict {path} to your account; it inherits the "
                  f"directory's permissions. icacls said: {result.stderr.strip() or 'nothing'}",
                  file=sys.stderr)
    elif fd is not None and hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    else:
        # A DIRECTORY NEEDS THE EXECUTE BIT. 0o600 on a directory is not "tighter", it is
        # unusable - nothing can traverse into it, including us on the next call. The Windows
        # branch above has no such distinction, which is exactly why it is easy to lose here:
        # a Windows-only test run cannot reach this line at all.
        os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)


def _refuse_symlink(path: str) -> None:
    """Explicit symlink check, because `O_NOFOLLOW` does not exist on Windows.

    The old guard was `getattr(os, "O_NOFOLLOW", 0)` - which on Windows is `| 0`, so the flag
    read as present and the defence was absent. This check is NOT a replacement: on POSIX
    `O_NOFOLLOW` is still passed and is the atomic one. This is racy by construction (the link
    can appear between the check and the open) and only narrows the window on the platform that
    has no atomic option at all. Windows symlinks and junctions are both reparse points and
    `os.path.islink` reports both.
    """
    if os.path.islink(path):
        raise OSError(errno.ELOOP, "refusing to write the token through a symlink", path)


def _write_token(token_path: str, creds: Credentials) -> None:
    """Write the token cache, atomically.

    Fix round 1, item 2 — DIVERGES from `csa-google-workspace` and should be backported there.
    The source project opens `token_path` directly with `O_TRUNC`, which is not atomic: a
    second process reading the same file mid-write (two MCP clients configured to share one
    token path, an ordinary setup rather than a hypothetical one, with one of them refreshing)
    can observe a partially written file — valid up to wherever the writer had reached, then
    truncated — and see a corrupt-token `AuthError` for a credential that is actually fine one
    write later. The token is a bearer credential for an entire mailbox, so that race is worth
    closing here even though it was inherited rather than introduced by this port.

    The fix: write the new content to a temp file IN THE SAME DIRECTORY, harden it before any
    content lands in it (same rule as before — the restrictive mode has to be in place at
    creation, not fixed up afterward), then `os.replace()` it onto `token_path`. `os.replace` is
    a single filesystem rename — POSIX `rename(2)`, Windows `MoveFileEx` with
    `MOVEFILE_REPLACE_EXISTING` — both atomic, so a concurrent reader sees either the whole old
    file or the whole new one, never a partial write. "Same directory" is what keeps this a
    same-filesystem rename; some runtimes silently fall back to copy-then-delete across a mount
    boundary, which would reopen exactly the race being closed here.
    """
    token_dir = os.path.dirname(token_path) or "."
    if token_dir != "." and not os.path.isdir(token_dir):
        os.makedirs(token_dir, exist_ok=True)
        _harden(token_dir)              # only harden a dir we created; don't mutate a caller's (#4)
    _refuse_symlink(token_path)          # refuse to replace a pre-existing symlink at this name
    fd, tmp_path = tempfile.mkstemp(dir=token_dir, prefix=".token-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            _harden(tmp_path, fd)       # restrictive mode enforced before content is written
            f.write(creds.to_json())
        os.replace(tmp_path, token_path)   # atomic swap; never a reader-visible partial write
    except BaseException:
        try:
            os.remove(tmp_path)          # the swap never happened; don't leave the temp behind
        except OSError:
            pass
        raise


# Where a client-secrets file comes from, said once. Every failure below ends with this,
# because "your file is wrong" without "and here is the file you should have" is half an error.
_WHERE_FROM = ("It must be the JSON for a Google Cloud OAuth client of type **Desktop app**. "
               "Download it from the Cloud console, or install the CSA one, then point "
               "CSA_GGC_CLIENT_SECRETS at it or place it at "
               "~/.csa_google_gmail_calendar/client_secret.json.")


def read_client_secrets(path: str) -> dict:
    """Parse an OAuth client-secrets file into a client config, or raise an actionable `AuthError`.

    This exists so that **we** open the file rather than `google_auth_oauthlib`, for two reasons
    that are worth keeping apart (#449).

    **Encoding.** `from_client_secrets_file` opens with no `encoding=` argument, so a UTF-8 BOM
    lands at char 0 and `json.load` refuses a file that is perfectly valid JSON. A BOM is legal
    and common on Windows - `Set-Content -Encoding utf8` emits one under Windows PowerShell 5.1
    though not under PowerShell 7 - so tolerating it belongs in the client, not in a rule about
    who may write the file. `utf-8-sig` strips a BOM when present and is a no-op when absent.

    **Actionability.** The upstream failure is `Expecting value: line 1 column 1 (char 0)` raised
    from inside a dependency, naming neither the path nor the fact that a client-secrets file was
    being read. That is unactionable even when the file genuinely IS malformed, which is the case
    that outlives the BOM. Every raise here names the path, says what the file is for, and keeps
    the underlying parse detail rather than swallowing it.

    Callers hand the result to `from_client_config`.
    """
    try:
        with open(os.path.expanduser(path), encoding="utf-8-sig") as f:
            config = json.load(f)
    except FileNotFoundError:
        raise AuthError(f"No OAuth client secrets at {path}. {_WHERE_FROM}") from None
    except OSError as e:
        raise AuthError(f"Could not read the OAuth client secrets at {path}: {e}") from None
    except json.JSONDecodeError as e:
        # `e` carries "line 1 column 1 (char 0)"; keep it - it is the only thing that
        # distinguishes a BOM-like problem at char 0 from a truncated file at char 4000.
        raise AuthError(f"The OAuth client secrets at {path} are not valid JSON: {e}. "
                        f"{_WHERE_FROM}") from None
    if not isinstance(config, dict) or not (config.get("installed") or config.get("web")):
        # Valid JSON, wrong document. Overwhelmingly a service-account key. Upstream's own
        # message ("Client secrets must be for a web or installed app") names no file, and when
        # two candidate files are on disk that is the whole question.
        top = ", ".join(sorted(config)) if isinstance(config, dict) else type(config).__name__
        raise AuthError(f"The JSON at {path} is not an OAuth client: it has no 'installed' or "
                        f"'web' key (found: {top}). {_WHERE_FROM}")
    return config


def load_credentials(client_secrets: str, token_path: str, required: list[str],
                     *, force: bool = False) -> Credentials:
    """Interactive: reuse the cache, else open a browser for consent. Terminal use only.

    `force=True` ignores the cache and re-consents. It does not delete anything: the
    existing token is replaced only once a new one is in hand, so a cancelled or failed
    consent leaves the old credentials working.

    Do NOT call this from a stdio MCP server — `run_local_server()` prints the consent URL
    to stdout (the JSON-RPC channel) and blocks on the browser redirect. Servers call
    `load_cached_credentials` instead, which has no such branch.
    """
    token_path = os.path.expanduser(token_path)
    creds = None if force else _read_cached(token_path, required)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        _refresh(creds)
    else:
        config = read_client_secrets(client_secrets)    # we open it; see read_client_secrets (#449)
        creds = InstalledAppFlow.from_client_config(config, required).run_local_server(port=0)
    _write_token(token_path, creds)
    return creds


def load_cached_credentials(token_path: str, required: list[str]) -> Credentials:
    """Non-interactive: usable credentials from the token cache, or `AuthError`.

    This function deliberately contains **no** `InstalledAppFlow` branch, so a caller that
    must never prompt — the stdio MCP server — cannot reach interactive consent even by
    mistake. That is a structural guarantee rather than a convention. Refreshing an expired
    token is pure HTTP with no stdout writes, so it stays on this path.

    No `client_secrets` argument is needed: `to_json()` persists client_id/client_secret/
    token_uri into the cache, so a cached token is self-sufficient for refresh.
    """
    token_path = os.path.expanduser(token_path)
    if not os.path.exists(token_path):
        raise AuthError("no cached credentials")
    # `_read_cached` raises `ScopesMissingError` (naming the scopes) rather than returning
    # None for a scope-short token, so a None here means only "nothing loadable".
    creds = _read_cached(token_path, required, explain_missing_scopes=True)
    if creds is None:
        raise AuthError("no usable cached credentials")
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        _refresh(creds)
        _write_token(token_path, creds)     # persist the refreshed token
        return creds
    raise AuthError("cached credentials are invalid and cannot be refreshed")


_TOKEN_PATH_ENV_VAR = "CSA_GGC_TOKEN_PATH"
_DEFAULT_TOKEN_PATH = "~/.csa_google_gmail_calendar/token.json"


def token_path_default() -> str:
    """Where the token cache lives when nothing overrides it.

    Reads CSA_GGC_TOKEN_PATH so an operator can relocate the cache (onto an encrypted volume,
    a mounted secrets directory) without a code change. The fallback mirrors the sibling
    project's per-package dotdir convention (`~/.csa_google_workspace/token.json`), so someone
    running both servers gets two separate token files rather than a name collision.
    """
    return os.path.expanduser(os.environ.get(_TOKEN_PATH_ENV_VAR) or _DEFAULT_TOKEN_PATH)
