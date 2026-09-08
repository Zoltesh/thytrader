"""Construct operator diagnostics from process settings without FastAPI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from coinbase.rest import RESTClient

from thytrader.config import Settings
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.execution.store import DisabledExecutionStore
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.audit_events import DisabledAuditEventStore
from thytrader.persistence.backtest_results import DisabledBacktestResultStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.portfolio_history import DisabledPortfolioHistoryStore
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_history import PostgresPortfolioHistoryStore
from thytrader.persistence.postgres_market_data_worker import PostgresMarketDataWorkerStateStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.strategies.authoring import DisabledStrategyDraftStore
from thytrader.strategies.publication import DisabledStrategyPublicationStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def operator_diagnostics(
    settings: Settings | None = None,
) -> AsyncIterator[OperatorDiagnostics]:
    """Yield diagnostics backed by PostgreSQL when configured, otherwise disabled stores."""
    resolved = settings or Settings()
    engine = None
    if resolved.database_url is not None:
        engine = create_engine(resolved.database_url)
        dataset_store = DatasetStore(resolved.market_data_dataset_root)
        strategy_store = PostgresStrategyPublicationStore(engine)
        diagnostics = OperatorDiagnostics(
            settings=resolved,
            portfolio=_portfolio_service(resolved),
            market_data_state=PostgresMarketDataWorkerStateStore(engine),
            history=PostgresPortfolioHistoryStore(engine),
            publications=strategy_store,
            drafts=strategy_store,
            backtests=PostgresBacktestResultStore(
                engine,
                research_run_store=PostgresResearchRunStore(engine),
                dataset_store=dataset_store,
            ),
            execution=PostgresExecutionStore(engine),
            audit=PostgresAuditEventStore(engine),
            engine=engine,
        )
    else:
        diagnostics = OperatorDiagnostics(
            settings=resolved,
            portfolio=_portfolio_service(resolved),
            market_data_state=DisabledMarketDataWorkerStateStore(),
            history=DisabledPortfolioHistoryStore(),
            publications=DisabledStrategyPublicationStore(),
            drafts=DisabledStrategyDraftStore(),
            backtests=DisabledBacktestResultStore(),
            execution=DisabledExecutionStore(),
            audit=DisabledAuditEventStore(),
        )
    try:
        yield diagnostics
    finally:
        if engine is not None:
            await dispose(engine)


def _portfolio_service(settings: Settings) -> PortfolioService:
    """Mirror API construction: live Coinbase when credentials exist, otherwise demo."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return PortfolioService(DemoExchangeAccount(), demo=True)
    client = RESTClient(
        api_key=settings.coinbase_api_key_name.get_secret_value(),
        api_secret=settings.coinbase_api_private_key.get_secret_value(),
        timeout=10,
    )
    return PortfolioService(CoinbaseAccount(client))
