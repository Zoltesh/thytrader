"""Command-line entry point for the continuously running worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import TYPE_CHECKING

from thytrader.credentials.worker_runtime import WorkerCredentialRuntime
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.observability.logging import configure_logging
from thytrader.persistence.audit_events import (
    AuditEventStore,
    DisabledAuditEventStore,
)
from thytrader.persistence.database import create_engine, dispose, ping
from thytrader.persistence.portfolio_history import (
    DisabledPortfolioHistoryStore,
    PortfolioHistoryStore,
)
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_history import PostgresPortfolioHistoryStore
from thytrader.persistence.postgres_worker_heartbeats import PostgresWorkerHeartbeatStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.runtime import RuntimeState
from thytrader.settings_yaml import SettingsStore
from thytrader.worker.service import run_worker

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.config import Settings
    from thytrader.portfolio.models import Portfolio

logger = logging.getLogger(__name__)


class _ReloadingPortfolioFetcher:
    """Swap the portfolio reader when shared Coinbase credentials change."""

    def __init__(self, initial: PortfolioService) -> None:
        """Bind the first portfolio service implementation."""
        self._service = initial

    def replace(self, settings: Settings) -> None:
        """Rebuild Coinbase or demo portfolio access from fresh settings."""
        self._service = _build_portfolio_service(settings)
        configure_logging(settings)

    async def get_portfolio(self) -> Portfolio:
        """Return the current portfolio snapshot."""
        return await self._service.get_portfolio()


async def run() -> None:
    """Run the worker until an operating-system shutdown signal arrives."""
    store = SettingsStore.open()
    settings = store.current()
    configure_logging(settings)
    runtime = RuntimeState(settings=settings, settings_store=store)
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop_requested.set)
    loop.add_signal_handler(signal.SIGTERM, stop_requested.set)

    portfolio_fetcher = _ReloadingPortfolioFetcher(_build_portfolio_service(settings))
    credential_runtime = WorkerCredentialRuntime(
        store,
        on_coinbase_reload=portfolio_fetcher.replace,
    )
    history_store, audit_store, engine, heartbeats = await _build_stores(settings)

    readiness_file = settings.worker_readiness_file
    try:
        logger.info("worker_started")
        await asyncio.gather(
            run_worker(
                runtime,
                stop_requested,
                portfolio_service=portfolio_fetcher,
                history_store=history_store,
                audit_store=audit_store,
                on_started=lambda: _mark_ready(readiness_file),
                heartbeat_store=heartbeats,
            ),
            credential_runtime.run_until_stopped(stop_requested),
        )
        logger.info("worker_stopped")
    finally:
        _clear_ready(readiness_file)
        if engine is not None:
            await dispose(engine)


def _mark_ready(readiness_file: Path | None) -> None:
    """Create the optional supervisor-facing readiness marker."""
    if readiness_file is None:
        return
    readiness_file.parent.mkdir(parents=True, exist_ok=True)
    readiness_file.touch()


def _clear_ready(readiness_file: Path | None) -> None:
    """Remove the optional readiness marker during shutdown."""
    if readiness_file is None:
        return
    with contextlib.suppress(FileNotFoundError):
        readiness_file.unlink()


def _build_portfolio_service(settings: Settings) -> PortfolioService:
    """Build a live Coinbase service when credentials exist, otherwise demo."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return PortfolioService(DemoExchangeAccount(), demo=True)

    from coinbase.rest import RESTClient  # noqa: PLC0415

    client = RESTClient(
        api_key=settings.coinbase_api_key_name.get_secret_value(),
        api_secret=settings.coinbase_api_private_key.get_secret_value(),
        timeout=10,
    )
    return PortfolioService(CoinbaseAccount(client))


async def _build_stores(
    settings: Settings,
) -> tuple[
    PortfolioHistoryStore,
    AuditEventStore,
    AsyncEngine | None,
    PostgresWorkerHeartbeatStore | None,
]:
    """Create PostgreSQL stores when configured, or disabled."""
    if settings.database_url is None:
        return DisabledPortfolioHistoryStore(), DisabledAuditEventStore(), None, None

    engine = create_engine(settings.database_url)
    try:
        await ping(engine)
    except Exception:
        await dispose(engine)
        logger.exception("Worker database connectivity check failed")
        raise
    return (
        PostgresPortfolioHistoryStore(engine),
        PostgresAuditEventStore(engine),
        engine,
        PostgresWorkerHeartbeatStore(engine),
    )


def main() -> None:
    """Start the worker process."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
