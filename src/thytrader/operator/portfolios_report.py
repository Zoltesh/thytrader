"""Operator ``portfolios`` report: composition, deployment, breakers (ADR 0088, ADR 0091).

Read-only. Lists every portfolio (bounded) with its sleeves and their issues, the
allocation and largest single asset against ``max_per_asset_fraction``, the stored limits
and manager settings, the deployment state (not deployed, running, partially running,
paused, stopped), a latched portfolio breaker, the pending manager proposals, the newest
stored portfolio backtest, and queued/running backtest jobs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader import __version__
from thytrader.execution.models import ExecutionStoreError
from thytrader.operator.models import (
    STANDARD_REDACTION,
    ComponentReport,
    PortfolioBacktestDigest,
    PortfolioDigest,
    PortfolioSleeveDigest,
    PortfoliosPayload,
    PortfoliosReport,
    ReportStatus,
)
from thytrader.operator.status import recommend_next_action
from thytrader.portfolios.deployment import members, sleeve_books
from thytrader.portfolios.models import (
    PORTFOLIO_BACKTEST_CONTRACT,
    PortfolioError,
    PortfolioRuntimeState,
    sleeve_issues,
)
from thytrader.portfolios.rules import allocation_summary
from thytrader.research.jobs import ResearchJobStatus

if TYPE_CHECKING:
    from thytrader.execution.models import Deployment
    from thytrader.execution.store import ExecutionStore
    from thytrader.portfolios.models import PortfolioAggregate
    from thytrader.portfolios.store import PortfolioStorage

REPORT_LIMIT = 100
_ACTIVE = (ResearchJobStatus.QUEUED, ResearchJobStatus.RUNNING)
_IDLE = frozenset({"not_deployed", "stopped"})


async def build_portfolios_report(
    store: PortfolioStorage | None, execution: ExecutionStore | None = None
) -> PortfoliosReport:
    """Build the report; storage outages degrade it instead of failing the CLI."""
    now = datetime.now(UTC)
    if store is None:
        return _unavailable(now)
    try:
        page = await store.list_page(limit=REPORT_LIMIT, offset=0)
    except PortfolioError:
        return _unavailable(now)
    warnings: list[str] = []
    deployments = await _deployments(execution, warnings)
    digests = tuple(
        [await _digest(store, aggregate, deployments, warnings) for aggregate in page.portfolios]
    )
    if page.total > len(digests):
        warnings.append(f"Showing the first {len(digests)} of {page.total} portfolios.")
    blocked = [
        f"{digest.name}: {sleeve.strategy_name}"
        for digest in digests
        for sleeve in digest.sleeves
        if sleeve.issues
    ]
    component = _component(digests, blocked, total=page.total)
    return PortfoliosReport(
        application_version=__version__,
        generated_at=now,
        overall_status=component.status,
        components=(component,),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action((component,)),
        payload=PortfoliosPayload(
            portfolio_storage="available",
            portfolio_backtest_contract=PORTFOLIO_BACKTEST_CONTRACT,
            total=page.total,
            portfolios=digests,
        ),
    )


def _component(
    digests: tuple[PortfolioDigest, ...], blocked: list[str], *, total: int
) -> ComponentReport:
    """Degraded on a latched portfolio breaker or a blocked sleeve, else healthy."""
    latched = [
        f"{digest.name}: {digest.breaker_reason_code}"
        for digest in digests
        if digest.breaker_latched
    ]
    if latched:
        return ComponentReport(
            name="portfolios",
            status=ReportStatus.DEGRADED,
            reason_code="PORTFOLIO_BREAKER_LATCHED",
            detail=(
                "Portfolio breakers latched (sleeves paused until an operator reset): "
                + "; ".join(latched)
            )[:500],
        )
    if blocked:
        return ComponentReport(
            name="portfolios",
            status=ReportStatus.DEGRADED,
            reason_code="PORTFOLIO_SLEEVE_ISSUES",
            detail=("Sleeves that cannot be backtested or started: " + "; ".join(blocked))[:500],
        )
    deployed = sum(1 for digest in digests if digest.deployment_state not in _IDLE)
    return ComponentReport(
        name="portfolios",
        status=ReportStatus.HEALTHY,
        reason_code="OK",
        detail=f"{total} portfolio(s); {deployed} deployed.",
    )


async def _deployments(
    execution: ExecutionStore | None, warnings: list[str]
) -> tuple[Deployment, ...]:
    """Every deployment (to derive portfolio states); an outage degrades to none."""
    if execution is None:
        return ()
    try:
        return await execution.list_deployments()
    except ExecutionStoreError:
        warnings.append("Deployments are unavailable; portfolio states read as not deployed.")
        return ()


def _unavailable(now: datetime) -> PortfoliosReport:
    """A degraded report when portfolio storage is disabled or unreachable."""
    component = ComponentReport(
        name="portfolios",
        status=ReportStatus.DEGRADED,
        reason_code="PORTFOLIO_STORAGE_UNAVAILABLE",
        detail="Portfolio storage is unavailable (PostgreSQL is required).",
    )
    return PortfoliosReport(
        application_version=__version__,
        generated_at=now,
        overall_status=ReportStatus.DEGRADED,
        components=(component,),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=("Portfolio storage is unavailable; no portfolios are listed.",),
        recommended_next_action=recommend_next_action((component,)),
        payload=PortfoliosPayload(
            portfolio_storage="unavailable",
            portfolio_backtest_contract=PORTFOLIO_BACKTEST_CONTRACT,
            total=0,
        ),
    )


async def _digest(
    store: PortfolioStorage,
    aggregate: PortfolioAggregate,
    deployments: tuple[Deployment, ...],
    warnings: list[str],
) -> PortfolioDigest:
    """Project one portfolio with its deployment, breaker, proposals, and newest backtest."""
    portfolio = aggregate.portfolio
    state = sleeve_books(aggregate, members(deployments, portfolio.portfolio_id)).state
    runtime = PortfolioRuntimeState(portfolio_id=portfolio.portfolio_id)
    pending = 0
    try:
        runtime = await store.runtime_state(portfolio.portfolio_id)
        pending = (
            await store.list_proposals(portfolio.portfolio_id, status="pending", limit=1, offset=0)
        ).total
    except PortfolioError:
        warnings.append(f"Runtime state of {portfolio.name} is unavailable.")
    allocation = allocation_summary(aggregate)
    largest = allocation.largest_asset
    latest: PortfolioBacktestDigest | None = None
    active = 0
    try:
        rows = await store.list_results(portfolio.portfolio_id, limit=1, offset=0)
        jobs = await store.list_jobs(portfolio.portfolio_id, limit=20)
    except PortfolioError:
        warnings.append(f"Backtests of {portfolio.name} are unavailable.")
    else:
        if rows:
            row = rows[0]
            latest = PortfolioBacktestDigest(
                result_fingerprint=row.result_fingerprint,
                published_at=row.published_at,
                evaluation_start=row.evaluation_start,
                evaluation_end=row.evaluation_end,
                total_return_fraction=row.total_return_fraction,
                maximum_drawdown_fraction=row.maximum_drawdown_fraction,
                basket_total_return_fraction=row.basket_total_return_fraction,
            )
        active = sum(1 for job in jobs if job.status in _ACTIVE)
    return PortfolioDigest(
        portfolio_id=portfolio.portfolio_id,
        name=portfolio.name,
        mode=portfolio.mode,
        quote_currency=portfolio.quote_currency,
        capital_quote=portfolio.capital_quote,
        cash_reserve_fraction=portfolio.cash_reserve_fraction,
        allocated_fraction=allocation.allocated_fraction,
        unallocated_fraction=allocation.unallocated_fraction,
        revision=portfolio.revision,
        sleeves=tuple(
            PortfolioSleeveDigest(
                sleeve_id=view.sleeve.sleeve_id,
                strategy_id=view.sleeve.strategy_id,
                strategy_name=view.strategy.name,
                product_id=view.strategy.product_id,
                timeframe=view.strategy.timeframe,
                weight_fraction=view.sleeve.weight_fraction,
                issues=sleeve_issues(view, portfolio.quote_currency),
            )
            for view in aggregate.sleeves
        ),
        largest_asset=None if largest is None else largest.asset,
        largest_asset_weight_fraction=None if largest is None else largest.weight_fraction,
        largest_asset_within_limit=allocation.largest_asset_within_limit,
        limits=portfolio.limits,
        manager=portfolio.manager,
        deployable=bool(aggregate.sleeves)
        and not any(sleeve_issues(view, portfolio.quote_currency) for view in aggregate.sleeves),
        deployment_state=state,
        breaker_latched=runtime.breaker_latched,
        breaker_reason_code=runtime.breaker_reason,
        pending_proposals=pending,
        latest_backtest=latest,
        active_backtest_jobs=active,
    )
