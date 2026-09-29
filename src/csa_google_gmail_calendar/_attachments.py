"""Which local files may be attached to outgoing mail, and where a downloaded attachment may be
written - two directions, two directories, and (fix round, final whole-branch review, CINO
2026-09-26) two separate environment variables, never one.

**Why one directory cannot do both jobs.** `AttachmentPolicy` (below) is the READ bound:
`send_message`/`create_draft`/etc. take a local path and attach whatever it names, so the bound
is "only files under `CSA_GGC_ATTACH_DIR` may be read and put on the wire." `DownloadPolicy`
(below) is the WRITE bound: `get_attachment` takes bytes from a message a STRANGER sent and
writes them to disk under a `filename` the same stranger chose. Configuring both directions to
the same directory - which is what this module used to do, with a single `CSA_GGC_ATTACH_DIR`
governing both - lets a stranger's message write a file into the exact directory outgoing mail
reads from. If the stranger names their attachment the same as a real file already there
(`q3-budget.pdf`), the download overwrites it; if they name it anything else, it is still a new
file sitting in the one directory a model is told is safe to attach from. Either way, a later
`send_message(attachments=["q3-budget.pdf"])` can put attacker-controlled bytes on the wire
wearing a name the user trusts. Separating the directions closes this: `CSA_GGC_DOWNLOAD_DIR`
does not default to `CSA_GGC_ATTACH_DIR`, does not default to a subdirectory of it, and
`check_directories_disjoint` (below) refuses at server construction if the two are ever
configured to overlap - the misconfiguration that recreates this bug is made impossible to
hold, not merely discouraged.

`send_message(attachments=[...])` takes a path, which makes it a file-read primitive wearing
an innocuous name. The bound is a single configured directory, and it is checked on the
**resolved** path: a symlink inside the root pointing at `~/.ssh/id_rsa` is inside the root by
its own name and outside it in every way that matters. `Path.resolve()` follows an entire
symlink chain (link to link to target) and walks through a symlinked directory component, not
just a symlinked final component, so both are caught by the same check rather than needing
special cases.

Unset means attachments (or downloads) are off, not unrestricted. That is the default-posture
rule from spec §3 applied to the filesystem, and the refusal names the variable - a capability
that is one environment variable away should say so rather than look broken (the csa-skilljar
idiom). A configured root that does not exist, or exists but is not a directory, is refused the
same way, at construction: a typo'd directory should not silently produce an object that
answers every call with "the file does not exist", sending the operator hunting for a missing
file instead of a broken configuration.

**Residual risk — TOCTOU.** `resolve()` followed by a read is two syscalls, and the filesystem
can change between them (a file swapped for a symlink after the check, before the read). This
is not fully closable from Python without `openat`-style primitives scoped to an open directory
file descriptor, which this module does not use. What it does instead: `resolve()` is called
exactly once per attachment, and `read()` re-opens the file by that same resolved path *string*
without re-deriving it from the caller's original, unresolved input — no file descriptor is
held open across the check, so this narrows the window (nothing re-parses the untrusted string
a second time) without closing it. Treat this as a residual risk, not a guarantee: a local
attacker who can race the filesystem between resolve and read is out of scope for this module.

**The same residual risk exists on the write side, in the other direction (documented,
final whole-branch review, CINO 2026-09-26).** `DownloadPolicy.write` does
`if resolved.exists(): raise` and then `resolved.write_bytes(...)` - a check, then a plain
truncating open, with no `O_EXCL` between them. A file created at that exact path in the
window between the two calls is silently clobbered by the write rather than triggering the
overwrite refusal this class exists to provide. Not closed here, and, unlike the read side, not
considered worth closing: `O_EXCL` would need dropping down to `os.open`/`os.fdopen` in place of
`Path.write_bytes`, for a window an attacker can only race by already holding what the
overwrite-refusal is defending against in the first place - the ability to create a
same-named file in the download directory at a moment of their choosing, i.e. local write
access to that directory. Someone who already has that has an easier path to the same
end (write the file directly, no race required) than winning a race against
`get_attachment`'s one write per call. Recorded here so the omission reads as considered, not
overlooked - the read-side paragraph above discusses only its own direction.

**Hard links are an accepted non-bound, not a bypass.** A hard link inside the root pointing at
an outside file has no symlink target to resolve — as far as the filesystem is concerned, it
*is* the same file, reachable under a second name that genuinely lives inside the root.
`resolve()` returns the in-root path unchanged, containment passes, and the bytes are read.
This is not evaded containment; the file really is inside the root. It is deliberately not
"fixed": an `st_nlink`/`st_dev` heuristic would also refuse ordinary, legitimate multiply-linked
files, and creating the hard link in the first place needs both write access to the root and
read access to the outside target — an adversary who already holds both could simply copy the
file into the root instead, so the check would buy nothing. Named here because the opening
paragraph's promise of a bound "checked on the resolved path" could otherwise be read as
"nothing outside the root is reachable", which is not quite what this module provides.

**Case-insensitive filesystems** (default on macOS/APFS): `Path.resolve()` does not
case-normalize a path to its on-disk spelling — it only follows symlinks and collapses `.`/
`..`. A supplied path that differs only in case from the configured root's real spelling will
therefore fail the (case-sensitive, string-prefix) containment check even though the OS would
resolve it to the same file. That failure mode is refusal, never escape: it can make this
policy reject a legitimate same-file path on a case-insensitive filesystem, it cannot make it
accept one that is actually outside the root. Unicode normalization (NFC vs NFD) is the same
class of non-issue for the same reason: a normalization difference in the root portion of a
path can cause a spurious refusal, never a false allow, because containment is a literal
path-component comparison and normalizing a string cannot move it across a directory boundary.
"""
from __future__ import annotations

