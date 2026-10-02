"""Pre-trade risk checks for deployments and entries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.lifecycle import occupies_running_slot
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.market_data.products import base_currency, is_spot_product_id
from thytrader.risk.breakers import (
    EntryObservation,
    evaluate_circuit_breakers,
    evaluate_rate_and_collar,
)
from thytrader.risk.exposure import (
    product_exposure,
    risk_bearing_snapshots,
    working_entry_notional,
)
from thytrader.risk.models import (
    RiskDecision,
    RiskPolicyDefinition,
    RiskPolicySource,
    RiskReasonCode,
    RiskVerdict,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_ENTRY, RuntimePhase.PENDING_EXIT}


@dataclass(frozen=True, slots=True)
class ProposedEntry:
    """One sized entry the runtime wants to rest after a matched closed bar."""

    product_id: str
    strategy_id: UUID | None
    notional: Decimal
    is_pyramid_add: bool = False


@dataclass(frozen=True, slots=True)
class PortfolioRiskBook:
    """One deployed portfolio's shared limits as the entry gate applies them (ADR 0091).

    Bound by the execution worker for every deployment tagged with a ``portfolio_id``.
    Exposure caps are fractions of the portfolio's ``capital`` (its configured
    ``capital_quote``) and count every risk-bearing deployment of the same portfolio.
    ``breaker_reason`` names a latched portfolio breaker (entries stay blocked until an
    operator reset). ``available=False`` means the worker could not load the portfolio's
    limits, so its sleeves fail closed. ``live`` marks a live portfolio, whose sleeve
    allocations count as risk-policy allocation membership for its own deployments.
    """

    portfolio_id: UUID
    capital: Decimal
    max_total_exposure_fraction: Decimal
    max_per_asset_fraction: Decimal
    live: bool = False
    breaker_reason: RiskReasonCode | None = None
    available: bool = True

    @classmethod
    def unavailable(cls, portfolio_id: UUID) -> PortfolioRiskBook:
        """A fail-closed book for a sleeve whose portfolio limits could not be read."""
        return cls(
            portfolio_id=portfolio_id,
            capital=Decimal("0"),
            max_total_exposure_fraction=Decimal("0"),
            max_per_asset_fraction=Decimal("0"),
            available=False,
        )


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
        return _paper_deploy_capital(policy, occupied, strategy_id, paper_starting_cash)
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
) -> RiskVerdict:
    """Allow a risk-increasing entry only when slots, exposure, and breakers permit it.

    A sleeve of a deployed portfolio passes its portfolio's limits (``portfolio``) after
    the policy's membership checks and before the account-wide exposure and breaker
    checks; every check must pass, so the strictest limit wins (ADR 0091).
    """
    occupied = tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode and occupies_running_slot(item.deployment)
    )
    risk_bearing = risk_bearing_snapshots(snapshots, mode)
    membership = _entry_membership(
        policy,
        mode=mode,
        proposed=proposed,
        occupied=occupied,
        portfolio_member=portfolio is not None and portfolio.live,
    )
    if membership.decision is RiskDecision.DENY:
        return membership
    if portfolio is not None:
        limited = evaluate_portfolio_entry(portfolio, proposed=proposed, snapshots=risk_bearing)
        if limited.decision is RiskDecision.DENY:
            return limited
    exposure = _exposure_verdict(
        policy,
        mode=mode,
        proposed=proposed,
        occupied=risk_bearing,
        live_quote_cash=live_quote_cash,
    )
    if exposure.decision is RiskDecision.DENY:
        return exposure
    return _entry_breaker_verdict(
        policy,
        mode=mode,
        proposed=proposed,
        occupied=risk_bearing,
        live_quote_cash=live_quote_cash,
        observation=observation,
    )


def evaluate_runtime_breakers(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    snapshot: DeploymentSnapshot,
    snapshots: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
    observation: EntryObservation,
) -> RiskVerdict:
    """Pause-worthy daily-loss and drawdown checks without rate or collar gates."""
    capital = _capital_base(policy, mode=mode, live_quote_cash=live_quote_cash)
    tripped = evaluate_circuit_breakers(
        policy,
        mode=mode,
        proposed_product_id=snapshot.deployment.product_id,
        proposed_strategy_id=snapshot.deployment.strategy_id,
        snapshots=risk_bearing_snapshots(snapshots, mode),
        observation=observation,
        capital=capital,
    )
    if tripped is None:
        return _allow()
    return tripped


def evaluate_portfolio_entry(
    book: PortfolioRiskBook,
    *,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
) -> RiskVerdict:
    """Apply one portfolio's latched breaker and exposure caps to a sleeve's entry.

    ``snapshots`` are the mode's risk-bearing books; only deployments tagged with this
    portfolio count. Total exposure is every sleeve's position value at entry price plus
    working entry remainders; per-asset exposure sums every product of the proposed
    base asset.
    """
    if not book.available:
        return _deny(
            RiskReasonCode.PORTFOLIO_LIMITS_UNAVAILABLE,
            "Portfolio limits could not be loaded; new entries for its sleeves are blocked.",
        )
    if book.breaker_reason is not None:
        return _deny(
            RiskReasonCode.PORTFOLIO_BREAKER_LATCHED,
            f"Portfolio breaker {book.breaker_reason.value} is latched until an operator "
            "resets it.",
        )
    exposure = portfolio_exposure(book.portfolio_id, snapshots)
    total_cap = book.capital * book.max_total_exposure_fraction
    if exposure.total + proposed.notional > total_cap:
        return _deny(
            RiskReasonCode.PORTFOLIO_TOTAL_EXPOSURE_LIMIT,
            f"Entry of {_quote(proposed.notional)} would take the portfolio's exposure from "
            f"{_quote(exposure.total)} above its cap of {_quote(total_cap)} "
            "(max_total_exposure_fraction times capital).",
        )
    asset = asset_of(proposed.product_id)
    held = exposure.assets.get(asset, Decimal("0"))
    asset_cap = book.capital * book.max_per_asset_fraction
    if held + proposed.notional > asset_cap:
        return _deny(
            RiskReasonCode.PORTFOLIO_ASSET_EXPOSURE_LIMIT,
            f"Entry of {_quote(proposed.notional)} would take the portfolio's {asset} "
            f"exposure from {_quote(held)} above its cap of {_quote(asset_cap)} "
            "(max_per_asset_fraction times capital).",
        )
    return _allow()


@dataclass(frozen=True, slots=True)
class PortfolioExposure:
    """One portfolio's exposure as the entry gate counts it: total and per base asset."""

    total: Decimal
    assets: Mapping[str, Decimal]


