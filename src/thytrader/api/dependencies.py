"""Typed FastAPI dependencies."""

# FastAPI resolves these dependency annotations at runtime.
from typing import cast

from fastapi import Request  # noqa: TC002
from sqlalchemy.ext.asyncio import AsyncEngine

from thytrader.backtest.submission import BacktestSubmitter
from thytrader.exchanges.protocols import ExchangeAccount  # noqa: TC001
from thytrader.execution.broker import Broker  # noqa: TC001
from thytrader.execution.store import ExecutionStore
from thytrader.execution.user_feed_state import UserOrderFeedStateStore
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.feed_state import MarketFeedStateStore
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.watchlist import MarketDataWatchlistStore
from thytrader.market_data.worker_state import MarketDataWorkerStateStore
from thytrader.memory.notify import NotificationSender
from thytrader.memory.store import ExperientialMemoryStore
from thytrader.operator_chat.service import OperatorChatService
from thytrader.persistence.audit_events import AuditEventStore
from thytrader.persistence.backtest_benchmarks import BacktestBenchmarkReader
from thytrader.persistence.backtest_results import BacktestResultReader
from thytrader.persistence.portfolio_history import PortfolioHistoryStore
from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
from thytrader.portfolio.service import PortfolioService
from thytrader.research.catalog import ResearchStudyCatalog
from thytrader.risk.store import RiskPolicyStore
from thytrader.runtime import RuntimeState
from thytrader.security.boundary import TrustBoundary
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


def get_market_data_watchlist_store(request: Request) -> MarketDataWatchlistStore:
    """Return the durable ingestion watchlist attached during app startup."""
    store = getattr(request.app.state, "market_data_watchlist_store", None)
    if not isinstance(store, MarketDataWatchlistStore):
        message = "Market-data watchlist store is unavailable."
        raise TypeError(message)
    return store


def get_market_feed_state_store(request: Request) -> MarketFeedStateStore:
    """Return durable public ticker state attached during app startup."""
    store = getattr(request.app.state, "market_feed_state_store", None)
    if not isinstance(store, MarketFeedStateStore):
        message = "Market-feed state store is unavailable."
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


def get_audit_event_store(request: Request) -> AuditEventStore:
    """Return the append-only audit event boundary attached during app startup."""
    store = getattr(request.app.state, "audit_event_store", None)
    if not isinstance(store, AuditEventStore):
        message = "Audit event store is unavailable."
        raise TypeError(message)
    return store


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


def get_paper_broker(request: Request) -> Broker:
    """Return the paper broker attached during app startup."""
    broker = getattr(request.app.state, "paper_broker", None)
    if broker is None:
        message = "Paper broker is unavailable."
        raise TypeError(message)
    # Protocol values on app.state cannot be isinstance-checked.
    return cast("Broker", broker)


def get_live_broker(request: Request) -> Broker | None:
    """Return the live broker when credentials constructed one, else None."""
    broker = getattr(request.app.state, "live_broker", None)
    if broker is None:
        return None
    return cast("Broker", broker)


def get_quote_reader(request: Request) -> ExchangeAccount | None:
    """Return the live quote-balance reader when one is attached."""
    reader = getattr(request.app.state, "quote_reader", None)
    if reader is None:
        return None
    return cast("ExchangeAccount", reader)


def get_execution_store(request: Request) -> ExecutionStore:
    """Return the paper/live execution boundary attached during app startup."""
    store = getattr(request.app.state, "execution_store", None)
    if not isinstance(store, ExecutionStore):
        message = "Execution store is unavailable."
        raise TypeError(message)
    return store


def get_risk_policy_store(request: Request) -> RiskPolicyStore:
    """Return the risk-policy registry attached during app startup."""
    store = getattr(request.app.state, "risk_policy_store", None)
    if not isinstance(store, RiskPolicyStore):
        message = "Risk-policy store is unavailable."
        raise TypeError(message)
    return store


def get_user_order_feed_state_store(request: Request) -> UserOrderFeedStateStore:
    """Return durable user-order feed state attached during app startup."""
    store = getattr(request.app.state, "user_order_feed_state_store", None)
    if not isinstance(store, UserOrderFeedStateStore):
        message = "User-order feed state store is unavailable."
        raise TypeError(message)
    return store


def get_memory_store(request: Request) -> ExperientialMemoryStore:
    """Return experiential memory storage attached during app startup."""
    store = getattr(request.app.state, "memory_store", None)
    if not isinstance(store, ExperientialMemoryStore):
        message = "Experiential memory store is unavailable."
        raise TypeError(message)
    return store


def get_research_study_catalog(request: Request) -> ResearchStudyCatalog:
    """Return the persisted research-study catalog attached during app startup."""
    store = getattr(request.app.state, "research_study_catalog", None)
    if not isinstance(store, ResearchStudyCatalog):
        message = "Research study catalog is unavailable."
        raise TypeError(message)
    return store


def get_notification_sender(request: Request) -> NotificationSender:
    """Return the configured notification sender attached during app startup."""
    sender = getattr(request.app.state, "notification_sender", None)
    if not isinstance(sender, NotificationSender):
        message = "Notification sender is unavailable."
        raise TypeError(message)
    return sender


def get_database_engine(request: Request) -> AsyncEngine | None:
    """Return the lifespan-owned engine when PostgreSQL was initialized."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return None
    if not isinstance(engine, AsyncEngine):
        message = "Database engine is unavailable."
        raise TypeError(message)
    return engine


def get_worker_heartbeat_store(request: Request) -> WorkerHeartbeatStore:
    """Return worker heartbeat storage attached during app startup."""
    store = getattr(request.app.state, "worker_heartbeat_store", None)
    if not isinstance(store, WorkerHeartbeatStore):
        message = "Worker heartbeat store is unavailable."
        raise TypeError(message)
    return store


def get_trust_boundary(request: Request) -> TrustBoundary:
    """Return the application trust boundary attached during app construction."""
    boundary = getattr(request.app.state, "trust_boundary", None)
    if not isinstance(boundary, TrustBoundary):
        message = "Trust boundary is unavailable."
        raise TypeError(message)
    return boundary


def get_operator_chat_service(request: Request) -> OperatorChatService:
    """Return the in-app operator chat service attached during app construction."""
    service = getattr(request.app.state, "operator_chat_service", None)
    if not isinstance(service, OperatorChatService):
        message = "Operator chat service is unavailable."
        raise TypeError(message)
    return service
