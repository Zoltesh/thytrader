"""Tests for ThyTrader application configuration."""

from ipaddress import IPv4Address

from pydantic import ValidationError
import pytest

from thytrader.config import Environment, Settings

# This intentionally unsafe address exercises the network-exposure rejection path.
_UNSAFE_BIND_ADDRESS = IPv4Address("0.0.0.0")  # noqa: S104


def test_settings_default_to_safe_local_development() -> None:
    """Settings should default to a loopback-only development process."""
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert str(settings.api_host) == "127.0.0.1"
    assert settings.api_port == 8200
    assert settings.allow_remote_access is False
    assert settings.log_level == "INFO"


def test_settings_reject_non_loopback_binding_without_explicit_opt_in() -> None:
    """Settings should block accidental network exposure."""
    with pytest.raises(ValidationError, match="Protected remote access is not implemented"):
        Settings(api_host=_UNSAFE_BIND_ADDRESS, _env_file=None)


def test_settings_reject_non_loopback_binding_even_with_legacy_opt_in() -> None:
    """A boolean opt-in alone must not expose portfolio data without authentication."""
    with pytest.raises(ValidationError, match="Protected remote access is not implemented"):
        Settings(
            api_host=_UNSAFE_BIND_ADDRESS,
            allow_remote_access=True,
            _env_file=None,
        )


def test_settings_wrap_coinbase_credentials_as_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coinbase environment credentials should not appear in settings representations."""
    api_key_name = "organizations/example/apiKeys/example"
    private_key = "synthetic-private-key-value"
    monkeypatch.setenv("THYTRADER_COINBASE_API_KEY_NAME", api_key_name)
    monkeypatch.setenv("THYTRADER_COINBASE_API_PRIVATE_KEY", private_key)

    settings = Settings(_env_file=None)

    assert settings.coinbase_api_key_name is not None
    assert settings.coinbase_api_private_key is not None
    assert settings.coinbase_api_key_name.get_secret_value() == api_key_name
    assert settings.coinbase_api_private_key.get_secret_value() == private_key
    assert api_key_name not in repr(settings)
    assert private_key not in repr(settings)
