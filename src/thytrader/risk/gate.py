"""Pre-trade risk checks for deployments and entries.

The gate admits new deployments and risk-increasing entries and composes the policy's
checks in a fixed order: membership and slots (``entry_limits``), a sleeve's portfolio
limits (``portfolio_limits``), optional per-order bounds (``order_bounds``), account
exposure, the optional BTC-beta-weighted exposure cap (``beta_exposure``, ADR 0125),
unresolved accounting, then the circuit breakers, rate, and collar gates and the
optional fleet entry clustering cap (``entry_clustering``, ADR 0125). Before order bounds,
live USD/USDC entries pass the shared-collateral gate (``futures_collateral``, ADR 0129):
manual CFM futures in use or unknown deny, unless a declared reserve covers them, which is
then withheld from the venue quote every later check uses. It gates entries only,
never protective exits. An in-kind adoption (ADR 0124) sends nothing to the venue, so it
skips the order bounds, rate, collar and clustering gates and adds its notional to live
capital; every other check applies. A reprice of an admitted working entry skips only the
clustering cap.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.market_data.products import is_spot_product_id, quote_currency
from thytrader.risk.beta_exposure import beta_verdict
from thytrader.risk.breakers import (
    EntryObservation,
    evaluate_circuit_breakers,
    evaluate_rate_and_collar,
    quote_scoped_snapshots,
    unresolved_accounting_verdict,
)
from thytrader.risk.entry_clustering import cluster_verdict
from thytrader.risk.entry_limits import (
    _allocation_for,
    _allocation_membership,
    _allowlist_verdict,
    _capital_base,
    _entry_membership,
    _exposure_verdict,
    _occupied,
)
from thytrader.risk.futures_collateral import collateral_verdict
from thytrader.risk.gate_common import ProposedEntry, _allow, _deny
from thytrader.risk.models import (
    RiskDecision,
    RiskPolicyDefinition,
    RiskPolicySource,
    RiskReasonCode,
    RiskVerdict,
)
from thytrader.risk.order_bounds import _order_bound_verdict
from thytrader.risk.portfolio_limits import PortfolioRiskBook, evaluate_portfolio_entry
from thytrader.trading.exposure import risk_bearing_snapshots
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import Deployment, DeploymentMode, DeploymentSnapshot

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.risk.beta import BetaEvidence
    from thytrader.risk.futures_collateral import FuturesCollateralEvidence


def evaluate_new_deployment(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    product_id: str,
    strategy_id: UUID | None,
    paper_starting_cash: Decimal | None,
    deployments: Sequence[Deployment],
    product_ids: Sequence[str] | None = None,
    policy_source: RiskPolicySource = RiskPolicySource.PUBLISHED,
    portfolio_sleeve: bool = False,
) -> RiskVerdict:
    """Allow a new running deployment only when slots, allowlist, and paper capital permit it.

    Live deployments additionally require an operator-published policy: a fresh
    install's compiled fallback must not become silent live authority (audit F25).
    ``portfolio_sleeve`` marks a sleeve of a live portfolio: its sleeve allocation
    counts as allocation membership (ADR 0091); every other rule still applies.
    """
    if mode is DeploymentMode.LIVE and policy_source is RiskPolicySource.COMPILED_DEFAULT:
        return _deny(
            RiskReasonCode.LIVE_REQUIRES_PUBLISHED_POLICY,
            "Live trading requires an operator-published risk policy; the compiled "
            "default cannot arm live orders.",
        )
    occupied = _occupied(deployments, mode)
    covered = tuple(product_ids) if product_ids else (product_id,)
    for covered_product in covered:
        allowlisted = _allowlist_verdict(policy, covered_product)
        if allowlisted.decision is RiskDecision.DENY:
            return allowlisted
    allocated = _allocation_membership(
        policy, strategy_id, mode=mode, portfolio_member=portfolio_sleeve
    )
    if allocated.decision is RiskDecision.DENY:
        return allocated
    if len(occupied) >= policy.max_concurrent_running_deployments:
        return _deny(
            RiskReasonCode.MAX_RUNNING_DEPLOYMENTS,
            "Occupied deployments already use every running slot for this mode.",
        )
    if mode is DeploymentMode.PAPER:
        return _paper_deploy_capital(
            policy, occupied, strategy_id, paper_starting_cash, product_id=product_id
        )
    return _allow()


def evaluate_new_entry(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None = None,
    observation: EntryObservation | None = None,
    portfolio: PortfolioRiskBook | None = None,
    beta: BetaEvidence | None = None,
    futures_collateral: FuturesCollateralEvidence | None = None,
) -> RiskVerdict:
    """Allow a risk-increasing entry only when slots, exposure, and breakers permit it.

    A sleeve of a deployed portfolio passes its portfolio's limits (``portfolio``) after
    the policy's membership checks and before the account-wide exposure and breaker
    checks; every check must pass, so the strictest limit wins (ADR 0091). ``beta`` is
    the BTC-beta evidence for the β cap (ADR 0125); it is ignored while no β cap binds
    in ``mode`` and denies as unavailable when a cap binds and it is missing.
    ``futures_collateral`` is the classified CFM account (ADR 0129); ``None`` means no
    evidence was requested (paper) and changes nothing.
    """
    occupied = tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode and occupies_running_slot(item.deployment)
    )
    risk_bearing = risk_bearing_snapshots(snapshots, mode)
    quote_books, incomplete = quote_scoped_snapshots(risk_bearing, proposed.product_id)
    if incomplete is not None:
        return incomplete
    membership = _entry_membership(
        policy,
        mode=mode,
        proposed=proposed,
        occupied=occupied,
        portfolio_member=portfolio is not None and portfolio.live,
    )
    if membership.decision is RiskDecision.DENY:
        return membership
    limited = _portfolio_verdict(
        portfolio, proposed=proposed, risk_bearing=risk_bearing, quote_books=quote_books
    )
    if limited is not None:
        return limited
    collateral, live_quote_cash = collateral_verdict(
        policy,
        mode=mode,
        proposed=proposed,
        evidence=futures_collateral,
        live_quote_cash=live_quote_cash,
    )
    if collateral is not None:
        return collateral
    bounded = (
        None
        if proposed.in_kind
        else _order_bound_verdict(
            policy,
            mode=mode,
            proposed=proposed,
            occupied=quote_books,
            live_quote_cash=live_quote_cash,
        )
    )
    if bounded is not None:
        return bounded
    exposure = _exposure_verdict(
        policy,
        mode=mode,
        proposed=proposed,
        occupied=quote_books,
        live_quote_cash=live_quote_cash,
    )
    if exposure.decision is RiskDecision.DENY:
        return exposure
    beta_capped = beta_verdict(
        policy,
        mode=mode,
        proposed=proposed,
        occupied=quote_books,
        live_quote_cash=live_quote_cash,
        beta=beta,
        as_of=None if observation is None else observation.as_of,
    )
    if beta_capped is not None:
        return beta_capped
    unresolved = unresolved_accounting_verdict(
        mode=mode, product_id=proposed.product_id, snapshots=snapshots
    )
    if unresolved is not None:
        return unresolved
    return _entry_breaker_verdict(
        policy,
        mode=mode,
        proposed=proposed,
        risk_bearing=risk_bearing,
        quote_books=quote_books,
        snapshots=snapshots,
        live_quote_cash=live_quote_cash,
        observation=observation,
    )


def _portfolio_verdict(
    portfolio: PortfolioRiskBook | None,
    *,
    proposed: ProposedEntry,
    risk_bearing: Sequence[DeploymentSnapshot],
    quote_books: Sequence[DeploymentSnapshot],
) -> RiskVerdict | None:
    """Apply a sleeve's portfolio limits; refuse portfolios that mix quote currencies."""
    if portfolio is None:
        return None
    if any(
        item.deployment.portfolio_id == portfolio.portfolio_id
        for item in risk_bearing
        if item not in quote_books
    ):
        return _deny(
            RiskReasonCode.PORTFOLIO_LIMITS_UNAVAILABLE,
            "Portfolio exposure cannot combine different quote currencies.",
        )
    limited = evaluate_portfolio_entry(portfolio, proposed=proposed, snapshots=quote_books)
    return limited if limited.decision is RiskDecision.DENY else None


