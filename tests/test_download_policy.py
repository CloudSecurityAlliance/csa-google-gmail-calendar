"""`DownloadPolicy` - the write-side counterpart to `AttachmentPolicy` (`test_attachment_allowlist.py`),
added by FIX 1 of the final whole-branch review (CINO 2026-09-26) to close the chain where
`get_attachment` used to write into the SAME directory `send_message` reads outgoing
attachments from: a stranger's message could overwrite a real file there, or simply place a new
one a model might later attach as if the user meant to send it.
"""
import pytest

from csa_google_gmail_calendar._attachments import (
    AttachmentPolicy,
    DownloadPolicy,
    check_directories_disjoint,
    download_policy_from_env,
)
from csa_google_gmail_calendar.exceptions import PolicyError


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "downloads"
    d.mkdir()
    return d


def test_a_filename_inside_the_root_resolves(root):
    p = DownloadPolicy(str(root)).resolve("note.txt")
    assert p.parent == root


def test_dotdot_is_refused(root):
    with pytest.raises(PolicyError, match="invalid"):
        DownloadPolicy(str(root)).resolve("../escaped.txt")


def test_an_absolute_filename_is_refused(root):
    with pytest.raises(PolicyError, match="invalid"):
        DownloadPolicy(str(root)).resolve("/etc/passwd")


def test_a_symlinked_subdirectory_escape_is_refused_on_the_resolved_path(root, tmp_path):
    (tmp_path / "outside").mkdir()
    (root / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(PolicyError, match="outside"):
        DownloadPolicy(str(root)).resolve("escape/pwned.txt")


def test_unconfigured_root_refuses_and_names_the_variable():
    with pytest.raises(PolicyError, match="CSA_GGC_DOWNLOAD_DIR"):
        DownloadPolicy(None).resolve("anything.pdf")


def test_missing_root_directory_is_refused_at_construction_naming_the_variable(tmp_path):
    missing = tmp_path / "no-such-directory"
    with pytest.raises(PolicyError, match="CSA_GGC_DOWNLOAD_DIR"):
        DownloadPolicy(str(missing))


def test_root_that_is_a_file_not_a_directory_is_refused_at_construction(tmp_path):
    not_a_dir = tmp_path / "im-a-file"
    not_a_dir.write_bytes(b"x")
    with pytest.raises(PolicyError, match="CSA_GGC_DOWNLOAD_DIR"):
        DownloadPolicy(str(not_a_dir))


def test_write_creates_the_file_with_the_given_content(root):
    resolved = DownloadPolicy(str(root)).write("note.txt", b"hello")
    assert resolved.read_bytes() == b"hello"
    assert resolved.parent == root


def test_write_refuses_to_overwrite_an_existing_file(root):
    """The end-to-end property FIX 1 exists for: a stranger's attachment must not be able to
    replace a file already in the download directory, whatever its name."""
    existing = root / "q3-budget.pdf"
    existing.write_bytes(b"THE USER'S REAL BUDGET")
    with pytest.raises(PolicyError, match="already exists"):
        DownloadPolicy(str(root)).write("q3-budget.pdf", b"ATTACKER CONTROLLED BYTES")
    assert existing.read_bytes() == b"THE USER'S REAL BUDGET"


def test_embedded_nul_byte_in_the_root_is_a_policy_refusal_not_a_bare_valueerror(tmp_path):
    with pytest.raises(PolicyError, match="not a valid path"):
        DownloadPolicy(str(tmp_path) + "\x00bogus")


def test_embedded_nul_byte_in_the_filename_is_a_policy_refusal_not_a_bare_valueerror(root):
    with pytest.raises(PolicyError, match="not a valid path"):
        DownloadPolicy(str(root)).resolve("note.txt\x00.pdf")


def test_write_refuses_an_escape_and_writes_nothing(root, tmp_path):
    secret = tmp_path / "secret.txt"
    with pytest.raises(PolicyError, match="invalid"):
        DownloadPolicy(str(root)).write("../secret.txt", b"payload")
    assert not secret.exists()


def test_from_env_reads_the_variable(root, monkeypatch):
    monkeypatch.setenv("CSA_GGC_DOWNLOAD_DIR", str(root))
    p = download_policy_from_env()
    assert p.write("a.txt", b"x").parent == root


def test_from_env_with_no_variable_is_a_policy_that_refuses_everything(monkeypatch):
    monkeypatch.delenv("CSA_GGC_DOWNLOAD_DIR", raising=False)
    with pytest.raises(PolicyError, match="CSA_GGC_DOWNLOAD_DIR"):
        download_policy_from_env().resolve("x.pdf")


# --- check_directories_disjoint -----------------------------------------------------------

def test_disjoint_directories_are_allowed(tmp_path):
    a = tmp_path / "attach"
    d = tmp_path / "download"
    a.mkdir()
    d.mkdir()
    check_directories_disjoint(AttachmentPolicy(str(a)), DownloadPolicy(str(d)))  # no raise


def test_the_same_directory_for_both_is_refused_naming_both_variables(tmp_path):
    same = tmp_path / "same"
    same.mkdir()
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR") as exc_info:
        check_directories_disjoint(AttachmentPolicy(str(same)), DownloadPolicy(str(same)))
    assert "CSA_GGC_DOWNLOAD_DIR" in str(exc_info.value)


def test_download_dir_nested_inside_attach_dir_is_refused(tmp_path):
    a = tmp_path / "attach"
    a.mkdir()
    d = a / "downloads"
    d.mkdir()
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        check_directories_disjoint(AttachmentPolicy(str(a)), DownloadPolicy(str(d)))


def test_attach_dir_nested_inside_download_dir_is_refused(tmp_path):
    d = tmp_path / "download"
    d.mkdir()
    a = d / "attach"
    a.mkdir()
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        check_directories_disjoint(AttachmentPolicy(str(a)), DownloadPolicy(str(d)))


def test_disjoint_check_is_a_noop_when_either_side_is_unconfigured(tmp_path):
    d = tmp_path / "download"
    d.mkdir()
    check_directories_disjoint(None, DownloadPolicy(str(d)))
    check_directories_disjoint(AttachmentPolicy(None), DownloadPolicy(str(d)))
    check_directories_disjoint(AttachmentPolicy(str(d)), None)
