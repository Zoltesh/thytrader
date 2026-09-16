"""Apply Coinbase credentials onto frozen Settings without echoing secrets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from thytrader.credentials.envfile import env_file_writable
from thytrader.credentials.models import (
    WORKERS_RESTART_DETAIL,
    WORKERS_RESTART_LEGACY_DETAIL,
    CoinbaseCredentialsStatus,
)

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.config import Settings


def credentials_are_configured(settings: Settings) -> bool:
    """True when both Coinbase secrets are present on ``settings``."""
    return (
        settings.coinbase_api_key_name is not None and settings.coinbase_api_private_key is not None
    )


def settings_with_coinbase(
    settings: Settings,
    *,
    key_name: str | None,
    private_key: str | None,
) -> Settings:
    """Return a new frozen Settings with Coinbase secrets replaced.

    Does not mutate the Settings class. Pair validation still runs on copy.
    """
    name_secret = SecretStr(key_name) if key_name else None
    key_secret = SecretStr(private_key) if private_key else None
    return settings.model_copy(
        update={
            "coinbase_api_key_name": name_secret,
            "coinbase_api_private_key": key_secret,
        }
    )


def shared_credentials_volume_active(settings: Settings, env_path: Path) -> bool:
    """True when credentials are stored on the durable shared credentials directory."""
    try:
        return env_path.resolve().is_relative_to(settings.credentials_dir.resolve())
    except ValueError:
        return False


def coinbase_status(
    settings: Settings,
    *,
    env_path: Path,
    persisted: bool,
    api_hot_reloaded: bool,
    workers_require_restart: bool,
) -> CoinbaseCredentialsStatus:
    """Build a write-only status payload with no secret fields."""
    shared = shared_credentials_volume_active(settings, env_path)
    return CoinbaseCredentialsStatus(
        configured=credentials_are_configured(settings),
        persisted=persisted,
        env_file_writable=env_file_writable(env_path),
        api_hot_reloaded=api_hot_reloaded,
        workers_require_restart=workers_require_restart and not shared,
        workers_restart_detail=(
            WORKERS_RESTART_DETAIL if shared else WORKERS_RESTART_LEGACY_DETAIL
        ),
    )
