"""`_login.py`'s own logic: which path it takes, what it prints, and what it warns about -
everything up to the one boundary this module exists to isolate (`auth.load_credentials`'s
real interactive consent, a browser and `InstalledAppFlow.run_local_server()`, which this file
never calls directly and this test suite does not simulate - that is task 14's gated live
suite, per the coordinator's ruling on the coverage gate). Every test here monkeypatches at
that one boundary and asserts on `_login.py`'s own wiring around it.
"""
import io
import json

import pytest

from csa_google_gmail_calendar import auth
from csa_google_gmail_calendar.exceptions import AuthError
from csa_google_gmail_calendar.mcp import _login
from csa_google_gmail_calendar.mcp._config import Settings
from csa_google_gmail_calendar.policy import Policy


def _settings(token_path: str) -> Settings:
    return Settings(token_path=token_path, client_secrets=None, policy=Policy())


def test_login_reports_no_client_secrets_and_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(_login, "DEFAULT_CLIENT_SECRETS_PATH", str(tmp_path / "absent.json"))
    out = io.StringIO()
    rc = _login.login(_settings(str(tmp_path / "token.json")), {}, out=out)
    assert rc == 2


def test_login_falls_back_to_the_default_client_secrets_path_when_it_exists(
        tmp_path, monkeypatch):
    default = tmp_path / "client_secret.json"
    default.write_text(json.dumps({"installed": {"client_id": "abc"}}))
    monkeypatch.setattr(_login, "DEFAULT_CLIENT_SECRETS_PATH", str(default))
    monkeypatch.setattr(auth, "load_cached_credentials",
                       lambda token_path, required: (_ for _ in ()).throw(AuthError("gone")))
    calls = []
    monkeypatch.setattr(
        auth, "load_credentials",
        lambda client_secrets, token_path, required, force=False:
            calls.append(client_secrets))
    rc = _login.login(_settings(str(tmp_path / "token.json")), {}, out=io.StringIO())
    assert rc == 0
    assert calls == [str(default)]


def test_login_uses_the_explicit_client_secrets_env_var(tmp_path, monkeypatch):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "abc"}}))
    monkeypatch.setattr(auth, "load_cached_credentials",
                       lambda token_path, required: (_ for _ in ()).throw(AuthError("no cred")))
    monkeypatch.setattr(auth, "load_credentials",
                       lambda client_secrets, token_path, required, force=False: None)
    out = io.StringIO()
    rc = _login.login(_settings(str(tmp_path / "token.json")),
                      {"CSA_GGC_CLIENT_SECRETS": str(secrets)}, out=out)
    assert rc == 0
    assert "Authorized" in out.getvalue()


def test_login_reports_already_authorized_when_a_usable_credential_is_cached(
        tmp_path, monkeypatch):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "same"}}))
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"client_id": "same"}))
    monkeypatch.setattr(auth, "load_cached_credentials",
                       lambda token_path, required: object())
    out = io.StringIO()
    rc = _login.login(_settings(str(token)), {"CSA_GGC_CLIENT_SECRETS": str(secrets)}, out=out)
    assert rc == 0
    assert "Already authorized" in out.getvalue()


def test_login_warns_when_the_cached_token_was_issued_by_a_different_client(
        tmp_path, monkeypatch, capsys):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "wanted-client"}}))
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"client_id": "cached-client"}))
    monkeypatch.setattr(auth, "load_cached_credentials",
                       lambda token_path, required: object())
    out = io.StringIO()
    rc = _login.login(_settings(str(token)), {"CSA_GGC_CLIENT_SECRETS": str(secrets)}, out=out)
    assert rc == 0
    err = capsys.readouterr().err
    assert "different OAuth client" in err
    assert "cached-client" in err
    assert "wanted-client" in err


def test_login_does_not_warn_when_client_ids_match(tmp_path, monkeypatch, capsys):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "same"}}))
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"client_id": "same"}))
    monkeypatch.setattr(auth, "load_cached_credentials",
                       lambda token_path, required: object())
    _login.login(_settings(str(token)), {"CSA_GGC_CLIENT_SECRETS": str(secrets)},
                out=io.StringIO())
    assert "different OAuth client" not in capsys.readouterr().err