def portfolio_exposure(
    portfolio_id: UUID, snapshots: Sequence[DeploymentSnapshot]
) -> PortfolioExposure:
    """Position cost plus working entries of every book tagged with this portfolio."""
    members = tuple(item for item in snapshots if item.deployment.portfolio_id == portfolio_id)
    total = sum((_marked_exposure(item) for item in members), Decimal("0"))
    assets: dict[str, Decimal] = {}
    for item in members:
        for asset in sorted({asset_of(product) for product in _book_products(item)}):
            assets[asset] = assets.get(asset, Decimal("0")) + _asset_exposure(item, asset)
    return PortfolioExposure(total=total, assets=assets)


def asset_of(product_id: str) -> str:
    """Base asset of one spot product (the whole id when it is not a spot pair)."""
    return base_currency(product_id) if is_spot_product_id(product_id) else product_id


def _book_products(snapshot: DeploymentSnapshot) -> set[str]:
    """Every product a book holds, works, or runs (its primary product included)."""
    products = {snapshot.deployment.product_id}
    products.update(
        resolved_product_id(position.product_id, snapshot.deployment)
        for position in snapshot_positions(snapshot)
    )
    products.update(runtime.product_id for runtime in snapshot.instrument_runtimes)
    products.update(
        resolved_product_id(order.product_id, snapshot.deployment) for order in snapshot.orders
    )
    return {product for product in products if product}


def _asset_exposure(snapshot: DeploymentSnapshot, asset: str) -> Decimal:
    """Position cost plus working entry remainders on every product of one base asset."""
    return sum(
        (
            product_exposure(snapshot, product)
            for product in sorted(_book_products(snapshot))
            if asset_of(product) == asset
        ),
        Decimal("0"),
    )


def _quote(amount: Decimal) -> str:
    """Render a quote amount for a verdict detail (two decimals, no exponent)."""
    return f"{amount.quantize(Decimal('0.01')):f}"


