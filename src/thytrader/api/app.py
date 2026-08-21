"""FastAPI application construction."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import TYPE_CHECKING

from coinbase.rest import RESTClient
from fastapi import FastAPI

from thytrader import __version__
from thytrader.api.routes.audit_events import router as audit_events_router
from thytrader.api.routes.backtests import router as backtests_router
from thytrader.api.routes.fees import router as fees_router
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
from thytrader.market_data.feed_state import (
    DisabledMarketFeedStateStore,
    MarketFeedStateStore,
)
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.worker_state import (
    DisabledMarketDataWorkerStateStore,
    MarketDataWorkerStateStore,
)
from thytrader.persistence.audit_events import (
    AuditEventStore,
    DisabledAuditEventStore,
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
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_history import PostgresPortfolioHistoryStore
from thytrader.persistence.postgres_market_data_worker import PostgresMarketDataWorkerStateStore
from thytrader.persistence.postgres_market_feed import PostgresMarketFeedStateStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.runtime import RuntimeState
from thytrader.strategies.authoring import DisabledStrategyDraftStore, StrategyDraftStore
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
    audit_event_store: AuditEventStore | None = None,
    market_data_state_store: MarketDataWorkerStateStore | None = None,
    market_feed_state_store: MarketFeedStateStore | None = None,
    backtest_result_store: BacktestResultReader | None = None,
    backtest_benchmark_reader: BacktestBenchmarkReader | None = None,
    strategy_store: StrategyPublicationStore | None = None,
    strategy_draft_store: StrategyDraftStore | None = None,
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
    external_audit_event_store = audit_event_store
    external_market_data_state_store = market_data_state_store
    external_market_feed_state_store = market_feed_state_store
    external_backtest_result_store = backtest_result_store
    external_backtest_benchmark_reader = backtest_benchmark_reader
    external_strategy_store = strategy_store
    external_strategy_draft_store = strategy_draft_store
    external_backtest_submitter = backtest_submitter
    engine: AsyncEngine | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Initialize persistence, mark ready, and dispose resources on shutdown."""
        nonlocal engine

        store = external_store
        audit_store = external_audit_event_store
        worker_state_store = external_market_data_state_store
        feed_state_store = external_market_feed_state_store
        backtest_store = external_backtest_result_store
        benchmark_reader = external_backtest_benchmark_reader
        publication_store = external_strategy_store
        draft_store = external_strategy_draft_store
        submitter = external_backtest_submitter
        dataset_store = DatasetStore(resolved_settings.market_data_dataset_root)
        needs_database = (
            store is None
            or audit_store is None
            or worker_state_store is None
            or feed_state_store is None
            or backtest_store is None
            or publication_store is None
            or draft_store is None
        )
        if needs_database and resolved_settings.database_url is not None:
            engine = create_engine(resolved_settings.database_url)
            try:
                await ping(engine)
            except Exception:
                await dispose(engine)
                _logger.exception("Database connectivity check failed")
                raise
            (
                store,
                audit_store,
                worker_state_store,
                feed_state_store,
                publication_store,
                draft_store,
                backtest_store,
            ) = _init_db_stores(
                engine=engine,
                dataset_store=dataset_store,
                store=store,
                audit_store=audit_store,
                worker_state_store=worker_state_store,
                feed_state_store=feed_state_store,
                publication_store=publication_store,
                draft_store=draft_store,
                backtest_store=backtest_store,
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
        _app.state.audit_event_store = audit_store or DisabledAuditEventStore()
        _app.state.market_data_state_store = (
            worker_state_store or DisabledMarketDataWorkerStateStore()
        )
        _app.state.market_feed_state_store = feed_state_store or DisabledMarketFeedStateStore()
        _app.state.backtest_result_store = backtest_store or DisabledBacktestResultStore()
        _app.state.backtest_benchmark_reader = benchmark_reader or DisabledBacktestBenchmarkReader()
        _app.state.backtest_submitter = submitter or DisabledBacktestSubmitter()
        _app.state.strategy_draft_store = draft_store or DisabledStrategyDraftStore()
        _app.state.strategy_publication_store = (
            publication_store or DisabledStrategyPublicationStore()
        )

        runtime.ready = True
        try:
            yield
        finally:
            runtime.ready = False
            await _dispose_if_present(engine)

    app = FastAPI(title="ThyTrader API", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.portfolio_service = portfolio_service or _build_portfolio_service(resolved_settings)
    app.state.market_data_service = market_data_service or _build_market_data_service(
        resolved_settings
    )
    app.state.dataset_store = DatasetStore(resolved_settings.market_data_dataset_root)
    app.include_router(health_router)
    app.include_router(audit_events_router)
    app.include_router(fees_router)
    app.include_router(market_data_router)
    app.include_router(market_data_ingestion_router)
    app.include_router(portfolio_router)
    app.include_router(portfolio_history_router)
    app.include_router(strategies_router)
    app.include_router(backtests_router)
    return app


def _init_db_stores(
    *,
    engine: AsyncEngine,
    dataset_store: DatasetStore,
    store: PortfolioHistoryStore | None,
    audit_store: AuditEventStore | None,
    worker_state_store: MarketDataWorkerStateStore | None,
    feed_state_store: MarketFeedStateStore | None,
    publication_store: StrategyPublicationStore | None,
    draft_store: StrategyDraftStore | None,
    backtest_store: BacktestResultReader | None,
) -> tuple[
    PortfolioHistoryStore,
    AuditEventStore,
    MarketDataWorkerStateStore,
    MarketFeedStateStore,
    StrategyPublicationStore,
    StrategyDraftStore,
    BacktestResultReader,
]:
    """Instantiate database-backed persistence stores when missing."""
    resolved_store = store or PostgresPortfolioHistoryStore(engine)
    resolved_audit = audit_store or PostgresAuditEventStore(engine)
    resolved_worker = worker_state_store or PostgresMarketDataWorkerStateStore(engine)
    resolved_feed = feed_state_store or PostgresMarketFeedStateStore(engine)
    resolved_publication = publication_store or PostgresStrategyPublicationStore(engine)
    resolved_draft = draft_store or PostgresStrategyPublicationStore(engine)
    resolved_backtest = backtest_store or PostgresBacktestResultStore(
        engine,
        research_run_store=PostgresResearchRunStore(engine),
        dataset_store=dataset_store,
    )
    return (
        resolved_store,
        resolved_audit,
        resolved_worker,
        resolved_feed,
        resolved_publication,
        resolved_draft,
        resolved_backtest,
    )


async def _dispose_if_present(engine: AsyncEngine | None) -> None:
    """Dispose a lifespan-owned database engine only when the API created one."""
    if engine is not None:
        await dispose(engine)


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
