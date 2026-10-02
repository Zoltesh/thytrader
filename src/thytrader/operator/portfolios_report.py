"""Operator ``portfolios`` report: composition, allocation, limits, manager settings (ADR 0088).

Read-only. Lists every portfolio (bounded) with its sleeves and their issues, the
allocation and largest single asset against ``max_per_asset_fraction``, the stored limits
and manager settings, the newest stored portfolio backtest, and queued/running backtest
jobs. ``deployable`` is always false: portfolio deployment is not shipped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader import __version__
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
from thytrader.portfolios.models import (
    PORTFOLIO_BACKTEST_CONTRACT,
    PortfolioError,
    sleeve_issues,
)
from thytrader.portfolios.rules import allocation_summary
from thytrader.research.jobs import ResearchJobStatus

if TYPE_CHECKING:
    from thytrader.portfolios.models import PortfolioAggregate
    from thytrader.portfolios.store import PortfolioStorage

REPORT_LIMIT = 100
_ACTIVE = (ResearchJobStatus.QUEUED, ResearchJobStatus.RUNNING)


async def build_portfolios_report(store: PortfolioStorage | None) -> PortfoliosReport:
    """Build the report; storage outages degrade it instead of failing the CLI."""
    now = datetime.now(UTC)
    if store is None:
        return _unavailable(now)
    try:
        page = await store.list_page(limit=REPORT_LIMIT, offset=0)
    except PortfolioError:
        return _unavailable(now)
    warnings: list[str] = []
    digests = tuple([await _digest(store, aggregate, warnings) for aggregate in page.portfolios])
    if page.total > len(digests):
        warnings.append(f"Showing the first {len(digests)} of {page.total} portfolios.")
    blocked = [
        f"{digest.name}: {sleeve.strategy_name}"
        for digest in digests
        for sleeve in digest.sleeves
        if sleeve.issues
    ]
    component = (
        ComponentReport(
            name="portfolios",
            status=ReportStatus.DEGRADED,
            reason_code="PORTFOLIO_SLEEVE_ISSUES",
            detail=("Sleeves that cannot be backtested: " + "; ".join(blocked))[:500],
        )
        if blocked
        else ComponentReport(
            name="portfolios",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=f"{page.total} portfolio(s); deployment is not available yet.",
        )
    )
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
    store: PortfolioStorage, aggregate: PortfolioAggregate, warnings: list[str]
) -> PortfolioDigest:
    """Project one portfolio with its newest backtest and active job count."""
    portfolio = aggregate.portfolio
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
        latest_backtest=latest,
        active_backtest_jobs=active,
    )
