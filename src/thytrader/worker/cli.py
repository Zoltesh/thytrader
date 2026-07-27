"""Command-line entry point for the continuously running worker."""

import asyncio
import logging
import signal

from thytrader.config import Settings
from thytrader.observability.logging import configure_logging
from thytrader.runtime import RuntimeState
from thytrader.worker.service import run_worker

logger = logging.getLogger(__name__)


async def run() -> None:
    """Run the worker until an operating-system shutdown signal arrives."""
    settings = Settings()
    configure_logging(settings)
    runtime = RuntimeState(settings=settings)
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop_requested.set)
    loop.add_signal_handler(signal.SIGTERM, stop_requested.set)

    logger.info("worker_started")
    await run_worker(runtime, stop_requested)
    logger.info("worker_stopped")


def main() -> None:
    """Start the worker process."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
