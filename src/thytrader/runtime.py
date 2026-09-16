"""Process-local runtime state shared by API dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.config import Settings
    from thytrader.settings_yaml import SettingsStore


class RuntimeState:
    """Track settings (optionally YAML-backed) and process-readiness state.

    ``RuntimeState(settings=...)`` stays the test constructor. Production attaches a
    ``SettingsStore`` so ``settings`` re-reads YAML without replacing this object.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        settings_store: SettingsStore | None = None,
        ready: bool = False,
    ) -> None:
        """Bind one Settings snapshot and an optional hot-reload store."""
        self._settings = settings
        self.settings_store = settings_store
        self.ready = ready

    @property
    def settings(self) -> Settings:
        """Return the latest validated settings, re-reading YAML when a store is attached."""
        if self.settings_store is not None:
            return self.settings_store.current()
        return self._settings