import os
import pathlib

from .exceptions import PolicyError

ENV_VAR = "CSA_GGC_ATTACH_DIR"

# The two defaults, and why they are not the same directory (#23).
#
# `~/Downloads` for the WRITE side because that is what the directory is for on every platform
# this ships to, and because `csa-google-workspace` already defaults `CSA_GW_EXPORT_DIR` there
# for the same "a program gave me a file" reason - discoverable in the Finder/Explorer sidebar,
# persistent, and somewhere nobody keeps precious unique files.
#
# The READ side deliberately does NOT default to `~/Downloads`, and could not: the two roots
# must be disjoint (`check_directories_disjoint`) or the server refuses to start, so defaulting
# both there would make a fresh install unable to run at all. The deeper reason is the one that
# check exists for - a stranger emails a file, it lands in Downloads, and it is now inside the
# root `send_message` attaches from. Shipping that as a DEFAULT would be worse than shipping it
# as a footgun, because nobody would have chosen it.
#
# So the send-side default is a CSA-specific directory that incoming mail never writes to. It is
# empty until a person puts something in it, which is precisely the property that makes it safe
# to read from by default.
#
# It sits beside `~/Downloads`, not inside it, and not under `~/Documents`. Both halves are
# deliberate. Inside `~/Downloads` is the configuration `check_directories_disjoint` refuses
# outright, so it could never have been the default. Under `~/Documents` was the default until
# 2026-09-29, and it was wrong on Windows in a way nothing reported: OneDrive's Known Folder
# Move redirects Documents by default, `os.path.expanduser` does not know that and string-joins
# `$HOME/Documents` anyway, and both paths exist - so a person told to create the directory
# made it in the Documents they could see while the server read one they could not. The home
# root is redirected by nothing, on any platform, which is the whole reason it was chosen.
def _refuse_nul(raw: str, describe) -> None:
    r"""Refuse a path containing a NUL byte, explicitly.

    This used to be inferred: `Path.resolve(strict=False)` raised ValueError on an embedded
    NUL because the underlying lstat() did, and the ValueError arms below turned that into a
    policy refusal. Measured on Windows, that stopped being true:

        Path('ok.pdf' + chr(0) + '.txt').resolve(strict=False)
            3.12 -> ValueError        3.14 -> returns normally

    So on 3.14 the refusal quietly became "does not exist" - still a refusal, but a
    different one, arrived at by accident rather than by the check that exists for it. A
    guard that works only while an unrelated function keeps raising is not a guard.

    `describe` builds the message, because the four call sites word it differently (a
    configured root names its environment variable; a caller-supplied path does not).
    """
    if "\x00" in raw:
        raise PolicyError(describe(raw))


