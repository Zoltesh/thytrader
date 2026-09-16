"""Pre-trade risk checks for deployments and long entries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.risk.models import (
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    RiskVerdict,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

_OCCUPIED = {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}
_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_ENTRY, RuntimePhase.PENDING_EXIT}
_ACTIVE_ORDER = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}


@dataclass(frozen=True, slots=True)
class ProposedEntry:
    """One sized long the runtime wants to rest after a matched closed bar."""

    product_id: str
    strategy_id: UUID | None
    notional: Decimal


def evaluate_new_deployment(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    product_id: str,
    strategy_id: UUID | None,
    paper_starting_cash: Decimal | None,
    deployments: Sequence[Deployment],
) -> RiskVerdict:
    """Allow a new running deployment only when slots, allowlist, and paper capital permit it."""
    occupied = _occupied(deployments, mode)
    allowlisted = _allowlist_verdict(policy, product_id)
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
) -> RiskVerdict:
    """Allow a risk-increasing entry only when open-position and exposure caps permit it."""
    occupied = tuple(
        item
        for item in snapshots
        if item.deployment.status in _OCCUPIED and item.deployment.mode is mode
    )
    allowlisted = _allowlist_verdict(policy, proposed.product_id)
    if allowlisted.decision is RiskDecision.DENY:
        return allowlisted
    allocated = _allocation_membership(policy, proposed.strategy_id)
    if allocated.decision is RiskDecision.DENY:
        return allocated
    open_count = sum(1 for item in occupied if _occupies_position_slot(item))
    if open_count >= policy.max_concurrent_open_positions:
        return _deny(
            RiskReasonCode.MAX_OPEN_POSITIONS,
            "Open and pending positions already use every concurrent slot for this mode.",
        )
    return _exposure_verdict(
        policy,
        mode=mode,
        proposed=proposed,
        occupied=occupied,
        live_quote_cash=live_quote_cash,
    )


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
        (
            _marked_exposure(item)
            for item in occupied
            if item.deployment.product_id == proposed.product_id
        ),
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
    return tuple(item for item in deployments if item.mode is mode and item.status in _OCCUPIED)


def _occupies_position_slot(snapshot: DeploymentSnapshot) -> bool:
    """True when the deployment holds inventory or a working entry/exit."""
    if snapshot.position is not None:
        return True
    return snapshot.deployment.phase in _IN_MARKET


def _marked_exposure(snapshot: DeploymentSnapshot) -> Decimal:
    """Approximate quote exposure from the open position or a working buy."""
    position = snapshot.position
    if position is not None:
        return position.quantity * position.entry_price
    for order in snapshot.orders:
        if (
            order.side is OrderSide.BUY
            and order.status in _ACTIVE_ORDER
            and order.price is not None
        ):
            remaining = order.quantity - order.filled_quantity
            if remaining > 0:
                return remaining * order.price
    return Decimal("0")


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
    """Paper uses the policy book; live uses remaining quote plus current exposure."""
    if mode is DeploymentMode.PAPER:
        return Decimal(policy.paper_capital_quote)
    if live_quote_cash is None or live_quote_cash < 0:
        return Decimal("0")
    return live_quote_cash + existing


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