def evaluate_runtime_breakers(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    snapshot: DeploymentSnapshot,
    snapshots: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
    observation: EntryObservation,
) -> RiskVerdict:
    """Pause-worthy daily-loss and drawdown checks without rate or collar gates.

    Capital stays on risk-bearing books. Loss evidence includes stopped flat rows present
    in ``snapshots``; this function does not drop them before the breaker.
    """
    occupied, incomplete = quote_scoped_snapshots(
        risk_bearing_snapshots(snapshots, mode), snapshot.deployment.product_id
    )
    if incomplete is not None:
        return incomplete
    capital = _capital_base(policy, mode=mode, live_quote_cash=live_quote_cash, occupied=occupied)
    tripped = evaluate_circuit_breakers(
        policy,
        mode=mode,
        proposed_product_id=snapshot.deployment.product_id,
        proposed_strategy_id=snapshot.deployment.strategy_id,
        snapshots=snapshots,
        observation=observation,
        capital=capital,
    )
    if tripped is None:
        return _allow()
    return tripped


def _entry_breaker_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    risk_bearing: Sequence[DeploymentSnapshot],
    quote_books: Sequence[DeploymentSnapshot],
    snapshots: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
    observation: EntryObservation | None,
) -> RiskVerdict:
    """Apply loss, drawdown, rate, collar, and fleet clustering gates.

    Rate limits stay on risk-bearing books. Circuit breakers and the clustering cap see the
    full snapshot list so a stopped book's loss, latch, and recent entries are not filtered
    out first. An in-kind entry keeps the breakers (on capital including its notional) but
    skips rate, collar, and clustering. Without an observation only the clustering cap is
    evaluated, and a set cap denies because its window has no anchor.
    """
    if observation is None:
        return _clustered_or_allow(
            policy, mode=mode, proposed=proposed, snapshots=snapshots, observation=None
        )
    capital = _capital_base(
        policy,
        mode=mode,
        live_quote_cash=live_quote_cash,
        occupied=quote_books,
        in_kind_notional=proposed.in_kind_capital,
    )
    tripped = evaluate_circuit_breakers(
        policy,
        mode=mode,
        proposed_product_id=proposed.product_id,
        proposed_strategy_id=proposed.strategy_id,
        snapshots=snapshots,
        observation=observation,
        capital=capital,
    )
    if tripped is not None:
        return tripped
    if proposed.in_kind:
        return _allow()
    protected = evaluate_rate_and_collar(
        policy, mode=mode, snapshots=risk_bearing, observation=observation
    )
    if protected is not None:
        return protected
    return _clustered_or_allow(
        policy, mode=mode, proposed=proposed, snapshots=snapshots, observation=observation
    )


