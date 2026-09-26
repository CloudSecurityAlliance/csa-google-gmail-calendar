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
environment variable away should say so rather than look broken (the csa-skilljar idiom).

**Residual risk — TOCTOU.** `resolve()` followed by a read is two syscalls, and the filesystem
can change between them (a file swapped for a symlink after the check, before the read). This
is not fully closable from Python without `openat`-style primitives scoped to an open directory
file descriptor, which this module does not use. What it does instead: `resolve()` is called
exactly once per attachment, and the read goes through the same resolved `Path` object rather
than re-deriving or re-resolving the string — this narrows the window to the smallest this
implementation can make it, it does not close it. Treat this as a residual risk, not a
guarantee: a local attacker who can race the filesystem between resolve and read is out of
scope for this module.

**Case-insensitive filesystems** (default on macOS/APFS): `Path.resolve()` does not
case-normalize a path to its on-disk spelling — it only follows symlinks and collapses `.`/
`..`. A supplied path that differs only in case from the configured root's real spelling will
therefore fail the (case-sensitive, string-prefix) containment check even though the OS would
resolve it to the same file. That failure mode is refusal, never escape: it can make this
policy reject a legitimate same-file path on a case-insensitive filesystem, it cannot make it
accept one that is actually outside the root.
"""
from __future__ import annotations

import os
import pathlib

from .exceptions import PolicyError

ENV_VAR = "CSA_GGC_ATTACH_DIR"


class AttachmentPolicy:
    def __init__(self, root: str | None) -> None:
        # Resolved at construction, once. A root that is itself a symlink (common: a
        # ~/Documents that points into a synced volume) would otherwise make every path
        # under it compare as an escape.
        self.root = pathlib.Path(os.path.expanduser(root)).resolve() if root else None

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
            raise PolicyError(f"{path!r} is not a valid path: {exc}") from exc
        if not resolved.is_relative_to(self.root):
            raise PolicyError(
                f"{path!r} resolves to a location outside the attachment directory "
                f"({self.root}). Only files under that directory can be attached.")
        if not resolved.exists():
            raise PolicyError(f"{path!r} does not exist (looked at {resolved}).")
        if not resolved.is_file():
            # Also the backstop for a FIFO or device file living inside the root: is_file()
            # is False for those (it stats the target, not just "is there a directory entry"),
            # so they are refused here rather than reaching read_bytes(), which would block
            # forever on a FIFO with nothing on the other end - a hang, not a leak, but still
            # not something this policy should walk into.
            raise PolicyError(f"{path!r} is not a regular file.")
        return resolved

    def read(self, path: str) -> tuple[bytes, str]:
        p = self.resolve(path)
        return p.read_bytes(), p.name


def from_env() -> AttachmentPolicy:
    return AttachmentPolicy(os.environ.get(ENV_VAR) or None)