def _is_rooted(path: str) -> bool:
    r"""Does `path` start at a filesystem root, by any spelling this code may meet?

    Asked directly rather than delegated to `os.path.isabs` or `Path.is_absolute`, because
    BOTH have answered this differently across versions and platforms, and a security guard
    must not depend on which CPython is running. Measured on Windows:

        os.path.isabs('/etc/passwd')     3.12 -> True    3.14 -> False
        Path('/etc/passwd').is_absolute()             -> False on both

    Python 3.13 changed `ntpath.isabs` to require a drive letter, matching pathlib. That
    is a defensible tidy-up of a confusing API, and it silently disarmed the guard below
    on 3.14 - which had been repaired only hours earlier, for the 3.12 version of exactly
    the same hole. Containment still refused the path both times, so this was a weakened
    layer rather than an escape; a layer that keeps dying for a new reason is worth making
    version-proof rather than repairing a third time.

    A leading separator is what "rooted" means on every platform this ships to, so that is
    what gets tested.
    """
    return path.startswith(("/", "\\")) or os.path.isabs(path)


DEFAULT_ATTACH_DIR = "~/CSA-Uploads"
DEFAULT_DOWNLOAD_DIR = "~/Downloads"

# A refused path is echoed into the exception message so the caller can see what was rejected.
# The caller's string is untrusted and unbounded (it may come from a model), and exception
# messages tend to end up in logs, so what gets echoed is capped rather than reproduced whole.
_MAX_ECHO = 200


def _echo(path: str) -> str:
    if len(path) <= _MAX_ECHO:
        return repr(path)
    return f"{path[:_MAX_ECHO]!r}... ({len(path)} chars, truncated)"


# A DEFAULT that does not exist is left unconfigured, and said out loud. It is NOT created.
#
# Creating it would make the feature work, and would do so by writing into somebody's home
# directory because a program started - a side effect nobody sanctioned, for a path nobody
# chose. Safe-by-default beats working-by-surprise: the server starts, the direction stays off,
# and the operator is told exactly which directory to make or which variable to set.
#
# It also gives the send side a better opt-in than a flag. `~/Downloads` exists on essentially
# every machine, so receiving works immediately. `~/CSA-Uploads` exists on none, so
# **sending a local file stays off until a person makes that directory** - and making it is a
# deliberate act that says "outgoing attachments, from here". The safer direction is the one
# that requires the gesture.
_MISSING_DEFAULT = ("the default directory for {var} ({path}) does not exist, so {what} is off. "
                    "Create that directory - it takes effect on the next call, no restart - or "
                    "set {var} to one that exists.")


