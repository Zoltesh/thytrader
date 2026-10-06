"""Read-only operator diagnostics HTTP contract."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: TC002

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_backtest_result_store,
    get_database_engine,
    get_dataset_store,
    get_decision_journal_store,
    get_execution_store,
    get_history_store,
    get_market_data_service,
    get_market_data_state_store,
    get_market_data_watchlist_store,
    get_memory_store,
    get_portfolio_service,
    get_portfolio_storage,
    get_research_queue,
    get_research_study_catalog,
    get_risk_policy_store,
    get_runtime_state,
    get_strategy_snapshot_store,
    get_strategy_store,
    get_user_order_feed_state_store,
    get_worker_heartbeat_store,
)
from thytrader.execution.decision_store import DecisionJournalStore  # noqa: TC001
from thytrader.execution.decisions import DECISION_PAGE_MAX_LIMIT, DecisionOutcome
from thytrader.execution.store import ExecutionStore  # noqa: TC001
from thytrader.execution.user_feed_state import UserOrderFeedStateStore  # noqa: TC001
from thytrader.market_data.datasets import DatasetStore  # noqa: TC001
from thytrader.market_data.models import DATASET_TIMEFRAME_PATTERN
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN
from thytrader.market_data.service import MarketDataService  # noqa: TC001
from thytrader.market_data.watchlist import MarketDataWatchlistStore  # noqa: TC001
from thytrader.market_data.worker_state import MarketDataWorkerStateStore  # noqa: TC001
from thytrader.memory.store import ExperientialMemoryStore  # noqa: TC001
from thytrader.operator.data_health import DataHealthReport, data_health_report
from thytrader.operator.models import (
    ConfigurationReport,
    DataCatalogReport,
    DecisionsReport,
    ExchangeReport,
    FeesReport,
    HealthReport,
    IndicatorsReport,
    MarketDataReport,
    MonitorReport,
    PerformanceReport,
    PortfolioReport,
    PortfoliosReport,
    ProductsReport,
    ReconciliationReport,
    RiskReport,
    RuntimeReport,
    StrategiesReport,
    StudiesReport,
    SupportBundleReport,
    TradeReasonsReport,
)
from thytrader.operator.readiness import ReadinessReport
from thytrader.operator.service import OperatorDiagnostics
from thytrader.operator.venue_reconciliation import VenueReconciliationReport
from thytrader.persistence.audit_events import AuditEventStore  # noqa: TC001
from thytrader.persistence.backtest_results import BacktestResultReader  # noqa: TC001
from thytrader.persistence.portfolio_history import PortfolioHistoryStore  # noqa: TC001
from thytrader.persistence.postgres_research_queue import (  # noqa: TC001 - FastAPI Depends.
    PostgresResearchQueue,
)
from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore  # noqa: TC001
from thytrader.portfolio.service import PortfolioService  # noqa: TC001
from thytrader.portfolios.store import PortfolioStorage  # noqa: TC001
from thytrader.research.catalog import ResearchStudyCatalog  # noqa: TC001
from thytrader.risk.store import RiskPolicyStore  # noqa: TC001
from thytrader.runtime import RuntimeState  # noqa: TC001
from thytrader.strategies.library import StrategyStore  # noqa: TC001
from thytrader.strategies.snapshots import StrategySnapshotStore  # noqa: TC001

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


def get_operator_diagnostics(
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    portfolio: Annotated[PortfolioService, Depends(get_portfolio_service)],
    market_data_state: Annotated[MarketDataWorkerStateStore, Depends(get_market_data_state_store)],
    history: Annotated[PortfolioHistoryStore, Depends(get_history_store)],
    publications: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    strategies: Annotated[StrategyStore, Depends(get_strategy_store)],
    backtests: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    dataset_store: Annotated[DatasetStore, Depends(get_dataset_store)],
    watchlist: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
    engine: Annotated[AsyncEngine | None, Depends(get_database_engine)],
    heartbeat_store: Annotated[WorkerHeartbeatStore, Depends(get_worker_heartbeat_store)],
    risk_policies: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
    user_order_feed: Annotated[UserOrderFeedStateStore, Depends(get_user_order_feed_state_store)],
    memory_store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    research_studies: Annotated[ResearchStudyCatalog, Depends(get_research_study_catalog)],
    decision_store: Annotated[DecisionJournalStore, Depends(get_decision_journal_store)],
    portfolios: Annotated[PortfolioStorage, Depends(get_portfolio_storage)],
    research_queue: Annotated[PostgresResearchQueue | None, Depends(get_research_queue)],
) -> OperatorDiagnostics:
    """Assemble diagnostics from the same application services as browser routes."""
    return OperatorDiagnostics(
        settings=runtime.settings,
        portfolio=portfolio,
        market_data_state=market_data_state,
        history=history,
        publications=publications,
        strategies_store=strategies,
        backtests=backtests,
        execution=execution,
        audit=audit,
        runtime=runtime,
        engine=engine,
        dataset_store=dataset_store,
        watchlist=watchlist,
        market_data=market_data,
        heartbeat_store=heartbeat_store,
        risk_policies=risk_policies,
        user_order_feed=user_order_feed,
        memory_store=memory_store,
        research_studies=research_studies,
        decision_store=decision_store,
        portfolios=portfolios,
        research_queue=research_queue,
    )


@router.get("/health", response_model=HealthReport)
async def get_operator_health(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> HealthReport:
    """Return the versioned health summary without mutating trading state."""
    return await diagnostics.health()


@router.get("/configuration", response_model=ConfigurationReport)
async def get_operator_configuration(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> ConfigurationReport:
    """Return redacted configuration validity."""
    return await diagnostics.configuration()


@router.get("/exchange", response_model=ExchangeReport)
async def get_operator_exchange(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> ExchangeReport:
    """Return Coinbase connectivity and detected permissions."""
    return await diagnostics.exchange()


@router.get("/market-data", response_model=MarketDataReport)
async def get_operator_market_data(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
    product_id: Annotated[str | None, Query(pattern=SPOT_PRODUCT_ID_PATTERN)] = None,
    timeframe: Annotated[str, Query(pattern=DATASET_TIMEFRAME_PATTERN)] = "1h",
) -> MarketDataReport:
    """Return freshness and gap evidence for one USD spot product and timeframe."""
    return await diagnostics.market_data_report(product_id, timeframe)


@router.get("/products", response_model=ProductsReport)
async def get_operator_products(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> ProductsReport:
    """Return enabled USD spot products from the current catalog."""
    return await diagnostics.products()


@router.get("/data-catalog", response_model=DataCatalogReport)
async def get_operator_data_catalog(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> DataCatalogReport:
    """Return local dataset coverage joined with the ingestion watchlist."""
    return await diagnostics.data_catalog()


@router.get("/data-health", response_model=DataHealthReport)
async def get_operator_data_health(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> DataHealthReport:
    """Return clock-aware tails for every enabled watch, without provider reads."""
    return data_health_report(await diagnostics.data_catalog())


@router.get("/indicators", response_model=IndicatorsReport)
async def get_operator_indicators(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> IndicatorsReport:
    """Return implemented indicator kinds without inventing unsupported studies."""
    return await diagnostics.indicators()


@router.get("/strategies", response_model=StrategiesReport)
async def get_operator_strategies(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> StrategiesReport:
    """Return draft, publication, and deployment status without order payloads."""
    return await diagnostics.strategies()


@router.get("/performance", response_model=PerformanceReport)
async def get_operator_performance(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
    result_fingerprint: Annotated[str | None, Query(pattern=r"^sha256:[0-9a-f]{64}$")] = None,
    deployment_id: UUID | None = None,
) -> PerformanceReport:
    """Return one backtest or runtime performance slice."""
    return await diagnostics.performance(
        result_fingerprint=result_fingerprint,
        deployment_id=deployment_id,
    )


@router.get("/risk", response_model=RiskReport)
async def get_operator_risk(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> RiskReport:
    """Return registry identity, slot counts, and pause/mismatch findings."""
    return await diagnostics.risk()


@router.get("/reconciliation", response_model=ReconciliationReport)
async def get_operator_reconciliation(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> ReconciliationReport:
    """Return mismatch and unknown-order findings."""
    return await diagnostics.reconciliation()


@router.get("/runtime", response_model=RuntimeReport)
async def get_operator_runtime(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
    deployment_id: UUID | None = None,
) -> RuntimeReport:
    """Return paper/live runtime status without trading authority."""
    return await diagnostics.runtime_report(deployment_id)


@router.get("/monitor", response_model=MonitorReport)
async def get_operator_monitor(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> MonitorReport:
    """Return deployments, recent journals, why-trade records, and notification delivery."""
    return await diagnostics.monitor()


@router.get("/studies", response_model=StudiesReport)
async def get_operator_studies(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> StudiesReport:
    """Return persisted research-study catalog rows without child equity."""
    return await diagnostics.studies()


@router.get("/portfolio", response_model=PortfolioReport)
async def get_operator_portfolio(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> PortfolioReport:
    """Return current Coinbase or demo balances without credentials."""
    return await diagnostics.portfolio_report()


@router.get("/fees", response_model=FeesReport)
async def get_operator_fees(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> FeesReport:
    """Return the current fee tier and research-only suggested maker/taker rates."""
    return await diagnostics.fees_report()


@router.get("/portfolios", response_model=PortfoliosReport)
async def get_operator_portfolios(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> PortfoliosReport:
    """List portfolios with sleeves, allocation, limits, manager settings, newest backtest."""
    return await diagnostics.portfolios_report()


@router.get("/readiness", response_model=ReadinessReport)
async def get_operator_readiness(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
    deployment_id: UUID | None = None,
    portfolio_id: UUID | None = None,
) -> ReadinessReport:
    """Return an advisory allocation, cap, fee, and breaker preflight.

    Read-only. This route never tightens risk policy or changes a deployment.
    """
    return await diagnostics.readiness_report(
        deployment_id=deployment_id, portfolio_id=portfolio_id
    )


@router.get("/venue-reconciliation", response_model=VenueReconciliationReport)
async def get_operator_venue_reconciliation(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> VenueReconciliationReport:
    """Compare managed inventory and working orders with a fresh venue listing.

    Read-only. This route never creates, cancels, or replaces an order.
    """
    return await diagnostics.venue_reconciliation_report()


@router.get("/trade-reasons", response_model=TradeReasonsReport)
async def get_operator_trade_reasons(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
    intent_id: UUID | None = None,
    deployment_id: UUID | None = None,
) -> TradeReasonsReport:
    """Return composed why-trade journals for human and agent review."""
    return await diagnostics.trade_reasons(intent_id=intent_id, deployment_id=deployment_id)


@router.get("/decisions", response_model=DecisionsReport)
async def get_operator_decisions(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
    deployment_id: UUID | None = None,
    strategy_id: UUID | None = None,
    outcome: Annotated[list[DecisionOutcome] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=DECISION_PAGE_MAX_LIMIT)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> DecisionsReport:
    """Return per-bar decisions (newest first) for one bot, one strategy, or every bot."""
    return await diagnostics.decisions(
        deployment_id=deployment_id,
        strategy_id=strategy_id,
        outcomes=tuple(outcome or ()),
        limit=limit,
        cursor=cursor,
    )


@router.get("/support-bundle", response_model=SupportBundleReport)
async def get_operator_support_bundle(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> SupportBundleReport:
    """Return the redacted diagnostic bundle."""
    return await diagnostics.support_bundle()
