"""Shared Coinbase credential reload for long-running worker processes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from thytrader.credentials.reload import CoinbaseCredentialReloadStore, reload_credentials_periodically
from thytrader.credentials.service import shared_credentials_volume_active

if TYPE_CHECKING:
    from thytrader.config import Settings
    from thytrader.settings_yaml import SettingsStore


class WorkerCredentialRuntime:
    """Merge YAML settings reload with shared dotenv credential hash reload."""

    def __init__(
        self,
        settings_store: SettingsStore,
        *,
        on_coinbase_reload: Callable[[Settings], None] | None = None,
    ) -> None:
        """Attach optional rebuild callbacks when the shared credentials volume is active."""
        self.settings_store = settings_store
        settings = settings_store.current()
        env_path = settings.credentials_dir / ".env"
        self._reload_store: CoinbaseCredentialReloadStore | None = None
        if shared_credentials_volume_active(settings, env_path):
            self._reload_store = CoinbaseCredentialReloadStore(
                env_path=env_path,
                base_settings=settings,
                on_reload=on_coinbase_reload,
            )

    def current_settings(self) -> Settings:
        """Return YAML settings overlaid with the latest shared Coinbase secrets."""
        yaml_settings = self.settings_store.current()
        reload_store = self._reload_store
        if reload_store is None:
            return yaml_settings
        reload_store.sync_base_settings(yaml_settings)
        return reload_store.current

    async def run_until_stopped(self, stop_requested: asyncio.Event) -> None:
        """Poll the shared dotenv file until shutdown when reload is enabled."""
        reload_store = self._reload_store
        if reload_store is None:
            return
        await reload_credentials_periodically(reload_store, stop_requested=stop_requested.is_set)
