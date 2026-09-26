"""Offline coverage for the OAuth flow: scope derivation, cache reading, re-consent detection,
and token-file hardening. Everything here monkeypatches the Google objects and uses a real
tmp_path token file — no browser, no network. Patterned on the sibling project's
tests/test_auth.py + tests/test_auth_lifecycle.py (csa-google-workspace), adapted for a
capability set instead of a read_only flag.
"""
import os

import pytest

from csa_google_gmail_calendar import auth, policy, scopes
from csa_google_gmail_calendar.exceptions import AuthError

B = "https://www.googleapis.com/auth/"


# --- scopes_for: what each capability requests -------------------------------------------

def test_read_only_capabilities_request_read_only_scopes():
    got = set(auth.scopes_for(frozenset({policy.MAIL_READ, policy.CALENDAR_READ})))
    assert f"{B}gmail.readonly" in got and f"{B}calendar.events.readonly" in got
    assert f"{B}gmail.modify" not in got, "spec §4: request what the capabilities need, no more"


def test_enabling_send_adds_exactly_the_send_scope():
    base = set(auth.scopes_for(frozenset({policy.MAIL_READ})))
    with_send = set(auth.scopes_for(frozenset({policy.MAIL_READ, policy.MAIL_SEND})))
    assert with_send - base == {f"{B}gmail.send"}


def test_mail_write_needs_modify_and_says_so_only_when_enabled():
    assert f"{B}gmail.modify" in auth.scopes_for(frozenset({policy.MAIL_WRITE}))
    assert f"{B}gmail.modify" not in auth.scopes_for(frozenset({policy.MAIL_READ}))


def test_full_mailbox_scope_is_never_requested_without_mail_delete():
    every_but_delete = frozenset(policy.ALL_CAPABILITIES) - {policy.MAIL_DELETE}
    assert "https://mail.google.com/" not in auth.scopes_for(every_but_delete)


def test_the_consent_screen_for_mail_read_and_calendar_read_offers_no_write_access():
    """Sanity check on what a real consent screen shows: MAIL_READ + CALENDAR_READ alone must
    produce exactly the read-only scope strings below, and nothing that could modify anything."""
    got = set(auth.scopes_for(frozenset({policy.MAIL_READ, policy.CALENDAR_READ})))
    assert got == {
        f"{B}gmail.readonly",
        f"{B}calendar.events.readonly",
        f"{B}calendar.calendarlist.readonly",
        f"{B}calendar.freebusy",
    }


def test_default_enabled_capabilities_never_request_the_bare_mailbox_scope():
    """DEFAULT_ENABLED excludes MAIL_DELETE and CALENDAR_DELETE; the consent screen a fresh
    install shows must therefore never carry the one scope that grants permanent destroy."""
    assert "https://mail.google.com/" not in auth.scopes_for(policy.DEFAULT_ENABLED)


def test_every_capability_scope_is_ranked_in_the_privilege_lattice():
    """Completeness, fail-closed: an unranked scope in _CAPABILITY_SCOPES could never be
    checked against scopes.narrowest()'s answer for the methods it gates (carry-forward-
    task-9.md), so a typo or a scope scopes.py has not been taught about must fail loudly
    here rather than silently requesting something this codebase cannot reason about."""
    for capability, capability_scopes in auth._CAPABILITY_SCOPES.items():
        for scope in capability_scopes:
            assert scope in scopes.RANK, f"{capability!r} requests unranked scope {scope!r}"
            assert scope not in scopes.UNRANKED


def test_unknown_capability_is_silently_ignored_by_scopes_for():
    # scopes_for uses .get(capability, ()); an unknown key contributes nothing rather than
    # raising, which matches _GATES-style fail-closed behaviour living in policy.py instead.
    assert auth.scopes_for(frozenset({"not-a-real-capability"})) == []


# --- needs_reconsent -----------------------------------------------------------------------

def test_a_granted_write_scope_satisfies_a_required_read_scope():
    assert not auth.needs_reconsent([f"{B}gmail.modify"], [f"{B}gmail.readonly"])


def test_a_missing_scope_needs_reconsent():
    assert auth.needs_reconsent([f"{B}gmail.readonly"], [f"{B}gmail.send"])


def test_needs_reconsent_false_when_all_present():
    required = auth.scopes_for(policy.DEFAULT_ENABLED)
    assert auth.needs_reconsent(granted=required, required=required) is False


# --- _read_cached / load_cached_credentials: error shape --------------------------------

