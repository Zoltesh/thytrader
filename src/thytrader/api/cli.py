"""Command-line entry point for the ThyTrader FastAPI process."""

import uvicorn

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.observability.logging import configure_logging


def main() -> None:
    """Start the ThyTrader API process with validated settings."""
    settings = Settings()
    configure_logging(settings)
    uvicorn.run(
        create_app(settings),
        host=str(settings.api_host),
        port=settings.api_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
