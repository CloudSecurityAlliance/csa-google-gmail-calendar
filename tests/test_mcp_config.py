"""`_config.py`: `policy_from_env`, `settings_from_env`, and `startup_warnings`."""
import pytest

from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.mcp import _config


def test_policy_from_env_unset_is_the_shipped_default():
    assert _config.policy_from_env({}).enabled == policy.DEFAULT_ENABLED


def test_policy_from_env_none_enables_nothing():
    assert _config.policy_from_env({"CSA_GGC_CAPABILITIES": "none"}).enabled == frozenset()


def test_policy_from_env_all_enables_everything():
    got = _config.policy_from_env({"CSA_GGC_CAPABILITIES": "all"})
    assert got.enabled == frozenset(policy.ALL_CAPABILITIES)


def test_policy_from_env_default_token_expands_and_composes_with_a_delta():
    got = _config.policy_from_env({"CSA_GGC_CAPABILITIES": "default,mail.delete"})
    assert got.enabled == policy.DEFAULT_ENABLED | {policy.MAIL_DELETE}


def test_policy_from_env_an_explicit_list_is_exactly_that_list():
    got = _config.policy_from_env({"CSA_GGC_CAPABILITIES": "mail.read,calendar.read"})
    assert got.enabled == frozenset({policy.MAIL_READ, policy.CALENDAR_READ})


def test_policy_from_env_refuses_an_unknown_capability_by_name():
    with pytest.raises(ValueError, match="CSA_GGC_CAPABILITIES"):
        _config.policy_from_env({"CSA_GGC_CAPABILITIES": "mail.reed"})


def test_settings_from_env_finds_no_client_secrets_when_unset_and_default_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(_config, "DEFAULT_CLIENT_SECRETS_PATH", str(tmp_path / "absent.json"))
    settings = _config.settings_from_env({}, policy.Policy())
    assert settings.client_secrets_path is None


def test_settings_from_env_prefers_the_explicit_client_secrets_variable(tmp_path):
    explicit = tmp_path / "client_secret.json"
    explicit.write_text("{}")
    settings = _config.settings_from_env({"CSA_GGC_CLIENT_SECRETS": str(explicit)}, policy.Policy())
    assert settings.client_secrets_path == str(explicit)


def test_settings_required_scopes_matches_auth_scopes_for():
    from csa_google_gmail_calendar import auth
    settings = _config.settings_from_env({}, policy.Policy(frozenset({policy.MAIL_READ})))
    assert settings.required_scopes == auth.scopes_for(frozenset({policy.MAIL_READ}))


def test_startup_warnings_names_every_enabled_capability():
    settings = _config.settings_from_env({}, policy.Policy(frozenset({policy.MAIL_READ})))
    warnings = _config.startup_warnings(settings)
    assert any("mail.read" in w for w in warnings)


def test_startup_warnings_calls_out_irreversible_capabilities():
    settings = _config.settings_from_env({}, policy.Policy(frozenset({policy.MAIL_DELETE})))
    warnings = _config.startup_warnings(settings)
    assert any("cannot be undone" in w for w in warnings)


def test_startup_warnings_flags_a_missing_oauth_client(tmp_path, monkeypatch):
    monkeypatch.setattr(_config, "DEFAULT_CLIENT_SECRETS_PATH", str(tmp_path / "absent.json"))
    settings = _config.settings_from_env({}, policy.Policy())
    warnings = _config.startup_warnings(settings)
    assert any("CSA_GGC_CLIENT_SECRETS" in w for w in warnings)


def test_startup_says_nothing_about_client_secrets_when_one_is_configured(tmp_path):
    """The quiet branch, which CI measured as uncovered while the laptop did not.

    `startup_warnings` appends a "no OAuth client secrets configured" line only when none is
    set. Every existing test ran with it unset, so the *configured* path — the ordinary one for
    anyone who has actually installed this — was never asserted. A warning that fires when it
    should is half the property; not firing when it should not is the other half.
    """
    secrets = tmp_path / "client_secret.json"
    secrets.write_text('{"installed": {"client_id": "x", "project_id": "p"}}')
    settings = _config.Settings(token_path=str(tmp_path / "t.json"),
                        client_secrets_path=str(secrets), policy=policy.Policy())
    lines = _config.startup_warnings(settings)
    assert not any("no OAuth client secrets" in line for line in lines)
    assert any("capabilities enabled" in line for line in lines)
