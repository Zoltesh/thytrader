"""The manager briefing: everything a manager agent reasons from, in one read (ADR 0091).

Read-only. One call returns the mandate and permissions (with the rolling weekly
auto-apply budget), the deployment state, live or paper performance, risk state
(breakers, exposure against the caps), each sleeve with its bot, its drawdown against
the newest portfolio backtest's evidence, and its recent per-bar decisions (each with a
``ref`` a proposal can cite), the pending and recent proposals, and the journal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, Field

from thytrader.execution.book_marks import marks_by_deployment
from thytrader.execution.decision_store import DecisionStoreError
from thytrader.portfolios.backtest import portfolio_backtest_fingerprint
from thytrader.portfolios.models import (
    PORTFOLIO_BRIEFING_CONTRACT,
    JournalEntry,
    ManagerSettings,
    PortfolioError,
    PortfolioLimits,
    PortfolioMode,
    utc_text,
)
from thytrader.portfolios.proposals import (
    REBALANCE_BUDGET_WINDOW,
    Proposal,
)
from thytrader.portfolios.runtime_views import (
    PortfolioBreakerResponse,
    PortfolioExposureResponse,
    SleeveBookResponse,
    SleeveDeploymentResponse,
    deployment_response,
)
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.execution.decisions import BarDecision
    from thytrader.portfolios.backtest import PortfolioBacktestResult, PortfolioSleeveResult
    from thytrader.portfolios.runtime import PortfolioDeploymentSnapshot
    from thytrader.portfolios.store import PortfolioStorage

BRIEFING_CONTRACT: Final = PORTFOLIO_BRIEFING_CONTRACT
DEFAULT_DECISIONS_PER_SLEEVE: Final = 5
DEFAULT_JOURNAL_ENTRIES: Final = 20
_RECENT_PROPOSALS = 50
_ZERO = Decimal(0)
_RATIO = Decimal("0.01")

BRIEFING_DISCLOSURES: Final[tuple[str, ...]] = (
    "Performance uses each sleeve bot's last persisted bar-close equity; it lags by up to "
    "one bar of the sleeve's clock.",
    "Backtest evidence is the newest stored portfolio backtest: sleeves simulated "
    "independently on fixed capital slices; portfolio caps and cross-sleeve interactions "
    "are not simulated. Fills are simulated from candles.",
    "The manager never places orders: strategies place every trade. Propose; never trade.",
)


class BriefingDecision(BaseModel):
    """One recent per-bar decision of a sleeve bot (cite ``ref`` as decision evidence)."""

    ref: str = Field(description="<deployment_id>/<product_id>@<bar_starts_at>")
    bar_starts_at: str
    product_id: str
    outcome: str
    reason_code: str
    summary: str


class BriefingEvidence(BaseModel):
    """What the newest portfolio backtest says about one sleeve."""

    result_fingerprint: str = Field(description="The sleeve's child backtest result.")
    total_return_fraction: str
    maximum_drawdown_fraction: str
    win_rate: str
    trade_count: int


class BriefingSleeve(BaseModel):
    """One sleeve, its bot, its backtest evidence, and its recent decisions."""

    sleeve_id: UUID
    strategy_id: UUID
    strategy_name: str
    product_id: str | None
    timeframe: str | None
    weight_fraction: str
    target_capital_quote: str
    issues: tuple[str, ...]
    deployment: SleeveDeploymentResponse | None
    backtest: BriefingEvidence | None
    drawdown_vs_backtest: str | None = Field(
        description="Live/paper drawdown over the backtest's max drawdown (1.5 means 1.5 times)."
    )
    recent_decisions: tuple[BriefingDecision, ...]


class BriefingPerformance(BaseModel):
    """The portfolio's run performance."""

    capital_quote: str
    equity: str
    net_pnl: str
    return_fraction: str
    daily_pnl: str | None
    drawdown_fraction: str | None
    run_started_at: str | None


class BriefingPermissions(BaseModel):
    """What the manager may change without approval, and what is left of its budget."""

    mandate: str
    may_rebalance: bool
    rebalance_auto_applies: bool = Field(
        description="True on paper with may_rebalance; a live rebalance always needs approval."
    )
    max_weight_change_per_week: str
    weight_moved_this_week: str = Field(description="By auto-applied rebalances, trailing 7 days.")
    weight_budget_remaining: str
    may_pause_sleeves: bool
    may_propose_sleeves: bool
    resume_needs_approval: Literal[True] = True
    order_authority: Literal[False] = Field(
        default=False, description="The manager never places orders; strategies do."
    )