class AttachmentPolicy:
    def __init__(self, root: str | None, *, from_default: bool = False) -> None:
        """`from_default=True` marks a root this project chose rather than one an operator set.

        It changes exactly two things, and both follow from that distinction:

        - **A missing directory leaves this direction OFF, with a warning, instead of raising.**
          An explicitly configured root that does not exist is a typo, and failing loudly at
          startup is the whole point of checking here rather than on first use. A DEFAULT that
          does not exist is just a machine where nobody has made that directory, and neither
          killing the server nor silently creating the directory is the right answer to that -
          the first punishes someone for a path they never chose, the second writes into their
          home because a program started.
        - **It is reported as the default**, by `describe_configuration` and by the disjointness
          refusal, so a message naming two directories says which of them the reader actually
          chose.
        """
        self.from_default = from_default
        # Set on every path so callers never have to guard the attribute's existence; non-None
        # only for a default that could not be used.
        self.warning: str | None = None
        # A defaulted root that was absent at construction, re-checked on use. `None` whenever
        # there is nothing to re-check: an explicit root, or one that was already there.
        self._pending: pathlib.Path | None = None
        # The other direction, set by `check_directories_disjoint` - the one place both are ever
        # in hand. Needed because adopting a root late has to re-ask the question that function
        # answers, and a policy cannot otherwise see its counterpart.
        self._sibling: AttachmentPolicy | DownloadPolicy | None = None
        if not root:
            self.root: pathlib.Path | None = None
            return
        # Resolved at construction, once. A root that is itself a symlink (common: a
        # ~/Documents that points into a synced volume) would otherwise make every path
        # under it compare as an escape.
        #
        # Checked at construction, not on first use: a `Policy` object is built once at
        # server startup (see `from_env`), and failing loudly then — before any tool call
        # reaches a model — beats a lazy check that would only surface the same misconfiguration
        # on whatever the first attachment attempt happens to be. The trade-off this accepts is
        # that a process which expects its attachment directory to be created *after* this
        # object is constructed cannot use this constructor as written; nothing in this project
        # does that today.
        _refuse_nul(root, lambda r: f"{ENV_VAR} is set to {_echo(r)}, which is not a valid "
                                    f"path: it contains a NUL byte.")
        try:
            resolved_root = pathlib.Path(os.path.expanduser(root)).resolve(strict=False)
        except ValueError as exc:  # pragma: no cover - see _refuse_nul: the embedded-NUL case that used to reach
        # this arm is now refused before resolve() is called, and NUL was the only input
        # that made resolve() raise ValueError. Kept rather than deleted: if a future
        # CPython raises ValueError here for some other reason, this turns it into a
        # PolicyError instead of letting it escape the module as an unrelated traceback.
        # Unreachable today, deliberately, with the reason at the line - never a lowered
        # coverage threshold (TESTING.md).
            raise PolicyError(f"{ENV_VAR} is set to {_echo(root)}, which is not a valid path: "
                               f"{exc}") from exc
        if not resolved_root.is_dir():
            kind = "does not exist" if not resolved_root.exists() else "is not a directory"
            if from_default:
                # Not an error: nobody asked for this path, so it must not stop the server or
                # the OTHER direction, which may be configured perfectly well.
                #
                # `_pending` keeps the path so `_adopt_pending_default` can look again when a
                # tool actually needs it. The decision is deliberately NOT frozen here: the
                # directory is the documented remedy, and a server that cached "absent" would
                # keep refusing after somebody followed its own instructions, telling them to
                # create a directory that now exists. Nothing is created either way.
                self.root = None
                self._pending = resolved_root
                self.warning = _MISSING_DEFAULT.format(
                    var=ENV_VAR, path=resolved_root,
                    what="attaching a local file to outgoing mail")
                return
            raise PolicyError(
                f"{ENV_VAR} is set to {_echo(root)}, but {resolved_root} {kind}. Configure "
                f"{ENV_VAR} to point at a directory this server may read attachments from.")
        self.root = resolved_root

    def resolve(self, path: str) -> pathlib.Path:
        if self.root is None:
            _adopt_pending_default(self, ENV_VAR)
        if self.root is None:
            # "nothing is configured" would be false when a default exists and simply is not
            # there - and it sends the reader to set a variable when making a directory is the
            # shorter fix. The warning already says which, so it is the message.
            raise PolicyError(
                self.warning or
                f"attachments are disabled: no attachment directory is configured. Set "
                f"{ENV_VAR} to a directory this server may read files from, and only files "
                f"under it can be attached.")
        _refuse_nul(path, lambda r: f"{_echo(r)} is not a valid path: it contains a NUL byte.")
        expanded = os.path.expanduser(path)
        candidate = pathlib.Path(expanded)
        # See the note in `DownloadPolicy.resolve`: on Windows a leading-separator path is
        # not `is_absolute()`, so it was joined onto the root and the join silently threw the
        # root away. The containment check below caught the result either way; treating it as
        # absolute here just stops the join from producing a path nobody asked for.
        if not (candidate.is_absolute() or _is_rooted(expanded)):
            candidate = self.root / candidate
        try:
            # strict=False so a MISSING file reaches the readable error below rather than
            # raising OSError from resolve() itself. A NUL byte embedded in the path makes
            # the underlying lstat() raise ValueError even with strict=False — caught below
            # so it surfaces as a refusal rather than an uncaught ValueError escaping this
            # module wearing no relation to the attachment policy at all.
            resolved = candidate.resolve(strict=False)
        except ValueError as exc:  # pragma: no cover - see _refuse_nul: the embedded-NUL case that used to reach
        # this arm is now refused before resolve() is called, and NUL was the only input
        # that made resolve() raise ValueError. Kept rather than deleted: if a future
        # CPython raises ValueError here for some other reason, this turns it into a
        # PolicyError instead of letting it escape the module as an unrelated traceback.
        # Unreachable today, deliberately, with the reason at the line - never a lowered
        # coverage threshold (TESTING.md).
            raise PolicyError(f"{_echo(path)} is not a valid path: {exc}") from exc
        if not resolved.is_relative_to(self.root):
            raise PolicyError(
                f"{_echo(path)} resolves to a location outside the attachment directory "
                f"({self.root}). Only files under that directory can be attached.")
        if not resolved.exists():
            raise PolicyError(f"{_echo(path)} does not exist (looked at {resolved}).")
        if not resolved.is_file():
            # Also the backstop for a FIFO or device file living inside the root: is_file()
            # is False for those (it stats the target, not just "is there a directory entry"),
            # so they are refused here rather than reaching read_bytes(), which would block
            # forever on a FIFO with nothing on the other end - a hang, not a leak, but still
            # not something this policy should walk into.
            raise PolicyError(f"{_echo(path)} is not a regular file.")
        return resolved

    def read(self, path: str) -> tuple[bytes, str]:
        p = self.resolve(path)
        return p.read_bytes(), p.name