def test_scope_short_token_raises_its_own_type_not_a_generic_auth_error(tmp_path):
    """REVIEW FOCUS #4. 'Your login is fine but one scope short' is a different instruction
    from 'you have not logged in', and the remedy reads differently even though the command
    is the same."""
    import json
    tok = tmp_path / "token.json"
    tok.write_text(json.dumps({"token": "x", "refresh_token": "y", "client_id": "c",
                               "client_secret": "s", "scopes": [f"{B}gmail.readonly"]}))
    with pytest.raises(auth.ScopesMissingError) as ei:
        auth.load_cached_credentials(str(tok), [f"{B}gmail.readonly", f"{B}gmail.send"])
    assert ei.value.scopes == [f"{B}gmail.send"]
    assert "re-consent" in str(ei.value)


def test_an_absent_token_is_a_plain_auth_error_not_a_scope_error(tmp_path):
    with pytest.raises(AuthError) as ei:
        auth.load_cached_credentials(str(tmp_path / "nope.json"), [f"{B}gmail.readonly"])
    assert not isinstance(ei.value, auth.ScopesMissingError)


def test_a_corrupt_token_file_does_not_echo_its_contents(tmp_path):
    """Never interpolate the cause — it may carry token material."""
    tok = tmp_path / "token.json"
    tok.write_text("{not json")
    with pytest.raises(AuthError) as ei:
        auth.load_cached_credentials(str(tok), [f"{B}gmail.readonly"])
    assert "not json" not in str(ei.value)
    assert ei.value.__cause__ is not None          # preserved via `from e`, just not interpolated


# --- lifecycle: cache / refresh / consent, with fakes standing in for google-auth --------

class FakeCreds:
    def __init__(self, *, valid=True, expired=False, refresh_token="rt", scopes=None):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.scopes = auth.scopes_for(policy.DEFAULT_ENABLED) if scopes is None else scopes
        self.refreshed = False

    def refresh(self, request):
        self.refreshed = True
        self.valid = True

    def to_json(self):
        return '{"token": "fake"}'


class RaisingCreds:
    """Refresh that fails the way a revoked refresh token fails: GoogleAuthError from Google,
    not a local bug."""
    valid = False
    expired = True
    refresh_token = "rt"

    def __init__(self, scopes=None):
        self.scopes = auth.scopes_for(policy.DEFAULT_ENABLED) if scopes is None else scopes

    def refresh(self, request):
        from google.auth.exceptions import RefreshError
        raise RefreshError("invalid_grant: Token has been expired or revoked.")


class FakeFlow:
    def __init__(self, creds):
        self.creds = creds

    def run_local_server(self, port=0):
        return self.creds


def _no_flow(*args, **kwargs):
    raise AssertionError("the interactive OAuth flow should not run on this path")


def _patch_from_file(monkeypatch, creds_or_exc):
    def loader(path, *a, **k):
        if isinstance(creds_or_exc, Exception):
            raise creds_or_exc
        return creds_or_exc
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", loader)


def _patch_flow(monkeypatch, creds):
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_config",
                        lambda config, scopes: FakeFlow(creds))


def _client_secrets(tmp_path):
    p = tmp_path / "client_secret.json"
    p.write_text('{"installed":{"client_id":"cid","client_secret":"cs",'
                 '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
                 '"token_uri":"https://oauth2.googleapis.com/token"}}', encoding="utf-8")
    return str(p)


def _required(tmp_path=None):
    return auth.scopes_for(policy.DEFAULT_ENABLED)


