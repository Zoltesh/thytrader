"""FastAPI application construction."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import TYPE_CHECKING

from coinbase.rest import RESTClient
from fastapi import FastAPI

from thytrader import __version__
from thytrader.api.routes.health import router as health_router
from thytrader.api.routes.portfolio import router as portfolio_router
from thytrader.api.routes.portfolio_history import router as portfolio_history_router
from thytrader.config import Settings
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.persistence.database import create_engine, dispose, ping
from thytrader.persistence.portfolio_history import (
    DisabledPortfolioHistoryStore,
    PortfolioHistoryStore,
)
from thytrader.persistence.postgres_history import PostgresPortfolioHistoryStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.runtime import RuntimeState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

_logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    portfolio_service: PortfolioService | None = None,
    history_store: PortfolioHistoryStore | None = None,
) -> FastAPI:
    """Create a configured ThyTrader API application.

    When a caller passes an explicit ``history_store`` (e.g. test doubles), it is
    used directly without engine creation. Otherwise the store is derived from
    ``settings.database_url`` during the lifespan: PostgreSQL when configured,
    or disabled when absent/blank.
    """
    resolved_settings = settings or Settings()
    runtime = RuntimeState(settings=resolved_settings)
    external_store = history_store
    engine: AsyncEngine | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Initialize persistence, mark ready, and dispose resources on shutdown."""
        nonlocal engine

        store = external_store
        if store is None and resolved_settings.database_url is not None:
            engine = create_engine(resolved_settings.database_url)
            try:
                await ping(engine)
            except Exception:
                await dispose(engine)
                _logger.exception("Database connectivity check failed")
                raise
            store = PostgresPortfolioHistoryStore(engine)

        _app.state.history_store = store or DisabledPortfolioHistoryStore()

        runtime.ready = True
        try:
            yield
        finally:
            runtime.ready = False
            if engine is not None:
                await dispose(engine)

    app = FastAPI(title="ThyTrader API", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.portfolio_service = portfolio_service or _build_portfolio_service(resolved_settings)
    app.include_router(health_router)
    app.include_router(portfolio_router)
    app.include_router(portfolio_history_router)
    return app


def _build_portfolio_service(settings: Settings) -> PortfolioService:
    """Build a live Coinbase service when credentials exist, otherwise demo data."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return PortfolioService(DemoExchangeAccount(), demo=True)

    client = RESTClient(
        api_key=settings.coinbase_api_key_name.get_secret_value(),
        api_secret=settings.coinbase_api_private_key.get_secret_value(),
        timeout=10,
    )
    return PortfolioService(CoinbaseAccount(client))
