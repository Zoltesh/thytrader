"""Typed FastAPI dependencies."""

# FastAPI resolves these dependency annotations at runtime.
from fastapi import Request  # noqa: TC002

from thytrader.backtest.submission import BacktestSubmitter
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.worker_state import MarketDataWorkerStateStore
from thytrader.persistence.backtest_benchmarks import BacktestBenchmarkReader
from thytrader.persistence.backtest_results import BacktestResultReader
from thytrader.persistence.portfolio_history import PortfolioHistoryStore
from thytrader.portfolio.service import PortfolioService
from thytrader.runtime import RuntimeState
from thytrader.strategies.authoring import (
    StrategyDraftStore,
)
from thytrader.strategies.publication import StrategyPublicationCatalog, StrategyPublicationStore


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
    """Return the read-only market-data service attached during app startup."""
    service = getattr(request.app.state, "market_data_service", None)
    if not isinstance(service, MarketDataService):
        message = "Market-data service is unavailable."
        raise TypeError(message)
    return service


def get_dataset_store(request: Request) -> DatasetStore:
    """Return the immutable local dataset catalogue attached during app startup."""
    store = getattr(request.app.state, "dataset_store", None)
    if not isinstance(store, DatasetStore):
        message = "Dataset store is unavailable."
        raise TypeError(message)
    return store


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


def get_backtest_result_store(request: Request) -> BacktestResultReader:
    """Return the read-only backtest result boundary attached during app startup."""
    store = getattr(request.app.state, "backtest_result_store", None)
    if not isinstance(store, BacktestResultReader):
        message = "Backtest result store is unavailable."
        raise TypeError(message)
    return store


def get_backtest_submitter(request: Request) -> BacktestSubmitter:
    """Return the immutable research-submission boundary attached during app startup."""
    submitter = getattr(request.app.state, "backtest_submitter", None)
    if not isinstance(submitter, BacktestSubmitter):
        message = "Backtest submitter is unavailable."
        raise TypeError(message)
    return submitter


def get_backtest_benchmark_reader(request: Request) -> BacktestBenchmarkReader:
    """Return the read-only derived benchmark boundary attached during app startup."""
    reader = getattr(request.app.state, "backtest_benchmark_reader", None)
    if not isinstance(reader, BacktestBenchmarkReader):
        message = "Backtest benchmark reader is unavailable."
        raise TypeError(message)
    return reader


def get_strategy_draft_store(request: Request) -> StrategyDraftStore:
    """Return the durable draft boundary attached during app startup."""
    store = getattr(request.app.state, "strategy_draft_store", None)
    if not isinstance(store, StrategyDraftStore):
        message = "Strategy draft storage is unavailable."
        raise TypeError(message)
    return store


def get_strategy_publication_catalog(request: Request) -> StrategyPublicationCatalog:
    """Return immutable strategy discovery and archive operations from application state."""
    store = getattr(request.app.state, "strategy_publication_store", None)
    if not isinstance(store, StrategyPublicationCatalog):
        message = "Strategy publication catalog is unavailable."
        raise TypeError(message)
    return store


def get_strategy_publication_store(request: Request) -> StrategyPublicationStore:
    """Return the immutable strategy publication boundary attached during app startup."""
    store = getattr(request.app.state, "strategy_publication_store", None)
    if not isinstance(store, StrategyPublicationStore):
        message = "Strategy publication store is unavailable."
        raise TypeError(message)
    return store
