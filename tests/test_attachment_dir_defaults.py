"""The attachment/download directory defaults (#23), and the one thing they complicate.

Before this, both variables were unset by default and both halves of the attachment feature were
off on a fresh install. Defensible - it is spec §3's default posture applied to the filesystem -
and the practical effect was that nobody used attachments at all.

The complication is that the two roots must stay **disjoint**: `check_directories_disjoint`
refuses at server construction when they are the same directory or one nests in the other,
because a stranger's downloaded attachment landing inside the root `send_message` reads from is
the exact chain that check exists to break. So they cannot both default to `~/Downloads`, and a
collision can now involve a directory the operator never chose - which is what most of the tests
below are about.
"""
import pytest

from csa_google_gmail_calendar._attachments import (
    DEFAULT_ATTACH_DIR,
    DEFAULT_DOWNLOAD_DIR,
    AttachmentPolicy,
    DownloadPolicy,
    check_directories_disjoint,
    download_policy_from_env,
    from_env,
)
from csa_google_gmail_calendar.exceptions import PolicyError


class TestTheDefaultsAreDisjointByConstruction:
    def test_the_two_defaults_are_not_the_same_directory(self, tmp_path, monkeypatch):
        """If this ever fails, a fresh install cannot start at all - `create_server` calls
        `check_directories_disjoint` unconditionally. Asserted on the real default strings
        rather than on the idea, so a later edit to either constant is caught here."""
        monkeypatch.delenv("CSA_GGC_ATTACH_DIR", raising=False)
        monkeypatch.delenv("CSA_GGC_DOWNLOAD_DIR", raising=False)

        check_directories_disjoint(from_env(), download_policy_from_env())

    def test_downloads_is_not_the_send_side_default(self):
        """The whole reason the send-side default is a CSA-specific directory. `~/Downloads` is
        where a stranger's attachment lands; a default that made it readable by `send_message`
        would ship C1 as the out-of-the-box configuration."""
        assert DEFAULT_DOWNLOAD_DIR == "~/Downloads"
        assert DEFAULT_ATTACH_DIR != DEFAULT_DOWNLOAD_DIR
        assert not DEFAULT_ATTACH_DIR.startswith(DEFAULT_DOWNLOAD_DIR)


class TestDefaultVersusExplicit:
    def test_a_defaulted_root_is_created(self, tmp_path):
        policy = AttachmentPolicy(str(tmp_path / "made" / "up"), from_default=True)
        assert policy.root.is_dir()
        assert policy.from_default is True

    def test_an_explicit_root_is_never_created(self, tmp_path):
        """The property that must survive: a path the operator asserted and got wrong fails
        loudly at startup. Creating it would turn a typo into a silently-empty directory and
        send them hunting for a missing file instead of a broken configuration."""
        with pytest.raises(PolicyError, match="does not exist"):
            AttachmentPolicy(str(tmp_path / "typo"))
        assert not (tmp_path / "typo").exists()

    def test_the_same_split_applies_to_downloads(self, tmp_path):
        assert DownloadPolicy(str(tmp_path / "made"), from_default=True).root.is_dir()
        with pytest.raises(PolicyError, match="does not exist"):
            DownloadPolicy(str(tmp_path / "typo"))

    def test_an_explicit_variable_still_wins_over_the_default(self, tmp_path, monkeypatch):
        chosen = tmp_path / "chosen"
        chosen.mkdir()
        monkeypatch.setenv("CSA_GGC_ATTACH_DIR", str(chosen))
        policy = from_env()
        assert policy.root == chosen.resolve()
        assert policy.from_default is False

    def test_an_undeletable_default_names_the_variable_to_set_instead(self, tmp_path):
        """A default that cannot be created must not read as a bug in the program. The remedy
        is the same one an explicit bad path gets - set the variable somewhere usable - so the
        message has to name it."""
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file, not a directory")
        with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
            AttachmentPolicy(str(blocker / "under" / "a" / "file"), from_default=True)


class TestTheDisjointnessRefusalSaysWhichPathYouChose:
    """Once both sides have defaults, a collision can name a directory the reader never set -
    and a message listing two paths without saying which is which leaves them looking for a
    variable that is not in their environment."""

    def _collide(self, tmp_path, *, attach_default, download_default):
        shared = tmp_path / "shared"
        shared.mkdir()
        return (AttachmentPolicy(str(shared), from_default=attach_default),
                DownloadPolicy(str(shared), from_default=download_default))

    def test_an_explicit_pair_names_both_variables_plainly(self, tmp_path):
        a, d = self._collide(tmp_path, attach_default=False, download_default=False)
        with pytest.raises(PolicyError) as ei:
            check_directories_disjoint(a, d)
        msg = str(ei.value)
        assert "CSA_GGC_ATTACH_DIR" in msg and "CSA_GGC_DOWNLOAD_DIR" in msg
        assert "the default for" not in msg

    def test_a_defaulted_side_is_labelled_as_the_default(self, tmp_path):
        a, d = self._collide(tmp_path, attach_default=True, download_default=False)
        with pytest.raises(PolicyError) as ei:
            check_directories_disjoint(a, d)
        msg = str(ei.value)
        assert "the default for CSA_GGC_ATTACH_DIR" in msg
        assert "the default for CSA_GGC_DOWNLOAD_DIR" not in msg

    def test_nesting_is_reported_with_the_same_labelling(self, tmp_path):
        """`samefile` does not subsume nesting - a parent and its child are different inodes -
        so the nesting branch is a separate message and needs the same treatment."""
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        with pytest.raises(PolicyError) as ei:
            check_directories_disjoint(AttachmentPolicy(str(child), from_default=True),
                                       DownloadPolicy(str(parent)))
        assert "the default for CSA_GGC_ATTACH_DIR" in str(ei.value)
