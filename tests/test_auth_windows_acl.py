"""The Windows ACL machinery in `auth.py` (`_current_windows_principal`, `_icacls`,
`_read_acl`, `_is_own_logon_session`, `_strays`, `_unexpected_principals`, and the Windows
branches of `file_is_owner_only`/`_harden`), exercised on whatever platform this suite runs on.

Most of this module is pure string/list logic over an `icacls` invocation, not OS-specific
itself - only the actual subprocess call is Windows-only. So `_icacls` is monkeypatched to
return canned output (never the real command, which does not exist on POSIX), and `auth._WINDOWS`
is monkeypatched to `True` where a function branches on it, the same way a test forces any other
module constant to exercise a path this interpreter's real platform would not otherwise take.
This tests the LOGIC assuming Windows, with the OS boundary (the actual `icacls.exe` call)
stubbed - not a claim that this suite runs on Windows.

Real Windows execution is out of scope here, same as `_harden`'s own docstring cites for the
directory-execute-bit distinction: "a Windows-only test run cannot reach this line at all" is
the converse of what this file does, which is a POSIX-only run reaching Windows-shaped LOGIC via
a forced `_WINDOWS` flag and a stubbed `_icacls`.
"""
from __future__ import annotations

import subprocess

from csa_google_gmail_calendar import auth


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["icacls"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# --- _current_windows_principal --------------------------------------------------------------

def test_current_windows_principal_formats_domain_and_username(monkeypatch):
    monkeypatch.setenv("USERDOMAIN", "WINTOP")
    monkeypatch.setenv("USERNAME", "alice")
    assert auth._current_windows_principal() == "WINTOP\\alice"


def test_current_windows_principal_with_no_domain_is_just_the_username(monkeypatch):
    monkeypatch.delenv("USERDOMAIN", raising=False)
    monkeypatch.setenv("USERNAME", "alice")
    assert auth._current_windows_principal() == "alice"


# --- _icacls: the one real OS boundary --------------------------------------------------------

def test_icacls_invokes_the_real_command_with_a_fixed_argv(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _completed(returncode=0, stdout="ok")

    monkeypatch.setattr(auth.subprocess, "run", fake_run)
    result = auth._icacls("C:\\Users\\alice\\token.json", "/inheritance:r")
    assert seen["argv"] == ["icacls", "C:\\Users\\alice\\token.json", "/inheritance:r"]
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["text"] is True
    assert seen["kwargs"]["check"] is False
    assert result.stdout == "ok"


# --- _read_acl: parsing icacls output ----------------------------------------------------------

def test_read_acl_returns_none_when_icacls_itself_failed(monkeypatch):
    monkeypatch.setattr(auth, "_icacls", lambda *a: _completed(returncode=1, stderr="denied"))
    assert auth._read_acl("C:\\path\\token.json") is None


def test_read_acl_parses_explicit_non_inherited_principals(monkeypatch):
    path = "C:\\Users\\alice\\token.json"
    stdout = (
        f"{path} WINTOP\\alice:(F)\n"
        f"                     BUILTIN\\Administrators:(F)\n"
        f"\n"
        f"Successfully processed 1 files; Failed processing 0 files.\n"
    )
    monkeypatch.setattr(auth, "_icacls", lambda *a: _completed(returncode=0, stdout=stdout))
    principals, inherited = auth._read_acl(path)
    assert principals == ["WINTOP\\alice", "BUILTIN\\Administrators"]
    assert inherited is False


def test_read_acl_detects_an_inherited_ace_and_excludes_it_from_principals(monkeypatch):
    path = "C:\\Users\\alice\\token.json"
    stdout = (
        f"{path} WINTOP\\alice:(F)\n"
        f"                     NT AUTHORITY\\SYSTEM:(I)(F)\n"
        f"\n"
        f"Successfully processed 1 files; Failed processing 0 files.\n"
    )
    monkeypatch.setattr(auth, "_icacls", lambda *a: _completed(returncode=0, stdout=stdout))
    principals, inherited = auth._read_acl(path)
    assert principals == ["WINTOP\\alice"]
    assert inherited is True


# --- _is_own_logon_session --------------------------------------------------------------------

def test_is_own_logon_session_recognises_the_sid_case_insensitively():
    assert auth._is_own_logon_session("NT AUTHORITY\\LogonSessionId_0_17444932") is True
    assert auth._is_own_logon_session("nt authority\\logonsessionid_0_1") is True


def test_is_own_logon_session_false_for_an_ordinary_principal():
    assert auth._is_own_logon_session("WINTOP\\someone-else") is False


# --- _strays -------------------------------------------------------------------------------

def test_strays_excludes_owner_root_equivalents_and_own_logon_session(monkeypatch):
    monkeypatch.setattr(auth, "_current_windows_principal", lambda: "WINTOP\\alice")
    assert auth._strays(["WINTOP\\alice"]) == []
    assert auth._strays(["NT AUTHORITY\\SYSTEM", "BUILTIN\\Administrators"]) == []
    assert auth._strays(["NT AUTHORITY\\LogonSessionId_0_17444932"]) == []


def test_strays_reports_an_unrecognised_principal(monkeypatch):
    monkeypatch.setattr(auth, "_current_windows_principal", lambda: "WINTOP\\alice")
    assert auth._strays(["Everyone"]) == ["Everyone"]
    assert auth._strays(["WINTOP\\someone-else"]) == ["WINTOP\\someone-else"]


# --- _unexpected_principals ------------------------------------------------------------------

def test_unexpected_principals_is_empty_when_the_acl_cannot_be_read(monkeypatch):
    monkeypatch.setattr(auth, "_read_acl", lambda path: None)
    assert auth._unexpected_principals("C:\\x\\token.json") == []


def test_unexpected_principals_forwards_the_read_principals_through_strays(monkeypatch):
    monkeypatch.setattr(auth, "_read_acl", lambda path: (["Everyone"], False))
    monkeypatch.setattr(auth, "_current_windows_principal", lambda: "WINTOP\\alice")
    assert auth._unexpected_principals("C:\\x\\token.json") == ["Everyone"]


# --- file_is_owner_only's Windows branch (forced) ----------------------------------------------

def test_file_is_owner_only_windows_branch_unknown_when_acl_unreadable(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth, "_read_acl", lambda path: None)
    assert auth.file_is_owner_only(str(token)) is None


def test_file_is_owner_only_windows_branch_false_when_inherited(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth, "_read_acl", lambda path: (["WINTOP\\alice"], True))
    assert auth.file_is_owner_only(str(token)) is False


def test_file_is_owner_only_windows_branch_unknown_when_no_principals_read(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth, "_read_acl", lambda path: ([], False))
    assert auth.file_is_owner_only(str(token)) is None


def test_file_is_owner_only_windows_branch_true_with_no_strays(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth, "_read_acl", lambda path: (["WINTOP\\alice"], False))
    monkeypatch.setattr(auth, "_current_windows_principal", lambda: "WINTOP\\alice")
    assert auth.file_is_owner_only(str(token)) is True


def test_file_is_owner_only_windows_branch_false_with_a_stray(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth, "_read_acl", lambda path: (["Everyone"], False))
    monkeypatch.setattr(auth, "_current_windows_principal", lambda: "WINTOP\\alice")
    assert auth.file_is_owner_only(str(token)) is False


# --- _harden's Windows branch (forced) ---------------------------------------------------------

def test_harden_windows_branch_removes_a_stray_left_by_the_process_default_dacl(
        tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth, "_current_windows_principal", lambda: "WINTOP\\alice")
    calls = []

    def fake_icacls(*args):
        calls.append(args)
        return _completed(returncode=0)

    monkeypatch.setattr(auth, "_icacls", fake_icacls)
    monkeypatch.setattr(auth, "_unexpected_principals", lambda path: ["Everyone"])

    auth._harden(str(token))

    assert calls[0] == (str(token), "/inheritance:r", "/grant:r", "WINTOP\\alice:F")
    assert calls[1] == (str(token), "/remove:g", "Everyone")


def test_harden_windows_branch_warns_on_stderr_when_icacls_fails(tmp_path, monkeypatch, capsys):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth, "_current_windows_principal", lambda: "WINTOP\\alice")
    monkeypatch.setattr(auth, "_icacls",
                        lambda *a: _completed(returncode=1, stderr="access is denied"))

    auth._harden(str(token))

    err = capsys.readouterr().err
    assert "could not restrict" in err
    assert "access is denied" in err


def test_harden_windows_branch_warns_with_nothing_when_icacls_gives_no_stderr(
        tmp_path, monkeypatch, capsys):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth, "_current_windows_principal", lambda: "WINTOP\\alice")
    monkeypatch.setattr(auth, "_icacls", lambda *a: _completed(returncode=1, stderr=""))

    auth._harden(str(token))

    assert "nothing" in capsys.readouterr().err
