"""Command-line entry point for the dedicated market-data ingestion worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import TYPE_CHECKING

from coinbase.rest import RESTClient

from thytrader.config import Settings
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketData
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.service import MarketDataService
from thytrader.market_data_worker.feed import run_public_market_feed
from thytrader.market_data_worker.service import run_market_data_worker
from thytrader.observability.logging import configure_logging
from thytrader.persistence.database import create_engine, dispose, ping
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_market_data_worker import PostgresMarketDataWorkerStateStore
from thytrader.persistence.postgres_market_feed import PostgresMarketFeedStateStore

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path


async def run() -> None:
    """Run ingestion until an operating-system shutdown signal arrives."""
    settings = Settings()
    configure_logging(settings)
    if settings.database_url is None:
        message = "The market-data worker requires THYTRADER_DATABASE_URL for durable state."
        raise RuntimeError(message)

    engine = create_engine(settings.database_url)
    service, provider = _build_service(settings)
    state_store = PostgresMarketDataWorkerStateStore(engine)
    feed_store = PostgresMarketFeedStateStore(engine)
    audit_store = PostgresAuditEventStore(engine)
    live_feed = provider == "coinbase"
    try:
        try:
            await ping(engine)
            await state_store.get(
                provider,
                settings.market_data_worker_product_id,
                CandleInterval.ONE_HOUR,
            )
        except Exception as error:  # noqa: BLE001 - startup boundary redacts database details.
            _logger.warning("market_data_worker_database_unavailable type=%s", type(error).__name__)
            message = "The market-data worker could not connect to its durable state store."
            raise RuntimeError(message) from None

        stop_requested = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, stop_requested.set)
        loop.add_signal_handler(signal.SIGTERM, stop_requested.set)
        readiness_file = settings.market_data_worker_readiness_file

        _logger.info("market_data_worker_started")
        await asyncio.gather(
            run_market_data_worker(
                stop_requested,
                service=service,
                dataset_store=DatasetStore(settings.market_data_dataset_root),
                state_store=state_store,
                provider=provider,
                product_id=settings.market_data_worker_product_id,
                lookback_hours=settings.market_data_worker_lookback_hours,
                interval_seconds=settings.market_data_worker_interval_seconds,
                on_readiness_changed=lambda ready: _set_readiness(readiness_file, ready),
            ),
            run_public_market_feed(
                stop_requested,
                product_id=settings.market_data_worker_product_id,
                enabled=live_feed,
                feed_store=feed_store,
                audit_store=audit_store,
            ),
        )
        _logger.info("market_data_worker_stopped")
    finally:
        _set_readiness(settings.market_data_worker_readiness_file, False)
        await dispose(engine)


def _build_service(settings: Settings) -> tuple[MarketDataService, str]:
    """Build live Coinbase ingestion when configured, otherwise labeled demo ingestion."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return MarketDataService(DemoMarketData()), "demo"
    client = RESTClient(
        api_key=settings.coinbase_api_key_name.get_secret_value(),
        api_secret=settings.coinbase_api_private_key.get_secret_value(),
        timeout=10,
    )
    return MarketDataService(CoinbaseMarketData(client)), "coinbase"


def _set_readiness(readiness_file: Path | None, ready: bool) -> None:
    """Synchronize the dedicated supervisor-facing readiness marker."""
    if readiness_file is None:
        return
    if ready:
        readiness_file.parent.mkdir(parents=True, exist_ok=True)
        readiness_file.touch()
        return
    with contextlib.suppress(FileNotFoundError):
        readiness_file.unlink()


def main() -> None:
    """Start the dedicated market-data worker process."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
