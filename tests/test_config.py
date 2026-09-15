"""Tests for ThyTrader application configuration."""

from ipaddress import IPv4Address

from pydantic import ValidationError
import pytest

from thytrader.agent_orchestration.models import YoloTier
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
    assert settings.database_url is None
    assert settings.market_data_worker_interval_seconds == 300
    assert settings.market_data_worker_lookback_hours == 168
    assert settings.market_data_worker_product_id == "BTC-USD"
    assert str(settings.market_data_dataset_root) == "data/market-data"


def test_settings_reject_invalid_market_data_worker_bounds() -> None:
    """Worker requests must remain within the supported bounded hourly contract."""
    with pytest.raises(ValidationError):
        Settings(market_data_worker_lookback_hours=2_161, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(market_data_worker_product_id="../BTC-USD", _env_file=None)


def test_settings_treats_an_empty_database_url_as_disabled() -> None:
    """An empty placeholder should preserve the stateless local workflow."""
    settings = Settings(database_url="   ", _env_file=None)

    assert settings.database_url is None


def test_settings_redacts_a_configured_database_url() -> None:
    """A database URL must not appear in Settings representations."""
    database_url = "postgresql+asyncpg://thytrader:synthetic-password@127.0.0.1:5439/thytrader"
    settings = Settings(database_url=database_url, _env_file=None)

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == database_url
    assert database_url not in repr(settings)


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


def test_settings_allow_compose_internal_listener() -> None:
    """The container network may use all interfaces when host publishing stays loopback-only."""
    settings = Settings(
        api_host=_UNSAFE_BIND_ADDRESS,
        containerized=True,
        _env_file=None,
    )

    assert settings.api_host == _UNSAFE_BIND_ADDRESS
    assert settings.containerized is True


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


def test_settings_default_yolo_off() -> None:
    """YOLO stays off so `--confirm` remains the mutation default."""
    settings = Settings(_env_file=None)

    assert settings.yolo_enabled is False
    assert settings.yolo_tiers == ()


def test_settings_reject_live_yolo_tier() -> None:
    """Live is never a YOLO-eligible tier."""
    with pytest.raises(ValidationError, match="never YOLO-eligible"):
        Settings(yolo_enabled=True, yolo_tiers="data,live", _env_file=None)


def test_settings_reject_enabled_yolo_without_tiers() -> None:
    """Enabling YOLO requires an explicit non-empty allowed-tier set."""
    with pytest.raises(ValidationError, match="YOLO_TIERS"):
        Settings(yolo_enabled=True, _env_file=None)


def test_settings_reject_tiers_without_enabled_flag() -> None:
    """Tiers without the enabled flag would hide a silent YOLO default."""
    with pytest.raises(ValidationError, match="YOLO_ENABLED"):
        Settings(yolo_tiers="data,research", _env_file=None)


def test_settings_accept_comma_separated_yolo_tiers() -> None:
    """Enabled YOLO parses unique non-live tiers from a comma-separated string."""
    settings = Settings(yolo_enabled=True, yolo_tiers="data, paper", _env_file=None)
    assert settings.yolo_enabled is True
    assert settings.yolo_tiers == (YoloTier.DATA, YoloTier.PAPER)


def test_settings_reject_duplicate_yolo_tiers() -> None:
    """Duplicate YOLO tiers are a configuration error, not a silent union."""
    with pytest.raises(ValidationError, match="duplicates"):
        Settings(yolo_enabled=True, yolo_tiers="data,data", _env_file=None)
