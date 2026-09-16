"""Tests for ThyTrader application configuration."""

from ipaddress import IPv4Address

from pydantic import ValidationError
import pytest

from thytrader.agent_orchestration.models import YoloTier
from thytrader.config import Environment, Settings
from thytrader.memory.models import NotifyProvider

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
    assert settings.notify_provider is NotifyProvider.NONE
    assert settings.notify_webhook_url is None


def test_settings_accept_live_yolo_tier() -> None:
    """Live is YOLO-eligible for `--confirm` skips when explicitly listed."""
    settings = Settings(yolo_enabled=True, yolo_tiers="data,live", _env_file=None)
    assert settings.yolo_enabled is True
    assert settings.yolo_tiers == (YoloTier.DATA, YoloTier.LIVE)


def test_settings_reject_unknown_yolo_tier() -> None:
    """Unknown YOLO tiers remain a configuration error."""
    with pytest.raises(ValidationError):
        Settings(yolo_enabled=True, yolo_tiers="data,memory", _env_file=None)


def test_settings_reject_enabled_yolo_without_tiers() -> None:
    """Enabling YOLO requires an explicit non-empty allowed-tier set."""
    with pytest.raises(ValidationError, match="YOLO_TIERS"):
        Settings(yolo_enabled=True, _env_file=None)


def test_settings_reject_tiers_without_enabled_flag() -> None:
    """Tiers without the enabled flag would hide a silent YOLO default."""
    with pytest.raises(ValidationError, match="YOLO_ENABLED"):
        Settings(yolo_tiers="data,research", _env_file=None)


def test_settings_accept_env_leftover_scalar_paper_yolo_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``THYTRADER_YOLO_TIERS=paper`` must not JSON-decode; paper YOLO is valid."""
    monkeypatch.setenv("THYTRADER_YOLO_ENABLED", "true")
    monkeypatch.setenv("THYTRADER_YOLO_TIERS", "paper")
    settings = Settings(_env_file=None)
    assert settings.yolo_enabled is True
    assert settings.yolo_tiers == (YoloTier.PAPER,)


def test_settings_accept_empty_env_yolo_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose leftover ``THYTRADER_YOLO_TIERS=`` is off, not a SettingsError."""
    monkeypatch.setenv("THYTRADER_YOLO_ENABLED", "false")
    monkeypatch.setenv("THYTRADER_YOLO_TIERS", "")
    settings = Settings(_env_file=None)
    assert settings.yolo_enabled is False
    assert settings.yolo_tiers == ()


def test_settings_accept_json_array_env_yolo_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON ``["paper"]`` leftover still parses after NoDecode."""
    monkeypatch.setenv("THYTRADER_YOLO_ENABLED", "true")
    monkeypatch.setenv("THYTRADER_YOLO_TIERS", '["paper"]')
    settings = Settings(_env_file=None)
    assert settings.yolo_tiers == (YoloTier.PAPER,)


def test_settings_accept_env_comma_list_including_paper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leftover comma lists such as ``data,paper`` are independent tiers, not JSON."""
    monkeypatch.setenv("THYTRADER_YOLO_ENABLED", "true")
    monkeypatch.setenv("THYTRADER_YOLO_TIERS", "data,paper")
    settings = Settings(_env_file=None)
    assert settings.yolo_tiers == (
        YoloTier.DATA,
        YoloTier.PAPER,
    )


def test_settings_reject_duplicate_yolo_tiers() -> None:
    """Duplicate YOLO tiers are a configuration error, not a silent union."""
    with pytest.raises(ValidationError, match="duplicates"):
        Settings(yolo_enabled=True, yolo_tiers="data,data", _env_file=None)


def test_settings_reject_webhook_notify_without_url() -> None:
    """Webhook notify requires an explicit URL."""
    with pytest.raises(ValidationError, match="NOTIFY_WEBHOOK_URL"):
        Settings(notify_provider="webhook", _env_file=None)


def test_settings_reject_webhook_url_without_webhook_provider() -> None:
    """A webhook URL is rejected unless the provider is webhook."""
    with pytest.raises(ValidationError, match="NOTIFY_PROVIDER"):
        Settings(notify_webhook_url="https://example.test/hook", _env_file=None)
