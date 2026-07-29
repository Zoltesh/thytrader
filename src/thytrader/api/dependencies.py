"""Typed FastAPI dependencies."""

# FastAPI resolves these dependency annotations at runtime.
from fastapi import Request  # noqa: TC002

from thytrader.market_data.service import MarketDataService
from thytrader.market_data.worker_state import MarketDataWorkerStateStore
from thytrader.persistence.portfolio_history import PortfolioHistoryStore
from thytrader.portfolio.service import PortfolioService
from thytrader.runtime import RuntimeState


def get_runtime_state(request: Request) -> RuntimeState:
    """Return the validated ThyTrader runtime attached to the application."""
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, RuntimeState):
        message = "ThyTrader runtime state is unavailable."
        raise TypeError(message)
    return runtime


def get_portfolio_service(request: Request) -> PortfolioService:
    """Return the configured portfolio service from application state."""
    service = getattr(request.app.state, "portfolio_service", None)
    if not isinstance(service, PortfolioService):
        message = "Portfolio service is unavailable."
        raise TypeError(message)
    return service


def get_market_data_service(request: Request) -> MarketDataService:
    """Return the configured read-only market-data service from application state."""
    service = getattr(request.app.state, "market_data_service", None)
    if not isinstance(service, MarketDataService):
        message = "Market-data service is unavailable."
        raise TypeError(message)
    return service


def get_history_store(request: Request) -> PortfolioHistoryStore:
    """Return the append-only history boundary attached during app construction."""
    store = getattr(request.app.state, "history_store", None)
    if not isinstance(store, PortfolioHistoryStore):
        message = "Portfolio history store is unavailable."
        raise TypeError(message)
    return store


def get_market_data_state_store(request: Request) -> MarketDataWorkerStateStore:
    """Return durable market-data worker state attached during app startup."""
    store = getattr(request.app.state, "market_data_state_store", None)
    if not isinstance(store, MarketDataWorkerStateStore):
        message = "Market-data worker state store is unavailable."
        raise TypeError(message)
    return store