class BriefingBacktest(BaseModel):
    """The newest stored portfolio backtest's headline."""

    result_fingerprint: str
    portfolio_revision: int
    evaluation_start: str
    evaluation_end: str
    total_return_fraction: str
    maximum_drawdown_fraction: str
    basket_total_return_fraction: str


class ManagerBriefing(BaseModel):
    """The one-call read a manager agent decides from."""

    contract: Literal["thytrader-portfolio-briefing-v1"] = BRIEFING_CONTRACT
    generated_at: str
    portfolio_id: UUID
    name: str
    mode: PortfolioMode
    quote_currency: str
    revision: int = Field(description="Name this revision when you submit a proposal.")
    capital_quote: str
    cash_reserve_fraction: str
    limits: PortfolioLimits
    manager: ManagerSettings
    permissions: BriefingPermissions
    state: str
    performance: BriefingPerformance
    breaker: PortfolioBreakerResponse
    exposure: PortfolioExposureResponse
    sleeves: tuple[BriefingSleeve, ...]
    latest_backtest: BriefingBacktest | None
    pending_proposals: tuple[Proposal, ...]
    recent_proposals: tuple[Proposal, ...]
    journal: tuple[JournalEntry, ...]
    disclosures: tuple[str, ...] = BRIEFING_DISCLOSURES


async def build_manager_briefing(
    snapshot: PortfolioDeploymentSnapshot,
    *,
    portfolios: PortfolioStorage,
    decisions: DecisionJournalStore,
    decisions_per_sleeve: int = DEFAULT_DECISIONS_PER_SLEEVE,
    journal_entries: int = DEFAULT_JOURNAL_ENTRIES,
    now: datetime | None = None,
) -> ManagerBriefing:
    """Assemble the briefing from one deployment snapshot plus the stored history."""
    moment = now or datetime.now(UTC)
    aggregate = snapshot.aggregate
    portfolio = aggregate.portfolio
    portfolio_id = portfolio.portfolio_id
    proposals = await portfolios.list_proposals(
        portfolio_id, status=None, limit=_RECENT_PROPOSALS, offset=0
    )
    pending = tuple(item for item in proposals.proposals if item.status == "pending")
    marks = await marks_by_deployment(decisions, snapshot.snapshots)
    deployment = deployment_response(snapshot, pending_proposals=len(pending), marks=marks)
    backtest = await _latest_backtest(portfolios, portfolio_id)
    journal = await portfolios.journal(portfolio_id, limit=journal_entries, offset=0)
    sleeves = tuple(
        [
            await _sleeve(item, backtest, decisions, limit=decisions_per_sleeve)
            for item in deployment.sleeves
        ]
    )
    capital = Decimal(portfolio.capital_quote)
    pnl = snapshot.equity - capital
    return ManagerBriefing(
        generated_at=utc_text(moment),
        portfolio_id=portfolio_id,
        name=portfolio.name,
        mode=portfolio.mode,
        quote_currency=portfolio.quote_currency,
        revision=portfolio.revision,
        capital_quote=portfolio.capital_quote,
        cash_reserve_fraction=portfolio.cash_reserve_fraction,
        limits=portfolio.limits,
        manager=portfolio.manager,
        permissions=_permissions(snapshot, proposals.proposals, now=moment),
        state=deployment.state,
        performance=BriefingPerformance(
            capital_quote=portfolio.capital_quote,
            equity=canonical_decimal(snapshot.equity),
            net_pnl=canonical_decimal(pnl),
            return_fraction=canonical_decimal((pnl / capital).quantize(Decimal("0.000001")))
            if capital > 0
            else "0",
            daily_pnl=deployment.breaker.daily_pnl,
            drawdown_fraction=deployment.breaker.drawdown_fraction,
            run_started_at=deployment.breaker.run_started_at,
        ),
        breaker=deployment.breaker,
        exposure=deployment.exposure,
        sleeves=sleeves,
        latest_backtest=None if backtest is None else _backtest_headline(backtest),
        pending_proposals=pending,
        recent_proposals=tuple(item for item in proposals.proposals if item.status != "pending")[
            :10
        ],
        journal=journal.entries,
    )


def _permissions(
    snapshot: PortfolioDeploymentSnapshot, proposals: Sequence[Proposal], *, now: datetime
) -> BriefingPermissions:
    """The manager's permissions with the rolling auto-apply budget."""
    portfolio = snapshot.aggregate.portfolio
    permissions = portfolio.manager.permissions
    since = now - REBALANCE_BUDGET_WINDOW
    moved = sum(
        (
            Decimal(item.weight_moved)
            for item in proposals
            if item.kind == "rebalance"
            and item.auto_applied
            and item.status == "applied"
            and item.weight_moved is not None
            and item.decided_at is not None
            and item.decided_at >= since
        ),
        _ZERO,
    )
    budget = Decimal(permissions.max_weight_change_per_week)
    return BriefingPermissions(
        mandate=portfolio.manager.mandate,
        may_rebalance=permissions.may_rebalance,
        rebalance_auto_applies=permissions.may_rebalance and portfolio.mode == "paper",
        max_weight_change_per_week=permissions.max_weight_change_per_week,
        weight_moved_this_week=canonical_decimal(moved),
        weight_budget_remaining=canonical_decimal(max(_ZERO, budget - moved)),
        may_pause_sleeves=permissions.may_pause_sleeves,
        may_propose_sleeves=permissions.may_propose_sleeves,
    )


