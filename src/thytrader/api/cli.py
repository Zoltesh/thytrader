"""Command-line entry point for the ThyTrader FastAPI process."""

import uvicorn

from thytrader.api.app import create_app
from thytrader.observability.logging import configure_logging
from thytrader.settings_yaml import SettingsStore


def main() -> None:
    """Start the ThyTrader API process with validated YAML-backed settings."""
    store = SettingsStore.open()
    settings = store.current()
    configure_logging(settings)
    uvicorn.run(
        create_app(settings, settings_store=store),
        host=str(settings.api_host),
        port=settings.api_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
