"""Durable Coinbase credential reload shared across API and worker processes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable  # noqa: TC003 - callback typing at runtime boundary.
import hashlib
import logging
from pathlib import Path  # noqa: TC003 - shared credential paths are runtime values.
from threading import Lock

from thytrader.config import Settings  # noqa: TC001 - reload store builds Settings at runtime.

LOGGER = logging.getLogger(__name__)


class CoinbaseCredentialReloadStore:
    """Reload Coinbase secrets from a shared dotenv file when content changes."""

    def __init__(
        self,
        *,
        env_path: Path,
        base_settings: Settings,
        on_reload: Callable[[Settings], None] | None = None,
    ) -> None:
        """Bind one shared dotenv path and optional reload callback."""
        self.env_path = env_path
        self._base_settings = base_settings
        self._on_reload = on_reload
        self._lock = Lock()
        self._content_hash: str | None = None
        self._current = base_settings
        self._refresh_from_disk()

    @property
    def current(self) -> Settings:
        """Return settings, reloading when the dotenv content hash changes."""
        with self._lock:
            self._refresh_from_disk()
            return self._current

    def adopt(self, settings: Settings) -> None:
        """Replace cached settings after an in-process mutation."""
        with self._lock:
            self._current = settings
            self._content_hash = self._hash_path()

    def sync_base_settings(self, settings: Settings) -> None:
        """Refresh YAML-backed fields before the next disk read."""
        with self._lock:
            self._base_settings = settings
            key_name, private_key = self._read_coinbase_from_env()
            from thytrader.credentials.service import settings_with_coinbase  # noqa: PLC0415

            self._current = settings_with_coinbase(
                self._base_settings,
                key_name=key_name,
                private_key=private_key,
            )

    def _refresh_from_disk(self) -> None:
        """Reload from disk when the content hash changes."""
        new_hash = self._hash_path()
        if new_hash == self._content_hash:
            return
        self._content_hash = new_hash
        key_name, private_key = self._read_coinbase_from_env()
        from thytrader.credentials.service import settings_with_coinbase  # noqa: PLC0415

        self._current = settings_with_coinbase(
            self._base_settings,
            key_name=key_name,
            private_key=private_key,
        )
        if self._on_reload is not None:
            self._on_reload(self._current)

    def _hash_path(self) -> str | None:
        """Return a SHA-256 digest of the dotenv bytes, or None when absent."""
        if not self.env_path.is_file():
            return None
        try:
            payload = self.env_path.read_bytes()
        except OSError:
            return None
        return hashlib.sha256(payload).hexdigest()

    def _read_coinbase_from_env(self) -> tuple[str | None, str | None]:
        """Parse Coinbase keys from the dotenv file without logging values."""
        if not self.env_path.is_file():
            return None, None
        key_name: str | None = None
        private_key: str | None = None
        try:
            for line in self.env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("THYTRADER_COINBASE_API_KEY_NAME="):
                    key_name = _parse_env_value(stripped.split("=", 1)[1])
                elif stripped.startswith("THYTRADER_COINBASE_API_PRIVATE_KEY="):
                    private_key = _parse_env_value(stripped.split("=", 1)[1])
        except OSError:
            LOGGER.exception("Could not read shared credentials env file")
            return None, None
        if key_name is not None and not key_name.strip():
            key_name = None
        if private_key is not None and not private_key.strip():
            private_key = None
        return key_name, private_key


def _parse_env_value(raw: str) -> str:
    """Parse one dotenv assignment value."""
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return value


async def reload_credentials_periodically(
    store: CoinbaseCredentialReloadStore,
    *,
    stop_requested: Callable[[], bool],
    interval_seconds: float = 5.0,
) -> None:
    """Poll the shared dotenv file until shutdown is requested."""
    while not stop_requested():
        _ = store.current
        await asyncio.sleep(interval_seconds)