async def _latest_backtest(
    portfolios: PortfolioStorage, portfolio_id: UUID
) -> PortfolioBacktestResult | None:
    """The newest stored portfolio backtest, if any (a storage hiccup reads as none)."""
    try:
        listings = await portfolios.list_results(portfolio_id, limit=1, offset=0)
        if not listings:
            return None
        return await portfolios.load_result(portfolio_id, listings[0].result_fingerprint)
    except PortfolioError:
        return None


def _backtest_headline(result: PortfolioBacktestResult) -> BriefingBacktest:
    """The newest backtest's headline numbers."""
    return BriefingBacktest(
        result_fingerprint=portfolio_backtest_fingerprint(result),
        portfolio_revision=result.portfolio_revision,
        evaluation_start=utc_text(result.evaluation_start),
        evaluation_end=utc_text(result.evaluation_end),
        total_return_fraction=result.summary.total_return_fraction,
        maximum_drawdown_fraction=result.summary.maximum_drawdown_fraction,
        basket_total_return_fraction=result.basket.total_return_fraction,
    )


async def _sleeve(
    book: SleeveBookResponse,
    backtest: PortfolioBacktestResult | None,
    decisions: DecisionJournalStore,
    *,
    limit: int,
) -> BriefingSleeve:
    """One briefing sleeve: its bot, its backtest evidence, and its recent decisions."""
    evidence = _evidence(backtest, book)
    recent: tuple[BriefingDecision, ...] = ()
    ratio: str | None = None
    if book.deployment is not None:
        recent = await _recent_decisions(decisions, book.deployment.deployment_id, limit=limit)
        drawdown = book.deployment.drawdown_fraction
        if evidence is not None and drawdown is not None:
            simulated = Decimal(evidence.maximum_drawdown_fraction)
            if simulated > 0:
                ratio = canonical_decimal((Decimal(drawdown) / simulated).quantize(_RATIO))
    return BriefingSleeve(
        sleeve_id=book.sleeve_id,
        strategy_id=book.strategy_id,
        strategy_name=book.strategy_name,
        product_id=book.product_id,
        timeframe=book.timeframe,
        weight_fraction=book.weight_fraction,
        target_capital_quote=book.target_capital_quote,
        issues=book.issues,
        deployment=book.deployment,
        backtest=evidence,
        drawdown_vs_backtest=ratio,
        recent_decisions=recent,
    )


def _evidence(
    backtest: PortfolioBacktestResult | None, book: SleeveBookResponse
) -> BriefingEvidence | None:
    """The newest portfolio backtest's numbers for this sleeve (same sleeve and strategy)."""
    if backtest is None:
        return None
    match: PortfolioSleeveResult | None = next(
        (
            item
            for item in backtest.sleeves
            if item.sleeve_id == book.sleeve_id and item.strategy_id == book.strategy_id
        ),
        None,
    )
    if match is None:
        return None
    return BriefingEvidence(
        result_fingerprint=match.result_fingerprint,
        total_return_fraction=match.total_return_fraction,
        maximum_drawdown_fraction=match.maximum_drawdown_fraction,
        win_rate=match.win_rate,
        trade_count=match.trade_count,
    )


async def _recent_decisions(
    decisions: DecisionJournalStore, deployment_id: UUID, *, limit: int
) -> tuple[BriefingDecision, ...]:
    """The bot's newest per-bar decisions (a journal outage reads as none)."""
    try:
        page = await decisions.list_for_deployment(deployment_id, limit=limit)
    except DecisionStoreError:
        return ()
    return tuple(_decision(item) for item in page.decisions)


def _decision(decision: BarDecision) -> BriefingDecision:
    """One decision with the reference a proposal cites."""
    bar = utc_text(decision.bar_starts_at.replace(microsecond=0))
    return BriefingDecision(
        ref=f"{decision.deployment_id}/{decision.product_id}@{bar}",
        bar_starts_at=bar,
        product_id=decision.product_id,
        outcome=decision.outcome.value,
        reason_code=decision.reason_code,
        summary=decision.summary,
    )
