"""Read-only operator diagnostics HTTP contract."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, Query

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_backtest_result_store,
    get_dataset_store,
    get_execution_store,
    get_history_store,
    get_market_data_service,
    get_market_data_state_store,
    get_market_data_watchlist_store,
    get_portfolio_service,
    get_runtime_state,
    get_strategy_draft_store,
    get_strategy_publication_catalog,
)
from thytrader.execution.store import ExecutionStore  # noqa: TC001
from thytrader.market_data.datasets import DatasetStore  # noqa: TC001
from thytrader.market_data.service import MarketDataService  # noqa: TC001
from thytrader.market_data.watchlist import MarketDataWatchlistStore  # noqa: TC001
from thytrader.market_data.worker_state import MarketDataWorkerStateStore  # noqa: TC001
from thytrader.operator.models import (
    ConfigurationReport,
    DataCatalogReport,
    ExchangeReport,
    HealthReport,
    IndicatorsReport,
    MarketDataReport,
    PerformanceReport,
    ProductsReport,
    ReconciliationReport,
    RiskReport,
    RuntimeReport,
    StrategiesReport,
    SupportBundleReport,
)
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.audit_events import AuditEventStore  # noqa: TC001
from thytrader.persistence.backtest_results import BacktestResultReader  # noqa: TC001
from thytrader.persistence.portfolio_history import PortfolioHistoryStore  # noqa: TC001
from thytrader.portfolio.service import PortfolioService  # noqa: TC001
from thytrader.runtime import RuntimeState  # noqa: TC001
from thytrader.strategies.authoring import StrategyDraftStore  # noqa: TC001
from thytrader.strategies.publication import StrategyPublicationCatalog  # noqa: TC001

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


def get_operator_diagnostics(
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    portfolio: Annotated[PortfolioService, Depends(get_portfolio_service)],
    market_data_state: Annotated[MarketDataWorkerStateStore, Depends(get_market_data_state_store)],
    history: Annotated[PortfolioHistoryStore, Depends(get_history_store)],
    publications: Annotated[StrategyPublicationCatalog, Depends(get_strategy_publication_catalog)],
    drafts: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
    backtests: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    dataset_store: Annotated[DatasetStore, Depends(get_dataset_store)],
    watchlist: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> OperatorDiagnostics:
    """Assemble diagnostics from the same application services as browser routes."""
    return OperatorDiagnostics(
        settings=runtime.settings,
        portfolio=portfolio,
        market_data_state=market_data_state,
        history=history,
        publications=publications,
        drafts=drafts,
        backtests=backtests,
        execution=execution,
        audit=audit,
        runtime=runtime,
        dataset_store=dataset_store,
        watchlist=watchlist,
        market_data=market_data,
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
    product_id: Annotated[str | None, Query(pattern=r"^[A-Z0-9]{2,20}-USD$")] = None,
    timeframe: Annotated[str, Query(pattern=r"^(1h|5m)$")] = "1h",
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
    """Return pause and mismatch findings without a risk-policy registry."""
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


@router.get("/support-bundle", response_model=SupportBundleReport)
async def get_operator_support_bundle(
    diagnostics: Annotated[OperatorDiagnostics, Depends(get_operator_diagnostics)],
) -> SupportBundleReport:
    """Return the redacted diagnostic bundle."""
    return await diagnostics.support_bundle()
