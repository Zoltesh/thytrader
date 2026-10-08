"""Planning a portfolio start: fee assumptions, sleeve capital, and admission stand-ins.

Paper fee assumptions are validated once for every sleeve and omitted rates come from the
account (unknown account rates refuse the start). Each planned sleeve's capital is exactly
weight times the portfolio's capital, and a hypothetical book stands in for it while the
risk policy admits the sleeves after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.paper_fees import PaperFeesUnavailableError, paper_fee_rates
from thytrader.portfolios.errors import (
    PortfolioConflictError,
    PortfolioValidationError,
    StartProblem,
)
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.ledger import resolve_paper_fee_schedule
from thytrader.trading.models import Deployment, DeploymentMode, DeploymentStatus, RuntimePhase

if TYPE_CHECKING:
    from thytrader.execution.paper_fees import PaperFeeSource
    from thytrader.portfolios.models import PortfolioAggregate, SleeveView
    from thytrader.strategies.snapshots import StrategySnapshot


@dataclass(frozen=True, slots=True)
class FeeAssumptions:
    """Optional paper maker/taker rates applied to every paper sleeve."""

    maker_fee_rate: Decimal | None = None
    taker_fee_rate: Decimal | None = None


@dataclass(frozen=True, slots=True)
class _PlannedSleeve:
    """One sleeve the start will create a book for."""

    view: SleeveView
    capital: Decimal
    snapshot: StrategySnapshot


def _fee_rates(mode: DeploymentMode, fees: FeeAssumptions) -> tuple[Decimal | None, Decimal | None]:
    """Validate paper fee assumptions once for every sleeve (live takes none)."""
    try:
        resolve_paper_fee_schedule(
            live=mode is DeploymentMode.LIVE,
            maker_fee_rate=fees.maker_fee_rate,
            taker_fee_rate=fees.taker_fee_rate,
        )
    except ValueError as error:
        raise PortfolioValidationError("portfolio_fee_rates_invalid", str(error)) from None
    return fees.maker_fee_rate, fees.taker_fee_rate


async def _account_fee_rates(
    fees: tuple[Decimal | None, Decimal | None], source: PaperFeeSource | None
) -> tuple[Decimal | None, Decimal | None]:
    """Fill omitted paper rates from the account; unknown account rates refuse the start."""
    try:
        return await paper_fee_rates(maker_fee_rate=fees[0], taker_fee_rate=fees[1], source=source)
    except PaperFeesUnavailableError as error:
        raise PortfolioConflictError("paper_fees_unavailable", str(error)) from None


def _sleeve_capital(aggregate: PortfolioAggregate, view: SleeveView) -> Decimal:
    """Exact sleeve capital: weight times the portfolio's capital."""
    return Decimal(aggregate.portfolio.capital_quote) * Decimal(view.sleeve.weight_fraction)


def _hypothetical(item: _PlannedSleeve, mode: DeploymentMode) -> Deployment:
    """A planned book standing in for admission checks of the sleeves after it."""
    now = utc_now()
    definition = item.snapshot.definition
    return Deployment(
        id=uuid7(now),
        strategy_fingerprint=item.snapshot.strategy_fingerprint,
        strategy_id=definition.strategy_id,
        product_id=definition.instrument.product_id,
        mode=mode,
        status=DeploymentStatus.RUNNING,
        cash=item.capital if mode is DeploymentMode.PAPER else Decimal(0),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        paper_starting_cash=item.capital if mode is DeploymentMode.PAPER else None,
    )


def _problem(view: SleeveView, code: str, message: str) -> StartProblem:
    """One sleeve problem naming the sleeve and its strategy."""
    return StartProblem(
        code=code,
        message=message,
        sleeve_id=view.sleeve.sleeve_id,
        strategy_id=view.sleeve.strategy_id,
        strategy_name=view.strategy.name,
    )
