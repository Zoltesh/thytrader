"""Construct operator diagnostics from process settings without FastAPI.

Used by `thytrader-operator --local`. The default CLI transport is loopback HTTP.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from coinbase.rest import RESTClient

from thytrader.alerts.store import DisabledAlertStore
from thytrader.audit_events import DisabledAuditEventStore
from thytrader.backtest.results import DisabledBacktestResultStore
from thytrader.config import Settings
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.coinbase_futures_catalog import CoinbaseFuturesCatalog
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketData
from thytrader.execution.decision_store import DisabledDecisionJournalStore
from thytrader.execution.futures_start import FuturesStart
from thytrader.execution.user_feed_state import DisabledUserOrderFeedStateStore
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.watchlist import DisabledMarketDataWatchlistStore
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.memory.store import DisabledExperientialMemoryStore
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.portfolio_history import DisabledPortfolioHistoryStore
from thytrader.persistence.postgres_alerts import PostgresAlertStore
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_decisions import PostgresDecisionJournalStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_execution_cycles import PostgresExecutionCycleStore
from thytrader.persistence.postgres_futures import PostgresFuturesObservationStore
from thytrader.persistence.postgres_futures_account import PostgresFuturesAccountStore
from thytrader.persistence.postgres_futures_books import PostgresFuturesContractStore
from thytrader.persistence.postgres_history import PostgresPortfolioHistoryStore
from thytrader.persistence.postgres_market_data_watchlist import PostgresMarketDataWatchlistStore
from thytrader.persistence.postgres_market_data_worker import PostgresMarketDataWorkerStateStore
from thytrader.persistence.postgres_memory import PostgresExperientialMemoryStore
from thytrader.persistence.postgres_portfolios import PostgresPortfolioStore
from thytrader.persistence.postgres_research_queue import PostgresResearchQueue
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_risk import PostgresRiskPolicyStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.postgres_studies import PostgresResearchStudyCatalog
from thytrader.persistence.postgres_user_feed import PostgresUserOrderFeedStateStore
from thytrader.persistence.postgres_worker_heartbeats import PostgresWorkerHeartbeatStore
from thytrader.persistence.worker_heartbeats import DisabledWorkerHeartbeatStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.portfolios.store import DisabledPortfolioStore
from thytrader.research.catalog import DisabledResearchStudyCatalog
from thytrader.risk.store import DisabledRiskPolicyStore
from thytrader.strategies.library import DisabledStrategyStore
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.store import DisabledExecutionStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def operator_diagnostics(
    settings: Settings | None = None,
) -> AsyncIterator[OperatorDiagnostics]:
    """Yield diagnostics backed by PostgreSQL when configured, otherwise disabled stores."""
    resolved = settings or Settings()
    engine = None
    dataset_store = DatasetStore(resolved.market_data_dataset_root)
    market_data = _market_data_service(resolved)
    if resolved.database_url is not None:
        engine = create_engine(resolved.database_url)
        strategy_store = PostgresStrategyStore(engine)
        diagnostics = OperatorDiagnostics(
            settings=resolved,
            portfolio=_portfolio_service(resolved),
            market_data_state=PostgresMarketDataWorkerStateStore(engine),
            history=PostgresPortfolioHistoryStore(engine),
            publications=strategy_store,
            strategies_store=strategy_store,
            backtests=PostgresBacktestResultStore(
                engine,
                research_run_store=PostgresResearchRunStore(engine),
                dataset_store=dataset_store,
            ),
            execution=PostgresExecutionStore(engine),
            audit=PostgresAuditEventStore(engine),
            engine=engine,
            dataset_store=dataset_store,
            watchlist=PostgresMarketDataWatchlistStore(engine),
            market_data=market_data,
            heartbeat_store=PostgresWorkerHeartbeatStore(engine),
            risk_policies=PostgresRiskPolicyStore(engine),
            user_order_feed=PostgresUserOrderFeedStateStore(engine),
            memory_store=PostgresExperientialMemoryStore(engine),
            research_studies=PostgresResearchStudyCatalog(engine),
            decision_store=PostgresDecisionJournalStore(engine),
            portfolios=PostgresPortfolioStore(engine),
            research_queue=PostgresResearchQueue(engine),
            alert_store=PostgresAlertStore(engine),
            futures_store=PostgresFuturesObservationStore(engine),
            futures_account_store=PostgresFuturesAccountStore(engine),
            futures_book_stores=FuturesStart(
                contracts=PostgresFuturesContractStore(engine),
                observations=PostgresFuturesObservationStore(engine),
            ),
            futures_account_history_store=PostgresFuturesAccountStore(engine),
            cycle_store=PostgresExecutionCycleStore(engine),
        )
    else:
        diagnostics = OperatorDiagnostics(
            settings=resolved,
            portfolio=_portfolio_service(resolved),
            market_data_state=DisabledMarketDataWorkerStateStore(),
            history=DisabledPortfolioHistoryStore(),
            publications=DisabledStrategySnapshotStore(),
            strategies_store=DisabledStrategyStore(),
            backtests=DisabledBacktestResultStore(),
            execution=DisabledExecutionStore(),
            audit=DisabledAuditEventStore(),
            dataset_store=dataset_store,
            watchlist=DisabledMarketDataWatchlistStore(),
            market_data=market_data,
            heartbeat_store=DisabledWorkerHeartbeatStore(),
            risk_policies=DisabledRiskPolicyStore(),
            user_order_feed=DisabledUserOrderFeedStateStore(),
            memory_store=DisabledExperientialMemoryStore(),
            research_studies=DisabledResearchStudyCatalog(),
            decision_store=DisabledDecisionJournalStore(),
            portfolios=DisabledPortfolioStore(),
            alert_store=DisabledAlertStore(),
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


def _market_data_service(settings: Settings) -> MarketDataService:
    """Mirror API construction: Coinbase when credentials exist, otherwise demo."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return MarketDataService(DemoMarketData())
    client = RESTClient(
        api_key=settings.coinbase_api_key_name.get_secret_value(),
        api_secret=settings.coinbase_api_private_key.get_secret_value(),
        timeout=10,
    )
    return MarketDataService(
        CoinbaseMarketData(client), futures_provider=CoinbaseFuturesCatalog(client)
    )
