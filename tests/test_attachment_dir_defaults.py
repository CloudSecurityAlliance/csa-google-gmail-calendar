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
        rather than on the idea, so a later edit to either constant is caught here.

        **Both directories are created first, on purpose.** Since #25 a missing default yields
        `root is None`, and `check_directories_disjoint` returns early on a None root - so
        without these mkdirs the check would pass by never running, which is the shape of a
        test that cannot fail."""
        monkeypatch.delenv("CSA_GGC_ATTACH_DIR", raising=False)
        monkeypatch.delenv("CSA_GGC_DOWNLOAD_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        (tmp_path / "Documents" / "CSA-Outbox").mkdir(parents=True)
        (tmp_path / "Downloads").mkdir()

        attach, download = from_env(), download_policy_from_env()
        assert attach.root is not None and download.root is not None, "both must be live"

        check_directories_disjoint(attach, download)

    def test_downloads_is_not_the_send_side_default(self):
        """The whole reason the send-side default is a CSA-specific directory. `~/Downloads` is
        where a stranger's attachment lands; a default that made it readable by `send_message`
        would ship C1 as the out-of-the-box configuration."""
        assert DEFAULT_DOWNLOAD_DIR == "~/Downloads"
        assert DEFAULT_ATTACH_DIR != DEFAULT_DOWNLOAD_DIR
        assert not DEFAULT_ATTACH_DIR.startswith(DEFAULT_DOWNLOAD_DIR)


class TestDefaultVersusExplicit:
    def test_a_missing_defaulted_root_is_off_not_created_and_not_fatal(self, tmp_path):
        """#25 reversed #23's original answer here. Three options existed for a default that
        does not exist - create it, refuse to start, or leave the direction off - and only the
        third neither writes into somebody's home unbidden nor punishes them for a path they
        never chose. It must also not be fatal: the OTHER direction may be configured fine."""
        policy = AttachmentPolicy(str(tmp_path / "made" / "up"), from_default=True)
        assert policy.root is None
        assert policy.from_default is True
        assert policy.warning and "does not exist" in policy.warning
        assert not (tmp_path / "made").exists()

    def test_an_explicit_root_is_never_created(self, tmp_path):
        """The property that must survive: a path the operator asserted and got wrong fails
        loudly at startup. Creating it would turn a typo into a silently-empty directory and
        send them hunting for a missing file instead of a broken configuration."""
        with pytest.raises(PolicyError, match="does not exist"):
            AttachmentPolicy(str(tmp_path / "typo"))
        assert not (tmp_path / "typo").exists()

    def test_the_same_split_applies_to_downloads(self, tmp_path):
        defaulted = DownloadPolicy(str(tmp_path / "made"), from_default=True)
        assert defaulted.root is None and defaulted.warning
        assert not (tmp_path / "made").exists()
        with pytest.raises(PolicyError, match="does not exist"):
            DownloadPolicy(str(tmp_path / "typo"))

    def test_an_explicit_variable_still_wins_over_the_default(self, tmp_path, monkeypatch):
        chosen = tmp_path / "chosen"
        chosen.mkdir()
        monkeypatch.setenv("CSA_GGC_ATTACH_DIR", str(chosen))
        policy = from_env()
        assert policy.root == chosen.resolve()
        assert policy.from_default is False

    def test_a_default_that_is_a_file_is_also_just_off(self, tmp_path):
        """Not only "missing" - any reason the default is unusable lands in the same place. A
        path that exists but is not a directory must not raise either, for the same reason: it
        is still a path nobody chose."""
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file, not a directory")

        policy = AttachmentPolicy(str(blocker), from_default=True)

        assert policy.root is None
        assert "CSA_GGC_ATTACH_DIR" in policy.warning
        assert blocker.read_text() == "I am a file, not a directory", "must not be touched"


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


class TestADefaultDirectoryCreatedLaterIsNoticed:
    """`mkdir` is the documented remedy, so it has to actually work (#26).

    The policy is built once, at server startup, and handed to every tool. Caching "absent" for
    the process lifetime meant the server went on refusing after somebody did exactly what it
    told them to - which is worse than the original absence, because the remedy appears broken
    and the message keeps naming a directory that now exists. Nothing is created here; this only
    looks again.
    """

    def test_it_is_adopted_on_the_next_call_without_a_restart(self, tmp_path):
        outbox = tmp_path / "Documents" / "CSA-Outbox"
        policy = AttachmentPolicy(str(outbox), from_default=True)
        assert policy.root is None
        with pytest.raises(PolicyError, match="does not exist"):
            policy.resolve("x.pdf")

        outbox.mkdir(parents=True)                      # the user follows the instruction
        (outbox / "x.pdf").write_bytes(b"hi")

        assert policy.resolve("x.pdf") == (outbox / "x.pdf").resolve()
        assert policy.warning is None, "the stale warning must be cleared, not just bypassed"

    def test_looking_again_still_creates_nothing(self, tmp_path):
        policy = AttachmentPolicy(str(tmp_path / "never"), from_default=True)
        with pytest.raises(PolicyError):
            policy.resolve("x.pdf")
        assert not (tmp_path / "never").exists()

    def test_the_download_side_behaves_the_same(self, tmp_path):
        downloads = tmp_path / "Downloads"
        policy = DownloadPolicy(str(downloads), from_default=True)
        with pytest.raises(PolicyError, match="does not exist"):
            policy.resolve("a.txt")

        downloads.mkdir()

        assert policy.resolve("a.txt") == (downloads / "a.txt").resolve()


class TestALateDirectoryMustStillBeDisjoint:
    """The startup check ran when this root did not exist, so it proved nothing about it.

    Adopting without re-asking would reopen the hole `check_directories_disjoint` exists to
    close - just later, and more quietly, because no configuration changed to prompt a re-read.
    """

    def test_a_late_directory_nested_in_the_other_root_is_refused(self, tmp_path):
        documents = tmp_path / "Documents"
        documents.mkdir()
        download = DownloadPolicy(str(documents))               # explicit, exists
        attach = AttachmentPolicy(str(documents / "CSA-Outbox"), from_default=True)

        check_directories_disjoint(attach, download)            # passes: attach root is None
        assert attach.root is None

        (documents / "CSA-Outbox").mkdir()                      # appears, nested inside

        with pytest.raises(PolicyError, match="nested"):
            attach.resolve("x.pdf")
        assert attach.root is None, "a colliding directory must not be adopted"

    def test_a_late_directory_that_is_disjoint_is_adopted(self, tmp_path):
        """The other half - otherwise the test above would pass on a policy that adopts
        nothing at all, which is the shape of a check that cannot fail."""
        downloads = tmp_path / "Downloads"
        outbox = tmp_path / "Outbox"
        downloads.mkdir()
        download = DownloadPolicy(str(downloads))
        attach = AttachmentPolicy(str(outbox), from_default=True)
        check_directories_disjoint(attach, download)

        outbox.mkdir()
        (outbox / "x.pdf").write_bytes(b"hi")

        assert attach.resolve("x.pdf") == (outbox / "x.pdf").resolve()

    def test_without_a_sibling_it_still_adopts(self, tmp_path):
        """A policy built outside `create_server` has no counterpart wired, and must not be
        stuck off forever because of a check it has nothing to run against."""
        outbox = tmp_path / "Outbox"
        policy = AttachmentPolicy(str(outbox), from_default=True)
        assert policy._sibling is None
        outbox.mkdir()
        (outbox / "x.pdf").write_bytes(b"hi")
        assert policy.resolve("x.pdf") == (outbox / "x.pdf").resolve()

    def test_an_unstattable_sibling_means_do_not_adopt(self, tmp_path, monkeypatch):
        """Same rule the startup check holds: a pair this cannot prove disjoint is not one it
        may call safe. Failing open here would be the permissive reading of exactly the
        condition the check exists for."""
        downloads = tmp_path / "Downloads"
        outbox = tmp_path / "Outbox"
        downloads.mkdir()
        download = DownloadPolicy(str(downloads))
        # The outbox must NOT exist yet, or there is nothing pending and this exercises nothing.
        attach = AttachmentPolicy(str(outbox), from_default=True)
        check_directories_disjoint(attach, download)
        assert attach.root is None and attach._pending is not None

        outbox.mkdir()                      # appears, and would otherwise be adopted

        def boom(*a, **kw):
            raise OSError("vanished")
        monkeypatch.setattr("csa_google_gmail_calendar._attachments.os.path.samefile", boom)

        with pytest.raises(PolicyError):
            attach.resolve("x.pdf")
        assert attach.root is None
