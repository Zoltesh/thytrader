"""FastAPI application construction."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import TYPE_CHECKING

from coinbase.rest import RESTClient
from fastapi import FastAPI

from thytrader import __version__
from thytrader.api.routes.backtests import router as backtests_router
from thytrader.api.routes.health import router as health_router
from thytrader.api.routes.market_data import router as market_data_router
from thytrader.api.routes.market_data_ingestion import router as market_data_ingestion_router
from thytrader.api.routes.portfolio import router as portfolio_router
from thytrader.api.routes.portfolio_history import router as portfolio_history_router
from thytrader.api.routes.strategies import router as strategies_router
from thytrader.backtest.submission import (
    BacktestSubmitter,
    DisabledBacktestSubmitter,
    PostgresBacktestSubmitter,
)
from thytrader.config import Settings
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketData
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.worker_state import (
    DisabledMarketDataWorkerStateStore,
    MarketDataWorkerStateStore,
)
from thytrader.persistence.backtest_benchmarks import (
    BacktestBenchmarkReader,
    DisabledBacktestBenchmarkReader,
    PostgresBacktestBenchmarkReader,
)
from thytrader.persistence.backtest_results import (
    BacktestResultReader,
    DisabledBacktestResultStore,
)
from thytrader.persistence.database import create_engine, dispose, ping
from thytrader.persistence.portfolio_history import (
    DisabledPortfolioHistoryStore,
    PortfolioHistoryStore,
)
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_history import PostgresPortfolioHistoryStore
from thytrader.persistence.postgres_market_data_worker import PostgresMarketDataWorkerStateStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.runtime import RuntimeState
from thytrader.strategies.publication import (
    DisabledStrategyPublicationStore,
    StrategyPublicationStore,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

_logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    portfolio_service: PortfolioService | None = None,
    market_data_service: MarketDataService | None = None,
    history_store: PortfolioHistoryStore | None = None,
    market_data_state_store: MarketDataWorkerStateStore | None = None,
    backtest_result_store: BacktestResultReader | None = None,
    backtest_benchmark_reader: BacktestBenchmarkReader | None = None,
    strategy_store: StrategyPublicationStore | None = None,
    backtest_submitter: BacktestSubmitter | None = None,
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
    external_market_data_state_store = market_data_state_store
    external_backtest_result_store = backtest_result_store
    external_backtest_benchmark_reader = backtest_benchmark_reader
    external_strategy_store = strategy_store
    external_backtest_submitter = backtest_submitter
    engine: AsyncEngine | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Initialize persistence, mark ready, and dispose resources on shutdown."""
        nonlocal engine

        store = external_store
        worker_state_store = external_market_data_state_store
        backtest_store = external_backtest_result_store
        benchmark_reader = external_backtest_benchmark_reader
        publication_store = external_strategy_store
        submitter = external_backtest_submitter
        dataset_store: DatasetStore | None = None
        needs_database = (
            store is None
            or worker_state_store is None
            or backtest_store is None
            or publication_store is None
        )
        if needs_database and resolved_settings.database_url is not None:
            engine = create_engine(resolved_settings.database_url)
            try:
                await ping(engine)
            except Exception:
                await dispose(engine)
                _logger.exception("Database connectivity check failed")
                raise
            if store is None:
                store = PostgresPortfolioHistoryStore(engine)
            if worker_state_store is None:
                worker_state_store = PostgresMarketDataWorkerStateStore(engine)
            dataset_store = DatasetStore(resolved_settings.market_data_dataset_root)
            if publication_store is None:
                publication_store = PostgresStrategyPublicationStore(engine)
            if backtest_store is None:
                backtest_store = PostgresBacktestResultStore(
                    engine,
                    research_run_store=PostgresResearchRunStore(engine),
                    dataset_store=dataset_store,
                )
            submitter = _submission_service(submitter, engine, dataset_store)
        if benchmark_reader is None and isinstance(backtest_store, PostgresBacktestResultStore):
            benchmark_dataset_store = dataset_store or DatasetStore(
                resolved_settings.market_data_dataset_root
            )
            benchmark_reader = PostgresBacktestBenchmarkReader(
                result_reader=backtest_store,
                source_reader=backtest_store,
                dataset_store=benchmark_dataset_store,
            )

        _app.state.history_store = store or DisabledPortfolioHistoryStore()
        _app.state.market_data_state_store = (
            worker_state_store or DisabledMarketDataWorkerStateStore()
        )
        _app.state.backtest_result_store = backtest_store or DisabledBacktestResultStore()
        _app.state.backtest_benchmark_reader = benchmark_reader or DisabledBacktestBenchmarkReader()
        _app.state.backtest_submitter = submitter or DisabledBacktestSubmitter()
        _app.state.strategy_publication_store = (
            publication_store or DisabledStrategyPublicationStore()
        )

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
    app.state.market_data_service = market_data_service or _build_market_data_service(
        resolved_settings
    )
    app.include_router(health_router)
    app.include_router(market_data_router)
    app.include_router(market_data_ingestion_router)
    app.include_router(portfolio_router)
    app.include_router(portfolio_history_router)
    app.include_router(strategies_router)
    app.include_router(backtests_router)
    return app


def _submission_service(
    submitter: BacktestSubmitter | None,
    engine: AsyncEngine,
    dataset_store: DatasetStore,
) -> BacktestSubmitter:
    """Preserve an injected test boundary or construct the durable production submitter."""
    return submitter or PostgresBacktestSubmitter(engine, dataset_store)


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


def _build_market_data_service(settings: Settings) -> MarketDataService:
    """Build a live preview with credentials or a deterministic local demo otherwise."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return MarketDataService(DemoMarketData())
    client = RESTClient(
        api_key=settings.coinbase_api_key_name.get_secret_value(),
        api_secret=settings.coinbase_api_private_key.get_secret_value(),
        timeout=10,
    )
    return MarketDataService(CoinbaseMarketData(client))
