import os
import pathlib
import sys

import pytest

from csa_google_gmail_calendar._attachments import AttachmentPolicy, from_env
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


def test_symlink_pointing_outside_is_refused_on_the_resolved_path(root):
    """REVIEW FOCUS #1. The check must run on the RESOLVED path. Checking the supplied
    string would pass this — the link is inside the root and its target is not."""
    (root / "innocent.txt").symlink_to(root.parent / "secret.txt")
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve("innocent.txt")


def test_symlink_pointing_inside_is_allowed(root):
    (root / "alias.pdf").symlink_to(root / "ok.pdf")
    assert AttachmentPolicy(str(root)).resolve("alias.pdf").name == "ok.pdf"


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
    monkeypatch.setenv("HOME", str(root.parent))
    assert AttachmentPolicy(str(root)).resolve("~/attach/ok.pdf").name == "ok.pdf"


def test_from_env_reads_the_variable(root, monkeypatch):
    monkeypatch.setenv("CSA_GGC_ATTACH_DIR", str(root))
    assert from_env().resolve("ok.pdf").name == "ok.pdf"


def test_from_env_with_no_variable_is_a_policy_that_refuses_everything(monkeypatch):
    monkeypatch.delenv("CSA_GGC_ATTACH_DIR", raising=False)
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        from_env().resolve("x.pdf")


def test_read_returns_bytes_and_the_basename(root):
    content, name = AttachmentPolicy(str(root)).read("ok.pdf")
    assert content == b"%PDF-1.4 fake" and name == "ok.pdf"


# --- Adversarial cases beyond the brief's 13 ---


def test_embedded_nul_byte_is_a_policy_refusal_not_a_bare_valueerror(root):
    """Path.resolve() raises a bare ValueError on an embedded NUL, even with strict=False.
    Left uncaught, that ValueError would escape this module wearing no relation at all to
    the attachment policy. It must surface as a PolicyError instead."""
    with pytest.raises(PolicyError, match="not a valid path"):
        AttachmentPolicy(str(root)).resolve("ok.pdf\x00.txt")


def test_deep_dotdot_walking_above_the_filesystem_root_is_still_refused_as_outside(root):
    """Enough `../` segments to walk past `/` and back down into a real path elsewhere on
    disk. resolve() collapses this like any other `..` chain; the refusal is still the
    'outside' message, not a crash or an unbounded loop."""
    escape = "../" * 40 + "etc/passwd"
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve(escape)


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
    with pytest.raises(PolicyError):
        AttachmentPolicy(str(root)).resolve(str(variant))
