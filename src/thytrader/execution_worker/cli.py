"""Command-line entry point for the strategy execution worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import TYPE_CHECKING

from coinbase.rest import RESTClient

from thytrader.config import Settings
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketData
from thytrader.exchanges.rest_transport import RestClientTransport
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker.service import run_execution_worker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.observability.logging import configure_logging
from thytrader.persistence.database import create_engine, dispose, ping
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.persistence.postgres_worker_heartbeats import PostgresWorkerHeartbeatStore

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.execution.broker import Broker
    from thytrader.execution_worker.service import QuoteBalanceReader


async def run() -> None:
    """Run execution until an operating-system shutdown signal arrives."""
    settings = Settings()
    configure_logging(settings)
    if settings.database_url is None:
        message = "The execution worker requires THYTRADER_DATABASE_URL for durable state."
        raise RuntimeError(message)

    engine = create_engine(settings.database_url)
    store = PostgresExecutionStore(engine)
    publication_store = PostgresStrategyPublicationStore(engine)
    heartbeats = PostgresWorkerHeartbeatStore(engine)
    market_data, live_broker, quote_reader = _build_live_dependencies(settings)
    try:
        try:
            await ping(engine)
        except Exception as error:  # noqa: BLE001 - startup boundary redacts database details.
            _logger.warning("execution_worker_database_unavailable type=%s", type(error).__name__)
            message = "The execution worker could not connect to its durable state store."
            raise RuntimeError(message) from None

        stop_requested = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, stop_requested.set)
        loop.add_signal_handler(signal.SIGTERM, stop_requested.set)
        _logger.info("execution_worker_started")
        await run_execution_worker(
            stop_requested,
            store=store,
            publication_store=publication_store,
            market_data=market_data,
            paper_broker=PaperBroker(),
            live_broker=live_broker,
            quote_reader=quote_reader,
            interval_seconds=settings.execution_worker_interval_seconds,
            on_readiness_changed=lambda ready: _set_readiness(
                settings.execution_worker_readiness_file, ready
            ),
            heartbeat_store=heartbeats,
        )
        _logger.info("execution_worker_stopped")
    finally:
        _set_readiness(settings.execution_worker_readiness_file, False)
        await dispose(engine)


def _build_live_dependencies(
    settings: Settings,
) -> tuple[MarketDataService, Broker | None, QuoteBalanceReader | None]:
    """Use Coinbase REST when credentials exist, otherwise demo candles and no live broker."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return MarketDataService(DemoMarketData()), None, None
    client = RESTClient(
        api_key=settings.coinbase_api_key_name.get_secret_value(),
        api_secret=settings.coinbase_api_private_key.get_secret_value(),
        timeout=10,
    )
    transport = RestClientTransport(client)
    return (
        MarketDataService(CoinbaseMarketData(client)),
        CoinbaseRestBroker(transport),
        CoinbaseAccount(client),
    )


def _set_readiness(readiness_file: Path | None, ready: bool) -> None:
    """Synchronize the supervisor-facing readiness marker."""
    if readiness_file is None:
        return
    if ready:
        readiness_file.parent.mkdir(parents=True, exist_ok=True)
        readiness_file.touch()
        return
    with contextlib.suppress(FileNotFoundError):
        readiness_file.unlink()


def main() -> None:
    """Start the dedicated execution worker process."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
