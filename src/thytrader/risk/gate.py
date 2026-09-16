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
    from collections.abc import Sequence
    from uuid import UUID

_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_ENTRY, RuntimePhase.PENDING_EXIT}


@dataclass(frozen=True, slots=True)
class ProposedEntry:
    """One sized entry the runtime wants to rest after a matched closed bar."""

    product_id: str
    strategy_id: UUID | None
    notional: Decimal
    is_pyramid_add: bool = False


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
) -> RiskVerdict:
    """Allow a new running deployment only when slots, allowlist, and paper capital permit it.

    Live deployments additionally require an operator-published policy: a fresh
    install's compiled fallback must not become silent live authority (audit F25).
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
    allocated = _allocation_membership(policy, strategy_id)
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
) -> RiskVerdict:
    """Allow a risk-increasing entry only when slots, exposure, and breakers permit it."""
    occupied = tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode and occupies_running_slot(item.deployment)
    )
    risk_bearing = risk_bearing_snapshots(snapshots, mode)
    membership = _entry_membership(policy, proposed=proposed, occupied=occupied)
    if membership.decision is RiskDecision.DENY:
        return membership
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
    occupied = tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode and occupies_running_slot(item.deployment)
    )
    existing = sum((_marked_exposure(item) for item in occupied), Decimal("0"))
    capital = _capital_base(policy, mode=mode, live_quote_cash=live_quote_cash, existing=existing)
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


def _entry_membership(
    policy: RiskPolicyDefinition,
    *,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
) -> RiskVerdict:
    """Apply allowlist, allocation membership, and open-position slot caps."""
    allowlisted = _allowlist_verdict(policy, proposed.product_id)
    if allowlisted.decision is RiskDecision.DENY:
        return allowlisted
    allocated = _allocation_membership(policy, proposed.strategy_id)
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
    existing = sum((_marked_exposure(item) for item in occupied), Decimal("0"))
    capital = _capital_base(policy, mode=mode, live_quote_cash=live_quote_cash, existing=existing)
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
    capital = _capital_base(
        policy, mode=mode, live_quote_cash=live_quote_cash, existing=existing_total
    )
    if capital <= 0:
        return _deny(
            RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED,
            "Capital base is missing or non-positive; new entries are blocked.",
        )
    portfolio_cap = capital * Decimal(policy.max_portfolio_exposure_fraction)
    if policy.max_portfolio_exposure_quote is not None:
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


def _allocation_membership(policy: RiskPolicyDefinition, strategy_id: UUID | None) -> RiskVerdict:
    """When allocations exist, require a listed strategy; deny discretionary books."""
    if not policy.allocations:
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
    existing: Decimal,
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