def _entry_membership(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
    portfolio_member: bool = False,
) -> RiskVerdict:
    """Apply allowlist, allocation membership, and open-position slot caps."""
    allowlisted = _allowlist_verdict(policy, proposed.product_id)
    if allowlisted.decision is RiskDecision.DENY:
        return allowlisted
    allocated = _allocation_membership(
        policy, proposed.strategy_id, mode=mode, portfolio_member=portfolio_member
    )
    if allocated.decision is RiskDecision.DENY:
        return allocated
    if proposed.is_pyramid_add and not policy.allow_intra_strategy_pyramiding:
        return _deny(
            RiskReasonCode.PYRAMIDING_NOT_ALLOWED,
            "Intra-strategy pyramiding is disabled on the active risk policy.",
        )
    if not proposed.is_pyramid_add:
        open_count = sum(open_position_slot_count(item) for item in occupied)
        if open_count >= policy.max_concurrent_open_positions:
            return _deny(
                RiskReasonCode.MAX_OPEN_POSITIONS,
                "Open and pending positions already use every concurrent slot for this mode.",
            )
    return _allow()


def _entry_breaker_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
    observation: EntryObservation | None,
) -> RiskVerdict:
    """Apply daily-loss, drawdown, rate, and collar gates when observation is present."""
    if observation is None:
        return _allow()
    capital = _capital_base(policy, mode=mode, live_quote_cash=live_quote_cash)
    tripped = evaluate_circuit_breakers(
        policy,
        mode=mode,
        proposed_product_id=proposed.product_id,
        proposed_strategy_id=proposed.strategy_id,
        snapshots=occupied,
        observation=observation,
        capital=capital,
    )
    if tripped is not None:
        return tripped
    protected = evaluate_rate_and_collar(
        policy, mode=mode, snapshots=occupied, observation=observation
    )
    if protected is not None:
        return protected
    return _allow()


def open_position_slot_count(snapshot: DeploymentSnapshot) -> int:
    """Count distinct in-market product books on one deployment."""
    products: set[str] = set()
    for position in snapshot_positions(snapshot):
        products.add(resolved_product_id(position.product_id, snapshot.deployment))
    if snapshot.instrument_runtimes:
        for runtime in snapshot.instrument_runtimes:
            if runtime.phase in _IN_MARKET:
                products.add(runtime.product_id)
        return len(products)
    if snapshot.position is not None or snapshot.deployment.phase in _IN_MARKET:
        products.add(snapshot.deployment.product_id)
    return len(products)


def _paper_deploy_capital(
    policy: RiskPolicyDefinition,
    occupied: tuple[Deployment, ...],
    strategy_id: UUID | None,
    paper_starting_cash: Decimal | None,
) -> RiskVerdict:
    """Cap paper starting cash against the book and an optional per-strategy allocation."""
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


def _exposure_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
) -> RiskVerdict:
    """Compare proposed plus existing marked exposure to portfolio and product caps."""
    existing_total = sum((_marked_exposure(item) for item in occupied), Decimal("0"))
    existing_product = sum(
        (product_exposure(item, proposed.product_id) for item in occupied),
        Decimal("0"),
    )
    capital = _capital_base(policy, mode=mode, live_quote_cash=live_quote_cash)
    if capital <= 0:
        return _deny(
            RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED,
            "Capital base is missing or non-positive; new entries are blocked.",
        )
    portfolio_cap = capital * Decimal(policy.max_portfolio_exposure_fraction)
    # The absolute quote ceiling protects real money; paper is bounded by paper capital.
    if policy.max_portfolio_exposure_quote is not None and mode is DeploymentMode.LIVE:
        portfolio_cap = min(portfolio_cap, Decimal(policy.max_portfolio_exposure_quote))
    if existing_total + proposed.notional > portfolio_cap:
        return _deny(
            RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED,
            "Proposed entry would exceed max_portfolio_exposure_fraction.",
        )
    product_cap = capital * Decimal(policy.per_product_max_exposure_fraction)
    if existing_product + proposed.notional > product_cap:
        return _deny(
            RiskReasonCode.PRODUCT_EXPOSURE_EXCEEDED,
            "Proposed entry would exceed per_product_max_exposure_fraction.",
        )
    reserved = _allocation_for(policy, proposed.strategy_id)
    if reserved is None:
        return _allow()
    strategy_exposure = sum(
        (
            _marked_exposure(item)
            for item in occupied
            if item.deployment.strategy_id == proposed.strategy_id
        ),
        Decimal("0"),
    )
    if strategy_exposure + proposed.notional > reserved:
        return _deny(
            RiskReasonCode.ALLOCATION_EXCEEDED,
            "Proposed entry would exceed the allocation for this strategy.",
        )
    return _allow()


