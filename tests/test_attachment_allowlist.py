import os
import pathlib
import sys

import pytest
from _platform import requires_symlinks

from csa_google_gmail_calendar._attachments import (
    AttachmentPolicy,
    DownloadPolicy,
    check_directories_disjoint,
    from_env,
)
from csa_google_gmail_calendar.exceptions import PolicyError


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "attach"
    d.mkdir()
    (d / "ok.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "secret.txt").write_bytes(b"not for sending")
    return d


def test_a_file_inside_the_root_resolves(root):
    p = AttachmentPolicy(str(root)).resolve("ok.pdf")
    assert p.name == "ok.pdf"


def test_absolute_path_inside_the_root_resolves(root):
    assert AttachmentPolicy(str(root)).resolve(str(root / "ok.pdf")).name == "ok.pdf"


def test_dotdot_escape_is_refused(root):
    """REVIEW FOCUS #1."""
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve("../secret.txt")


@requires_symlinks
def test_symlink_pointing_outside_is_refused_on_the_resolved_path(root):
    """REVIEW FOCUS #1. The check must run on the RESOLVED path. Checking the supplied
    string would pass this — the link is inside the root and its target is not."""
    (root / "innocent.txt").symlink_to(root.parent / "secret.txt")
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve("innocent.txt")


@requires_symlinks
def test_symlink_pointing_inside_is_allowed(root):
    (root / "alias.pdf").symlink_to(root / "ok.pdf")
    assert AttachmentPolicy(str(root)).resolve("alias.pdf").name == "ok.pdf"


@requires_symlinks
def test_a_root_that_is_itself_a_symlink_still_works(root, tmp_path):
    """The root is resolved too, or every path under a symlinked root looks like an escape."""
    link = tmp_path / "via-link"
    link.symlink_to(root)
    assert AttachmentPolicy(str(link)).resolve("ok.pdf").name == "ok.pdf"


def test_unconfigured_root_refuses_and_names_the_variable():
    """DEC-020 and the csa-skilljar idiom: it may be one environment variable away."""
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        AttachmentPolicy(None).resolve("anything.pdf")


def test_a_directory_is_not_an_attachment(root):
    (root / "sub").mkdir()
    with pytest.raises(PolicyError, match="not a regular file"):
        AttachmentPolicy(str(root)).resolve("sub")


def test_a_missing_file_says_missing_not_outside(root):
    """Two different problems. 'outside the allowlist' for a file that does not exist sends
    the user looking for a permissions problem they do not have."""
    with pytest.raises(PolicyError, match="does not exist"):
        AttachmentPolicy(str(root)).resolve("nope.pdf")


def test_tilde_is_expanded(root, monkeypatch):
    # Both, because `os.path.expanduser` reads HOME on POSIX and USERPROFILE on Windows -
    # the rule conftest states for the whole suite. Setting only HOME left `~` pointing at
    # conftest's isolated home on Windows, so the path resolved outside `root` and was
    # (correctly) refused, failing a test about expansion for a reason about containment.
    monkeypatch.setenv("HOME", str(root.parent))
    monkeypatch.setenv("USERPROFILE", str(root.parent))
    assert AttachmentPolicy(str(root)).resolve("~/attach/ok.pdf").name == "ok.pdf"


def test_from_env_reads_the_variable(root, monkeypatch):
    monkeypatch.setenv("CSA_GGC_ATTACH_DIR", str(root))
    assert from_env().resolve("ok.pdf").name == "ok.pdf"


def test_from_env_with_no_variable_uses_the_default_when_it_exists(tmp_path, monkeypatch):
    """#23 gave this a default; #25 stopped it creating one. The directory has to already be
    there, which for the send side means a person made it deliberately."""
    monkeypatch.delenv("CSA_GGC_ATTACH_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    outbox = tmp_path / "Documents" / "CSA-Outbox"
    outbox.mkdir(parents=True)

    policy = from_env()

    assert policy.from_default is True
    assert policy.root == outbox.resolve()
    assert policy.warning is None


def test_a_missing_default_is_left_off_and_never_created(tmp_path, monkeypatch):
    """**Safe by default beats working by surprise (#25).** Creating the directory would make
    the feature work by writing into somebody's home because a program started - a side effect
    nobody sanctioned, for a path nobody chose. So: server starts, direction off, reason said."""
    monkeypatch.delenv("CSA_GGC_ATTACH_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    policy = from_env()

    assert policy.root is None
    assert not (tmp_path / "Documents").exists(), "a default must never be created"
    assert "does not exist" in policy.warning
    assert "CSA_GGC_ATTACH_DIR" in policy.warning


def test_the_refusal_says_the_directory_is_missing_not_that_none_is_configured(
        tmp_path, monkeypatch):
    """"No attachment directory is configured" would be FALSE here - one is, by default, and it
    merely is not there. It also sends the reader to set a variable when making a directory is
    the shorter fix."""
    monkeypatch.delenv("CSA_GGC_ATTACH_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    with pytest.raises(PolicyError, match="does not exist"):
        from_env().resolve("x.pdf")


def test_an_explicitly_set_directory_that_is_missing_is_still_refused(tmp_path, monkeypatch):
    """The half that must NOT change. Creating a missing root is a concession to a path the
    operator never chose; a path they DID choose and got wrong must still fail loudly at
    startup, which is the entire reason this is checked at construction."""
    monkeypatch.setenv("CSA_GGC_ATTACH_DIR", str(tmp_path / "typo"))
    with pytest.raises(PolicyError, match="does not exist"):
        from_env()
    assert not (tmp_path / "typo").exists(), "an explicit root must never be created"


def test_read_returns_bytes_and_the_basename(root):
    content, name = AttachmentPolicy(str(root)).read("ok.pdf")
    assert content == b"%PDF-1.4 fake" and name == "ok.pdf"


def test_read_refuses_an_escape_and_returns_no_bytes(root):
    """FIX 1 (review round 1). Every other refusal test goes through resolve() — this is the
    one that pins the property where it actually matters, at read(). If read() were ever
    changed to re-derive a path from the string, call open() directly, or swallow PolicyError,
    every other test in this file would stay green while the control quietly disappeared."""
    secret = root.parent / "secret.txt"
    secret.write_bytes(b"DISTINCTIVE-SECRET-PAYLOAD-92f1")
    with pytest.raises(PolicyError, match="outside") as exc_info:
        AttachmentPolicy(str(root)).read("../secret.txt")
    assert b"DISTINCTIVE-SECRET-PAYLOAD-92f1" not in str(exc_info.value).encode()


@requires_symlinks
def test_read_refuses_a_symlink_to_outside_and_returns_no_bytes(root):
    """The escape a reader is most likely to assume resolve() alone covers."""
    secret = root.parent / "secret.txt"
    secret.write_bytes(b"DISTINCTIVE-SECRET-PAYLOAD-92f1")
    (root / "innocent.txt").symlink_to(secret)
    with pytest.raises(PolicyError, match="outside") as exc_info:
        AttachmentPolicy(str(root)).read("innocent.txt")
    assert b"DISTINCTIVE-SECRET-PAYLOAD-92f1" not in str(exc_info.value).encode()


def test_missing_root_directory_is_refused_at_construction_naming_the_variable(tmp_path):
    """FIX 3 (review round 1). Path.resolve() defaults to strict=False, so a typo'd root would
    otherwise construct silently and answer every call with a 'file does not exist' error that
    sends the operator hunting for a missing file rather than a misconfigured directory."""
    missing = tmp_path / "no-such-directory"
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        AttachmentPolicy(str(missing))


def test_root_that_is_a_file_not_a_directory_is_refused_at_construction(tmp_path):
    not_a_dir = tmp_path / "im-a-file"
    not_a_dir.write_bytes(b"x")
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        AttachmentPolicy(str(not_a_dir))


# --- Adversarial cases beyond the brief's 13 ---


def test_embedded_nul_byte_is_a_policy_refusal_not_a_bare_valueerror(root):
    """Path.resolve() raises a bare ValueError on an embedded NUL, even with strict=False.
    Left uncaught, that ValueError would escape this module wearing no relation at all to
    the attachment policy. It must surface as a PolicyError instead."""
    with pytest.raises(PolicyError, match="not a valid path"):
        AttachmentPolicy(str(root)).resolve("ok.pdf\x00.txt")


def test_an_embedded_nul_byte_in_the_root_itself_is_a_policy_refusal_at_construction(tmp_path):
    """The same `Path.resolve()` ValueError as above, but from the CONSTRUCTOR's own resolve of
    `root` (`CSA_GGC_ATTACH_DIR` itself), not from a per-call `.resolve()` on a caller-supplied
    filename - a separate try/except, so covering one does not cover the other."""
    with pytest.raises(PolicyError, match="not a valid path"):
        AttachmentPolicy(str(tmp_path) + "\x00bad")


def test_a_long_offending_path_is_echoed_truncated_not_reproduced_whole(root):
    """The echoed path is capped at 200 chars: it may come from a model and is unbounded, and
    exception messages tend to end up in logs."""
    long_name = "a" * 250 + ".pdf"
    with pytest.raises(PolicyError, match="truncated"):
        AttachmentPolicy(str(root)).resolve(long_name)


def test_deep_dotdot_walking_above_the_filesystem_root_is_still_refused_as_outside(root):
    """Enough `../` segments to walk past `/` and back down into a real path elsewhere on
    disk. resolve() collapses this like any other `..` chain; the refusal is still the
    'outside' message, not a crash or an unbounded loop."""
    escape = "../" * 40 + "etc/passwd"
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve(escape)


@requires_symlinks
def test_symlink_chain_of_two_links_is_fully_followed(root, tmp_path):
    """link1 (inside root) -> link2 (inside root) -> secret.txt (outside root). One call to
    resolve() must follow the *whole* chain, not just one hop."""
    secret = tmp_path / "secret.txt"
    link2 = root / "link2"
    link2.symlink_to(secret)
    link1 = root / "link1"
    link1.symlink_to(link2)
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve("link1")


@requires_symlinks
def test_directory_symlink_reached_through_is_refused(root, tmp_path):
    """A symlink *component* in the middle of the path, not just the final segment: `root/
    dirlink` points at an outside directory, and the supplied path reaches a file through it."""
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "leaked.txt").write_bytes(b"leaked")
    (root / "dirlink").symlink_to(outside_dir)
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve("dirlink/leaked.txt")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="mkfifo is POSIX-only")
def test_a_fifo_inside_the_root_is_refused_not_read(root):
    """is_file() is False for a FIFO, so this must be refused before read_bytes() is ever
    called — reading a FIFO with nothing on the other end blocks forever, which is a hang,
    not a leak, but still not a thing this policy should walk into."""
    fifo_path = root / "pipe"
    os.mkfifo(fifo_path)
    with pytest.raises(PolicyError, match="not a regular file"):
        AttachmentPolicy(str(root)).resolve("pipe")


@pytest.mark.skipif(sys.platform != "darwin", reason="exercises case-insensitive-fs behaviour")
def test_case_variant_of_the_root_never_escapes_the_containment_check(root):
    """On a case-insensitive filesystem (default macOS/APFS), Path.resolve() does not
    case-normalize to the on-disk spelling. A same-file-different-case absolute path may
    exist per the OS, but the containment check is a case-sensitive string comparison, so this
    can only ever be refused — never wrongly allowed through. This test documents that the
    failure mode is refusal, not escape."""
    variant = pathlib.Path(str(root).upper()) / "OK.PDF"
    if not variant.exists():
        pytest.skip("this tmp path happens to be case-sensitive on this run")
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve(str(variant))


def test_two_different_names_for_one_directory_are_refused_on_every_platform(tmp_path, monkeypatch):
    """The inode check refuses, independent of whether this filesystem can produce the case.

    `is_relative_to` above already catches identical paths, so `samefile` only matters when two
    *textually different* paths share an inode. On macOS APFS a case-variant does that; on a
    case-sensitive Linux filesystem it genuinely cannot, which is why the companion test above is
    `skipif(darwin)` — and why CI measured this branch as uncovered while the laptop did not.

    Skipping it on Linux would leave the *logic* untested on the platform this actually deploys to.
    So this asserts the rule rather than the filesystem: when `samefile` reports one directory, the
    configuration is refused. That is the property; the case-variant is only one way to reach it,
    and a bind mount or a future filesystem quirk is another.
    """
    attach = tmp_path / "attach"
    attach.mkdir()
    download = tmp_path / "download"
    download.mkdir()
    monkeypatch.setattr(os.path, "samefile", lambda a, b: True)
    with pytest.raises(PolicyError, match="same directory"):
        check_directories_disjoint(AttachmentPolicy(str(attach)), DownloadPolicy(str(download)))