def from_env() -> AttachmentPolicy:
    """`CSA_GGC_ATTACH_DIR` if set, else `DEFAULT_ATTACH_DIR`.

    The env var still wins, and setting it to a path that does not exist still fails loudly -
    the default only covers the case where nobody has said anything. Before #23 an unset variable
    meant "sending attachments is off", which is a defensible fail-closed posture that had the
    practical effect of nobody ever using the feature.
    """
    configured = os.environ.get(ENV_VAR) or None
    if configured:
        return AttachmentPolicy(configured)
    return AttachmentPolicy(DEFAULT_ATTACH_DIR, from_default=True)


DOWNLOAD_ENV_VAR = "CSA_GGC_DOWNLOAD_DIR"


class DownloadPolicy:
    """Where `get_attachment` writes a downloaded attachment - the write-side, incoming-mail
    counterpart to `AttachmentPolicy`'s read-side, outgoing-mail root above. Deliberately a
    separate class with its own environment variable, never a second use of `CSA_GGC_ATTACH_DIR`
    or a subdirectory derived from it - see this module's docstring for why sharing one
    directory between the two directions is exploitable, not merely untidy.

    Unset (`root is None`) means downloads are refused, naming `CSA_GGC_DOWNLOAD_DIR` - the same
    posture `AttachmentPolicy` holds for `CSA_GGC_ATTACH_DIR`. A configured root is resolved and
    existence/directory-checked at construction, for the same reason `AttachmentPolicy` does:
    a typo'd directory should fail loudly at startup, not on whatever the first download
    happens to be.
    """

    def __init__(self, root: str | None, *, from_default: bool = False) -> None:
        """`from_default` carries the same meaning as on `AttachmentPolicy` - see there."""
        self.from_default = from_default
        self.warning: str | None = None
        # A defaulted root that was absent at construction, re-checked on use. `None` whenever
        # there is nothing to re-check: an explicit root, or one that was already there.
        self._pending: pathlib.Path | None = None
        # The other direction, set by `check_directories_disjoint` - the one place both are ever
        # in hand. Needed because adopting a root late has to re-ask the question that function
        # answers, and a policy cannot otherwise see its counterpart.
        self._sibling: AttachmentPolicy | DownloadPolicy | None = None
        if not root:
            self.root: pathlib.Path | None = None
            return
        _refuse_nul(root, lambda r: f"{DOWNLOAD_ENV_VAR} is set to {_echo(r)}, which is not "
                                    f"a valid path: it contains a NUL byte.")
        try:
            resolved_root = pathlib.Path(os.path.expanduser(root)).resolve(strict=False)
        except ValueError as exc:  # pragma: no cover - see _refuse_nul: the embedded-NUL case that used to reach
        # this arm is now refused before resolve() is called, and NUL was the only input
        # that made resolve() raise ValueError. Kept rather than deleted: if a future
        # CPython raises ValueError here for some other reason, this turns it into a
        # PolicyError instead of letting it escape the module as an unrelated traceback.
        # Unreachable today, deliberately, with the reason at the line - never a lowered
        # coverage threshold (TESTING.md).
            raise PolicyError(f"{DOWNLOAD_ENV_VAR} is set to {_echo(root)}, which is not a "
                               f"valid path: {exc}") from exc
        if not resolved_root.is_dir():
            kind = "does not exist" if not resolved_root.exists() else "is not a directory"
            if from_default:
                # Not an error: nobody asked for this path, so it must not stop the server or
                # the OTHER direction, which may be configured perfectly well.
                #
                # `_pending` keeps the path so `_adopt_pending_default` can look again when a
                # tool actually needs it. The decision is deliberately NOT frozen here: the
                # directory is the documented remedy, and a server that cached "absent" would
                # keep refusing after somebody followed its own instructions, telling them to
                # create a directory that now exists. Nothing is created either way.
                self.root = None
                self._pending = resolved_root
                self.warning = _MISSING_DEFAULT.format(
                    var=DOWNLOAD_ENV_VAR, path=resolved_root,
                    what="saving an incoming attachment")
                return
            raise PolicyError(
                f"{DOWNLOAD_ENV_VAR} is set to {_echo(root)}, but {resolved_root} {kind}. "
                f"Configure {DOWNLOAD_ENV_VAR} to point at a directory this server may write "
                f"downloaded attachments to.")
        self.root = resolved_root

    def resolve(self, filename: str) -> pathlib.Path:
        """The write-side containment check: resolve, then check containment on the RESOLVED
        path, against the configured root - the same bound `AttachmentPolicy.resolve` applies
        to a read, applied here to a file that by definition does not exist yet.

        `filename` is message-supplied - it comes from whichever attachment part of a Gmail
        message a stranger wrote. An absolute path or a `..` segment is refused outright rather
        than silently reinterpreted, which is exactly wrong for a value nobody trustworthy
        chose."""
        if self.root is None:
            _adopt_pending_default(self, DOWNLOAD_ENV_VAR)
        if self.root is None:
            raise PolicyError(
                self.warning or
                f"downloads are disabled: no download directory is configured. Set "
                f"{DOWNLOAD_ENV_VAR} to a directory this server may write downloaded "
                f"attachments to.")
        _refuse_nul(filename, lambda r: f"{_echo(r)} is not a valid path: it contains a NUL "
                                        f"byte.")
        candidate = pathlib.Path(filename)
        # `os.path.isabs` AS WELL AS `Path.is_absolute`, and the difference is only visible
        # on Windows: `WindowsPath("/etc/passwd").is_absolute()` is False, because pathlib
        # wants a drive letter before it calls a path absolute, while `ntpath.isabs` calls a
        # leading separator absolute. Without the first check this guard did not fire on
        # Windows for exactly the shape it exists to refuse, and `root / Path("/etc/passwd")`
        # discards the root entirely - it evaluates to `C:\etc\passwd`. The containment check
        # below still refused it, so nothing escaped, but the outright refusal this docstring
        # promises was inert on one platform (csa-google-gmail-calendar#29). On POSIX
        # `os.path.isabs` IS `posixpath.isabs`, so nothing changes there.
        if _is_rooted(filename) or candidate.is_absolute() or ".." in candidate.parts:
            raise PolicyError(
                f"{filename!r} is an invalid attachment filename (must be a plain relative "
                f"name, no path separators or '..'). Refused rather than guessing what was "
                f"meant.")
        target = self.root / candidate
        try:
            resolved = target.resolve(strict=False)
        except ValueError as exc:  # pragma: no cover - see _refuse_nul: the embedded-NUL case that used to reach
        # this arm is now refused before resolve() is called, and NUL was the only input
        # that made resolve() raise ValueError. Kept rather than deleted: if a future
        # CPython raises ValueError here for some other reason, this turns it into a
        # PolicyError instead of letting it escape the module as an unrelated traceback.
        # Unreachable today, deliberately, with the reason at the line - never a lowered
        # coverage threshold (TESTING.md).
            raise PolicyError(f"{_echo(filename)} is not a valid path: {exc}") from exc
        if not resolved.is_relative_to(self.root):
            raise PolicyError(
                f"{filename!r} resolves to a location outside the download directory "
                f"({self.root}). Refused.")
        return resolved

    def write(self, filename: str, content: bytes) -> pathlib.Path:
        """Write `content` under the configured download directory, refusing to overwrite
        anything already there. A message's attachment part can name itself anything, including
        the name of a file already sitting in the download directory - refusing the overwrite
        (rather than disambiguating the filename) is what closes the chain this class exists
        to close: a stranger's message must not be able to replace a file a person put there,
        any more than it should be able to replace one under `AttachmentPolicy`'s root."""
        resolved = self.resolve(filename)
        if resolved.exists():
            raise PolicyError(
                f"{filename!r} already exists at {resolved} - refused rather than overwritten. "
                f"A message's attachment can be named anything, including the name of a file "
                f"already in the download directory; retry with a different filename.")
        resolved.write_bytes(content)
        return resolved