def test_login_falls_through_to_consent_when_nothing_usable_is_cached(tmp_path, monkeypatch):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "abc"}}))
    calls = []
    monkeypatch.setattr(auth, "load_cached_credentials",
                       lambda token_path, required: (_ for _ in ()).throw(AuthError("gone")))
    monkeypatch.setattr(
        auth, "load_credentials",
        lambda client_secrets, token_path, required, force=False: calls.append(force))
    rc = _login.login(_settings(str(tmp_path / "token.json")),
                      {"CSA_GGC_CLIENT_SECRETS": str(secrets)}, out=io.StringIO())
    assert rc == 0
    assert calls == [False]


def test_login_force_skips_the_cached_credential_check_entirely(tmp_path, monkeypatch):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "abc"}}))

    def _boom(token_path, required):
        raise AssertionError("force=True must skip the cached-credential probe")

    calls = []
    monkeypatch.setattr(auth, "load_cached_credentials", _boom)
    monkeypatch.setattr(
        auth, "load_credentials",
        lambda client_secrets, token_path, required, force=False: calls.append(force))
    rc = _login.login(_settings(str(tmp_path / "token.json")),
                      {"CSA_GGC_CLIENT_SECRETS": str(secrets)}, force=True, out=io.StringIO())
    assert rc == 0
    assert calls == [True]


def test_client_id_of_returns_none_for_an_unreadable_file(tmp_path):
    assert _login._client_id_of(str(tmp_path / "nope.json")) is None


def test_client_id_of_reads_the_installed_client_id(tmp_path):
    p = tmp_path / "secrets.json"
    p.write_text(json.dumps({"installed": {"client_id": "abc123"}}))
    assert _login._client_id_of(str(p)) == "abc123"


def test_client_id_of_reads_the_web_client_id(tmp_path):
    p = tmp_path / "secrets.json"
    p.write_text(json.dumps({"web": {"client_id": "webclient"}}))
    assert _login._client_id_of(str(p)) == "webclient"


def test_token_client_id_returns_none_for_a_missing_file(tmp_path):
    assert _login._token_client_id(str(tmp_path / "nope.json")) is None


def test_token_client_id_returns_none_for_malformed_json(tmp_path):
    p = tmp_path / "token.json"
    p.write_text("not json")
    assert _login._token_client_id(str(p)) is None


def test_token_client_id_reads_the_stored_client_id(tmp_path):
    p = tmp_path / "token.json"
    p.write_text(json.dumps({"client_id": "xyz"}))
    assert _login._token_client_id(str(p)) == "xyz"


def test_branded_success_page_swaps_and_restores_the_redirect_app():
    """The one swap this context manager makes, verified directly - the real interactive
    consent flow that would exercise this in production is out of scope for this offline
    suite (see this module's own docstring)."""
    import google_auth_oauthlib.flow as flow_mod
    original = flow_mod._RedirectWSGIApp
    with _login._branded_success_page():
        assert flow_mod._RedirectWSGIApp is not original
        app = flow_mod._RedirectWSGIApp("ignored")
        assert app.last_request_uri is None
    assert flow_mod._RedirectWSGIApp is original


def test_branded_success_page_falls_back_quietly_when_the_import_is_unavailable(monkeypatch):
    """The defensive `except (ImportError, AttributeError)` branch - forced directly rather
    than by breaking a real installed import, which would risk destabilising every other test
    in this file that imports the same module."""
    import csa_google_gmail_calendar.mcp._login as login_mod

    real_import = __import__

    def _fail_flow_import(name, *args, **kwargs):
        if name == "google_auth_oauthlib.flow":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fail_flow_import)
    with login_mod._branded_success_page():
        pass  # must not raise


@pytest.mark.parametrize("client_secrets_value", [None, ""])
def test_login_falls_back_to_the_default_path_when_env_var_is_unset_or_empty(
        client_secrets_value, tmp_path, monkeypatch):
    monkeypatch.setattr(_login, "DEFAULT_CLIENT_SECRETS_PATH", str(tmp_path / "absent.json"))
    env = {} if client_secrets_value is None else {"CSA_GGC_CLIENT_SECRETS": client_secrets_value}
    rc = _login.login(_settings(str(tmp_path / "token.json")), env, out=io.StringIO())
    assert rc == 2
