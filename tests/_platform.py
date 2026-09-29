"""Platform capabilities the suite probes for, rather than infers from `sys.platform`.

Only one so far: whether this process may create a symbolic link.

Windows gates `os.symlink` behind `SeCreateSymbolicLinkPrivilege`, which an ordinary
user does not hold - an unelevated developer shell gets `OSError: [WinError 1314] A
required privilege is not held by the client` before the test under test runs at all.
An elevated shell, or one with Developer Mode enabled, succeeds. GitHub's
`windows-latest` runner is in the second group, so these tests are expected to EXECUTE
in CI and to skip on a typical developer box.

That asymmetry is the point, and it is why this is a runtime probe rather than
`skipif(sys.platform == "win32")`. The tests guarded by this marker are the ones that
prove symlink escapes are refused. Keying the skip to the platform would retire that
proof on Windows permanently, including on a runner that was perfectly capable of
running it. Keying it to the capability means the control is verified wherever it CAN
be verified, and the skip is an honest statement about one machine rather than a
standing exemption.

When it does skip, it is saying the control is UNVERIFIED here - not that it does not
apply. That distinction is the whole of csa-google-workspace#452: three mitigations
named in a threat model turned out to be POSIX-only no-ops, and nothing said so.
"""
import pathlib
import tempfile

import pytest


def _can_create_symlinks() -> bool:
    """Try it once, in a throwaway directory, and believe the answer."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        try:
            (root / "link").symlink_to(root / "target")
        except (OSError, NotImplementedError, AttributeError):
            return False
        return True


def _chmod_can_remove_read_access() -> bool:
    """`chmod(0o000)` then try to read it. On Windows the call succeeds and changes
    nothing that matters - only the read-only bit is honoured, so the owner can still
    read the file - which makes any test that builds "unreadable" out of `chmod` a test
    of a precondition that was never established."""
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "probe"
        p.write_text("x", encoding="utf-8")
        try:
            p.chmod(0o000)
            try:
                p.read_text(encoding="utf-8")
                return False
            except OSError:
                return True
        finally:
            p.chmod(0o600)


CAN_CREATE_SYMLINKS = _can_create_symlinks()
CHMOD_REMOVES_READ_ACCESS = _chmod_can_remove_read_access()

requires_symlinks = pytest.mark.skipif(
    not CAN_CREATE_SYMLINKS,
    reason=(
        "this process cannot create symlinks, so the symlink-escape refusal it proves "
        "is UNVERIFIED on this machine (Windows needs an elevated shell or Developer "
        "Mode; see csa-google-gmail-calendar#29)"
    ),
)

requires_chmod_read_removal = pytest.mark.skipif(
    not CHMOD_REMOVES_READ_ACCESS,
    reason=(
        "chmod cannot remove the owner's read access here, so an 'unreadable file' "
        "precondition cannot be built this way (Windows; see csa-google-workspace#452). "
        "The same code path is covered platform-independently by the directory variant "
        "of this test."
    ),
)