def _allowlist_verdict(policy: RiskPolicyDefinition, product_id: str) -> RiskVerdict:
    """Deny products absent from a nonempty allowlist."""
    if not policy.product_allowlist or product_id in policy.product_allowlist:
        return _allow()
    return _deny(
        RiskReasonCode.PRODUCT_NOT_ALLOWLISTED,
        "Product is not on the risk-policy allowlist.",
    )


def _allocation_membership(
    policy: RiskPolicyDefinition,
    strategy_id: UUID | None,
    *,
    mode: DeploymentMode,
    portfolio_member: bool = False,
) -> RiskVerdict:
    """When allocations exist, live requires a listed strategy and denies discretionary books.

    Allocations reserve real capital, so membership gates LIVE only. Paper research is not
    blocked by them: an unlisted paper strategy or discretionary paper book is allowed and
    sized by paper capital, while a listed strategy's paper starting cash and exposure stay
    bounded by its allocation (a rehearsal of the live reservation). A sleeve of a live
    portfolio is a member through its portfolio: the sleeve's weight times the portfolio's
    capital is its reservation (ADR 0091). Standalone books keep these semantics.
    """
    if not policy.allocations or mode is not DeploymentMode.LIVE:
        return _allow()
    if portfolio_member and strategy_id is not None:
        return _allow()
    if strategy_id is None:
        return _deny(
            RiskReasonCode.DISCRETIONARY_NOT_ALLOCATED,
            "Discretionary orders are denied when risk-policy allocations are in force.",
        )
    if any(item.strategy_id == strategy_id for item in policy.allocations):
        return _allow()
    return _deny(
        RiskReasonCode.STRATEGY_NOT_ALLOCATED,
        "Strategy is not listed in risk-policy allocations.",
    )


def _allocation_for(policy: RiskPolicyDefinition, strategy_id: UUID | None) -> Decimal | None:
    """Return the reserved quote for one strategy, if allocations are in force."""
    if not policy.allocations or strategy_id is None:
        return None
    match = next((item for item in policy.allocations if item.strategy_id == strategy_id), None)
    if match is None:
        return None
    return Decimal(match.allocated_quote)


def _occupied(deployments: Sequence[Deployment], mode: DeploymentMode) -> tuple[Deployment, ...]:
    """Return running and paused deployments in one mode."""
    return tuple(item for item in deployments if item.mode is mode and occupies_running_slot(item))


def _marked_exposure(snapshot: DeploymentSnapshot) -> Decimal:
    """Approximate quote exposure from open books plus every working remainder."""
    books = snapshot_positions(snapshot)
    total = sum((item.quantity * item.entry_price for item in books), Decimal("0"))
    if snapshot.instrument_runtimes:
        for runtime in snapshot.instrument_runtimes:
            if runtime.phase in _IN_MARKET:
                total += working_entry_notional(snapshot, runtime.product_id)
        return total
    if books:
        for position in books:
            product_id = resolved_product_id(position.product_id, snapshot.deployment)
            total += working_entry_notional(snapshot, product_id)
        return total
    if snapshot.position is not None:
        position_total = snapshot.position.quantity * snapshot.position.entry_price
        return position_total + working_entry_notional(snapshot, snapshot.deployment.product_id)
    return working_entry_notional(snapshot, snapshot.deployment.product_id)


def _paper_committed(occupied: Sequence[Deployment]) -> Decimal:
    """Sum paper starting cash already reserved by occupied deployments."""
    total = Decimal("0")
    for item in occupied:
        if item.paper_starting_cash is not None:
            total += item.paper_starting_cash
    return total


def _capital_base(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    live_quote_cash: Decimal | None,
) -> Decimal:
    """Paper uses the policy book; live uses allocated or initial equity, never venue+cash mix."""
    if mode is DeploymentMode.PAPER:
        return Decimal(policy.paper_capital_quote)
    if live_quote_cash is None or live_quote_cash <= 0:
        return Decimal("0")
    return live_quote_cash


def _allow() -> RiskVerdict:
    """Return a successful gate decision."""
    return RiskVerdict(
        decision=RiskDecision.ALLOW,
        reason_code=RiskReasonCode.ALLOWED,
        detail="Risk policy allows this action.",
    )


def _deny(reason_code: RiskReasonCode, detail: str) -> RiskVerdict:
    """Return a fail-closed gate decision."""
    return RiskVerdict(decision=RiskDecision.DENY, reason_code=reason_code, detail=detail)