def download_policy_from_env() -> DownloadPolicy:
    """`CSA_GGC_DOWNLOAD_DIR` if set, else `DEFAULT_DOWNLOAD_DIR` (`~/Downloads`)."""
    configured = os.environ.get(DOWNLOAD_ENV_VAR) or None
    if configured:
        return DownloadPolicy(configured)
    return DownloadPolicy(DEFAULT_DOWNLOAD_DIR, from_default=True)


def _adopt_pending_default(policy: AttachmentPolicy | DownloadPolicy, var: str) -> None:
    """Look again at a defaulted directory that was missing at construction.

    Called from `resolve()`, so `mkdir ~/CSA-Uploads` takes effect on the next tool
    call rather than on the next restart. Without this the server cached "absent" for its whole
    lifetime and went on refusing after somebody did exactly what it told them to, which is a
    worse failure than the original absence - the remedy appears not to work.

    Still creates nothing. This only notices.

    **Disjointness is re-asked here, not assumed.** The startup check ran when this root did not
    exist, so it proved nothing about it. A directory appearing later can collide with the other
    side - `CSA_GGC_DOWNLOAD_DIR=~`, with the default outbox nested directly inside it, is how
    that happens - and adopting without checking would reopen the exact hole
    `check_directories_disjoint` exists to close, just later and more quietly.
    """
    pending = policy._pending
    if pending is None or not pending.is_dir():
        return
    sibling = policy._sibling
    if sibling is not None and sibling.root is not None:
        try:
            overlap = _roots_overlap(pending, sibling.root)
        except OSError:
            return                      # cannot prove it safe, so do not adopt it
        if overlap is not None:
            policy.warning = (
                f"{pending} now exists, but it is the same directory as - or nested with - the "
                f"one the other direction uses ({sibling.root}), so it cannot be adopted. "
                f"Set {var} to a directory that is separate from it.")
            return
    policy.root = pending
    policy._pending = None
    policy.warning = None