def test_valid_cached_token_is_returned_without_flow(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    cached = FakeCreds(valid=True)
    _patch_from_file(monkeypatch, cached)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_config", _no_flow)

    assert auth.load_credentials("client.json", str(token), _required()) is cached


def test_missing_token_triggers_oauth_flow(tmp_path, monkeypatch):
    token = tmp_path / "sub" / "token.json"   # neither file nor dir exists yet
    fresh = FakeCreds(valid=True)
    _patch_flow(monkeypatch, fresh)

    result = auth.load_credentials(_client_secrets(tmp_path), str(token), _required())
    assert result is fresh and token.exists()


def test_cached_expired_token_is_refreshed_and_persisted(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    creds = FakeCreds(valid=False, expired=True, refresh_token="rt")
    _patch_from_file(monkeypatch, creds)
    monkeypatch.setattr(auth, "Request", lambda: None)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_config", _no_flow)

    result = auth.load_cached_credentials(str(token), _required())
    assert result is creds and creds.refreshed is True
    assert token.read_text() == '{"token": "fake"}'          # refreshed token persisted


def test_cached_unrefreshable_token_raises(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    _patch_from_file(monkeypatch, FakeCreds(valid=False, expired=False, refresh_token=None))
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_config", _no_flow)

    with pytest.raises(AuthError):
        auth.load_cached_credentials(str(token), _required())


def test_revoked_refresh_token_surfaces_as_auth_error_not_a_raw_google_exception(tmp_path, monkeypatch):
    """A refresh token can be revoked server-side (user revoked app access, admin action,
    password change). `_refresh` must translate the resulting GoogleAuthError into this
    package's own AuthError rather than letting google-auth's exception cross the boundary,
    so a caller can catch one exception type regardless of why auth failed."""
    token = tmp_path / "token.json"
    token.write_text("{}")
    _patch_from_file(monkeypatch, RaisingCreds())
    monkeypatch.setattr(auth, "Request", lambda: None)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_config", _no_flow)

    with pytest.raises(AuthError) as ei:
        auth.load_cached_credentials(str(token), _required())
    assert not isinstance(ei.value, auth.ScopesMissingError)
    assert ei.value.__cause__ is not None
    from google.auth.exceptions import GoogleAuthError
    assert not isinstance(ei.value, GoogleAuthError)


def test_insufficient_scopes_forces_reconsent_on_the_interactive_path(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    stale = FakeCreds(valid=True, scopes=[s for s in _required() if "send" not in s])
    _patch_from_file(monkeypatch, stale)
    fresh = FakeCreds(valid=True)
    _patch_flow(monkeypatch, fresh)

    assert auth.load_credentials(_client_secrets(tmp_path), str(token), _required()) is fresh


# --- token-file hardening -------------------------------------------------------------------

def test_written_token_and_dir_are_owner_only(tmp_path, monkeypatch):
    token = tmp_path / "creds" / "token.json"   # dir is created by load_credentials
    _patch_flow(monkeypatch, FakeCreds(valid=True))

    auth.load_credentials(_client_secrets(tmp_path), str(token), _required())

    assert auth.file_is_owner_only(str(token)) is True
    assert auth.file_is_owner_only(str(token.parent)) is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows path covered separately")
def test_a_preexisting_looser_mode_is_hardened_before_content_is_written(tmp_path):
    """#17: O_TRUNC keeps a pre-existing file's mode, so the fix has to happen at open time,
    not as a follow-up chmod after the fact — a file created 0644 and chmod'd to 0600
    afterwards is world-readable for the moment in between. `_write_token` calls `_harden`
    on the fd immediately after `os.open`, before `f.write(...)` runs, so no readable window
    exists on POSIX regardless of the file's prior mode."""
    token = tmp_path / "token.json"
    token.write_text("old-content-that-must-not-survive")
    token.chmod(0o644)                                     # pre-existing, world-readable

    auth._write_token(str(token), FakeCreds(valid=True))

    assert auth.file_is_owner_only(str(token)) is True
    assert token.read_text() == '{"token": "fake"}'


def test_symlinked_token_path_is_refused(tmp_path):
    link = tmp_path / "token.json"
    try:
        link.symlink_to(tmp_path / "nonexistent-target.json")  # dangling -> reaches the write
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create a symlink without elevation")
    with pytest.raises(OSError):
        auth._write_token(str(link), FakeCreds(valid=True))


def test_preexisting_token_dir_is_not_mutated_as_a_side_effect(tmp_path):
    d = tmp_path / "caller-dir"
    d.mkdir()
    if os.name != "nt":
        d.chmod(0o755)
    before = auth.file_is_owner_only(str(d))
    token = d / "token.json"

    auth._write_token(str(token), FakeCreds(valid=True))

    assert auth.file_is_owner_only(str(d)) == before        # unchanged, not hardened for them
    assert auth.file_is_owner_only(str(token)) is True       # the file we created IS hardened


# --- token_path_default ----------------------------------------------------------------------

def test_token_path_default_expands_home_and_has_no_env_override():
    path = auth.token_path_default()
    assert path == os.path.expanduser("~/.csa_google_gmail_calendar/token.json")
    assert not path.startswith("~")


def test_token_path_default_honours_its_env_var(monkeypatch, tmp_path):
    override = str(tmp_path / "elsewhere" / "token.json")
    monkeypatch.setenv("CSA_GGC_TOKEN_PATH", override)
    assert auth.token_path_default() == override


# --- read_client_secrets ---------------------------------------------------------------------

def test_missing_client_secrets_names_the_path_and_the_env_var(tmp_path):
    with pytest.raises(AuthError, match="CSA_GGC_CLIENT_SECRETS"):
        auth.read_client_secrets(str(tmp_path / "nope.json"))


def test_client_secrets_that_is_not_json_names_the_path(tmp_path):
    bad = tmp_path / "client_secret.json"
    bad.write_text("{not json")
    with pytest.raises(AuthError, match=str(bad)):
        auth.read_client_secrets(str(bad))


def test_client_secrets_missing_installed_or_web_key_is_refused(tmp_path):
    # The shape of a service-account key, not an installed-app OAuth client.
    bad = tmp_path / "client_secret.json"
    bad.write_text('{"type": "service_account", "project_id": "x"}')
    with pytest.raises(AuthError, match="installed"):
        auth.read_client_secrets(str(bad))
