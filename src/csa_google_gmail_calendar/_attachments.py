"""Which local files may be attached to outgoing mail.

`send_message(attachments=[...])` takes a path, which makes it a file-read primitive wearing
an innocuous name. The bound is a single configured directory, and it is checked on the
**resolved** path: a symlink inside the root pointing at `~/.ssh/id_rsa` is inside the root by
its own name and outside it in every way that matters. `Path.resolve()` follows an entire
symlink chain (link to link to target) and walks through a symlinked directory component, not
just a symlinked final component, so both are caught by the same check rather than needing
special cases.

Unset means attachments are off, not unrestricted. That is the default-posture rule from spec
§3 applied to the filesystem, and the refusal names the variable — a capability that is one
environment variable away should say so rather than look broken (the csa-skilljar idiom). A
configured root that does not exist, or exists but is not a directory, is refused the same way,
at construction: a typo'd directory should not silently produce an object that answers every
call with "the file does not exist", sending the operator hunting for a missing file instead of
a broken configuration.

**Residual risk — TOCTOU.** `resolve()` followed by a read is two syscalls, and the filesystem
can change between them (a file swapped for a symlink after the check, before the read). This
is not fully closable from Python without `openat`-style primitives scoped to an open directory
file descriptor, which this module does not use. What it does instead: `resolve()` is called
exactly once per attachment, and `read()` re-opens the file by that same resolved path *string*
without re-deriving it from the caller's original, unresolved input — no file descriptor is
held open across the check, so this narrows the window (nothing re-parses the untrusted string
a second time) without closing it. Treat this as a residual risk, not a guarantee: a local
attacker who can race the filesystem between resolve and read is out of scope for this module.

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

# A refused path is echoed into the exception message so the caller can see what was rejected.
# The caller's string is untrusted and unbounded (it may come from a model), and exception
# messages tend to end up in logs, so what gets echoed is capped rather than reproduced whole.
_MAX_ECHO = 200


def _echo(path: str) -> str:
    if len(path) <= _MAX_ECHO:
        return repr(path)
    return f"{path[:_MAX_ECHO]!r}... ({len(path)} chars, truncated)"


class AttachmentPolicy:
    def __init__(self, root: str | None) -> None:
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
        try:
            resolved_root = pathlib.Path(os.path.expanduser(root)).resolve(strict=False)
        except ValueError as exc:
            raise PolicyError(f"{ENV_VAR} is set to {_echo(root)}, which is not a valid path: "
                               f"{exc}") from exc
        if not resolved_root.is_dir():
            kind = "does not exist" if not resolved_root.exists() else "is not a directory"
            raise PolicyError(
                f"{ENV_VAR} is set to {_echo(root)}, but {resolved_root} {kind}. Configure "
                f"{ENV_VAR} to point at a directory this server may read attachments from.")
        self.root = resolved_root

    def resolve(self, path: str) -> pathlib.Path:
        if self.root is None:
            raise PolicyError(
                f"attachments are disabled: no attachment directory is configured. Set "
                f"{ENV_VAR} to a directory this server may read files from, and only files "
                f"under it can be attached.")
        candidate = pathlib.Path(os.path.expanduser(path))
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            # strict=False so a MISSING file reaches the readable error below rather than
            # raising OSError from resolve() itself. A NUL byte embedded in the path makes
            # the underlying lstat() raise ValueError even with strict=False — caught below
            # so it surfaces as a refusal rather than an uncaught ValueError escaping this
            # module wearing no relation to the attachment policy at all.
            resolved = candidate.resolve(strict=False)
        except ValueError as exc:
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
    return AttachmentPolicy(os.environ.get(ENV_VAR) or None)
