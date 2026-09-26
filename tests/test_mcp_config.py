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
    assert settings.client_secrets is None


def test_settings_from_env_prefers_the_explicit_client_secrets_variable(tmp_path):
    explicit = tmp_path / "client_secret.json"
    explicit.write_text("{}")
    settings = _config.settings_from_env({"CSA_GGC_CLIENT_SECRETS": str(explicit)}, policy.Policy())
    assert settings.client_secrets == str(explicit)


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
