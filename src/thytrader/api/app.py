"""FastAPI application construction."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from coinbase.rest import RESTClient
from fastapi import FastAPI

from thytrader import __version__
from thytrader.api.routes.health import router as health_router
from thytrader.api.routes.portfolio import router as portfolio_router
from thytrader.config import Settings
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.runtime import RuntimeState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def create_app(
    settings: Settings | None = None,
    portfolio_service: PortfolioService | None = None,
) -> FastAPI:
    """Create a configured ThyTrader API application."""
    resolved_settings = settings or Settings()
    runtime = RuntimeState(settings=resolved_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Mark the process ready only while application lifespan is active."""
        runtime.ready = True
        try:
            yield
        finally:
            runtime.ready = False

    app = FastAPI(title="ThyTrader API", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.portfolio_service = portfolio_service or _build_portfolio_service(resolved_settings)
    app.include_router(health_router)
    app.include_router(portfolio_router)
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