def _clustered_or_allow(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None,
) -> RiskVerdict:
    """Return the fleet clustering denial, or allow when the cap does not object."""
    clustered = cluster_verdict(
        policy, mode=mode, proposed=proposed, snapshots=snapshots, observation=observation
    )
    return _allow() if clustered is None else clustered


def _paper_deploy_capital(
    policy: RiskPolicyDefinition,
    occupied: tuple[Deployment, ...],
    strategy_id: UUID | None,
    paper_starting_cash: Decimal | None,
    *,
    product_id: str,
) -> RiskVerdict:
    """Cap single-quote paper starting cash and the optional per-strategy allocation."""
    if not is_spot_product_id(product_id) or any(
        not is_spot_product_id(item.product_id)
        or quote_currency(item.product_id) != quote_currency(product_id)
        for item in occupied
    ):
        return _deny(
            RiskReasonCode.PAPER_CAPITAL_EXCEEDED,
            "Paper capital cannot combine unsupported or different quote currencies.",
        )
    if paper_starting_cash is None or paper_starting_cash <= 0:
        return _deny(
            RiskReasonCode.PAPER_CAPITAL_EXCEEDED,
            "Paper deployments require positive starting cash under the risk policy.",
        )
    committed = _paper_committed(occupied)
    book = Decimal(policy.paper_capital_quote)
    if committed + paper_starting_cash > book:
        return _deny(
            RiskReasonCode.PAPER_CAPITAL_EXCEEDED,
            "Paper starting cash would exceed paper_capital_quote.",
        )
    reserved = _allocation_for(policy, strategy_id)
    if reserved is None:
        return _allow()
    if paper_starting_cash > reserved:
        return _deny(
            RiskReasonCode.ALLOCATION_EXCEEDED,
            "Paper starting cash exceeds the allocation for this strategy.",
        )
    return _allow()


def _paper_committed(occupied: Sequence[Deployment]) -> Decimal:
    """Sum paper starting cash already reserved by occupied deployments."""
    total = Decimal("0")
    for item in occupied:
        if item.paper_starting_cash is not None:
            total += item.paper_starting_cash
    return total