def _roots_overlap(a: pathlib.Path, b: pathlib.Path) -> str | None:
    """`"nested"`, `"same"`, or `None`. Raises `OSError` if either path cannot be stat'd.

    Factored out so the startup check and the late-adoption check in `_adopt_pending_default`
    cannot drift. They ask the identical question - may these two directories coexist - and a
    second hand-written copy of `is_relative_to` plus `samefile` is exactly how the case-variant
    hole got into the shell script that mirrors this.

    Nesting is tested first and separately because `samefile` does not subsume it: a parent and
    its child are genuinely different inodes, so no identity test would ever flag that pair.
    """
    if a.is_relative_to(b) or b.is_relative_to(a):
        return "nested"
    if os.path.samefile(a, b):
        return "same"
    return None


def _describe_root(policy: AttachmentPolicy | DownloadPolicy, var: str) -> str:
    """`CSA_GGC_X (/path)` for a configured root, `the default for CSA_GGC_X (/path)` otherwise.

    A refusal that names two directories is only actionable if the reader can tell which one
    they set. When the answer is "neither", the phrasing has to say so - otherwise the remedy
    looks like changing a variable that is not set.
    """
    where = "the default for " if policy.from_default else ""
    return f"{where}{var} ({policy.root})"


def check_directories_disjoint(attach_policy: AttachmentPolicy | None,
                               download_policy: DownloadPolicy | None) -> None:
    """Refuse a configuration where `CSA_GGC_ATTACH_DIR` and `CSA_GGC_DOWNLOAD_DIR` name the
    same directory, or one nested inside the other. That configuration recreates the exact
    vulnerability `DownloadPolicy` exists to close (a stranger's downloaded attachment landing
    somewhere `send_message` can then pick up as if the user meant to send it) - it must be
    impossible to hold, not merely discouraged, so this is called once, at server construction
    (`mcp.server.create_server`), the one place both configured roots are ever in hand together.

    **Same-directory is checked with `os.path.samefile`, not `==` (fix round, final
    whole-branch review, CINO 2026-09-26 - a real gap, verified against the shipped code, not a
    hypothetical).** `Path.resolve()` does not canonicalise case, and a case-insensitive
    filesystem (default on macOS/APFS, this project's own development platform) treats
    `.../Shared` and `.../shared` as one directory on disk while `==` sees two different
    strings - so `CSA_GGC_ATTACH_DIR=~/Mail/Attach` and `CSA_GGC_DOWNLOAD_DIR=~/Mail/attach`
    used to sail through this check and land both variables on the same inode, restoring the
    exact overwrite chain FIX 1 exists to close. `os.path.samefile` compares `st_dev`/`st_ino`
    - the filesystem's own notion of identity - so it catches a case-variant the same way it
    already would a symlink, a hard link, or a bind mount: every way two names can denote one
    directory, not just the one this project happened to develop on. `is_relative_to` is kept
    alongside it, not replaced - `samefile` does not subsume nesting, since a parent and its
    child are genuinely different inodes and neither `==` nor `samefile` would ever flag that
    pair on its own.

    `samefile` raises `OSError` if either path cannot be stat'd (already guaranteed not to
    happen for a `root` that made it through `AttachmentPolicy`/`DownloadPolicy` construction,
    both of which require their root to exist as a directory - but a directory can still vanish
    between that construction and this call, e.g. a racing `rmdir`). That `OSError` is treated
    as a refusal, not silently allowed through: a directory this check cannot prove disjoint is
    not one it can call safe, and the permissive reading is exactly the Critical this whole
    function exists to close.
    """
    if attach_policy is None or download_policy is None:
        return
    # The one place both are in hand. Late adoption needs to re-ask this question and cannot
    # otherwise reach its counterpart.
    attach_policy._sibling = download_policy
    download_policy._sibling = attach_policy
    attach_root, download_root = attach_policy.root, download_policy.root
    if attach_root is None or download_root is None:
        return
    # Once both sides have defaults (#23), a collision can involve a directory the reader never
    # chose: `CSA_GGC_DOWNLOAD_DIR=~` collides with the DEFAULT attach root nested directly
    # inside it, and a message naming two paths would leave them hunting for a second variable
    # they never set. Saying which is which is the actionable half.
    a = _describe_root(attach_policy, ENV_VAR)
    d = _describe_root(download_policy, DOWNLOAD_ENV_VAR)
    # ONE guarded call. The first refactor of this ran the test twice - once outside the `try`
    # for nesting and once inside it for identity - which moved the `samefile` stat out from
    # under the handler, so a directory that vanished between construction and here escaped as a
    # bare OSError instead of the refusal this function promises. The existing test for that
    # caught it.
    try:
        overlap = _roots_overlap(attach_root, download_root)
    except OSError as exc:
        raise PolicyError(
            f"could not confirm {a} and {d} "
            f"are different directories: {exc}. Refused rather than "
            f"assumed disjoint - a directory this check cannot stat is not one it can prove "
            f"safe.") from exc
    if overlap == "nested":
        raise PolicyError(
            f"{a} and {d} must not be "
            f"the same directory, or nested inside one another. The directory outgoing mail "
            f"reads attachments from and the directory get_attachment writes downloaded "
            f"attachments to must be disjoint - otherwise anything a stranger sends could "
            f"overwrite, or later be picked up as, a file the user meant to send. Configure "
            f"them to point at two separate directories.")
    if overlap == "same":
        raise PolicyError(
            f"{a} and {d} name the "
            f"same directory (confirmed by device/inode, not just by spelling - e.g. two "
            f"names that differ only in case on a case-insensitive filesystem still land "
            f"here). The directory outgoing mail reads attachments from and the directory "
            f"get_attachment writes downloaded attachments to must be disjoint - otherwise "
            f"anything a stranger sends could overwrite, or later be picked up as, a file the "
            f"user meant to send. Configure them to point at two separate directories.")
