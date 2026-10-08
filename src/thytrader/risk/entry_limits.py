"""Risk-policy membership, slot, account exposure, and capital-base checks for the gate.

Membership covers the product allowlist and, on live, strategy allocations (a live
portfolio's sleeve is a member through its portfolio, ADR 0091). Slots count distinct
in-market product books. Exposure compares marked exposure plus the proposed notional to
the account, product, and strategy-allocation caps over the mode's capital base, which is
never a bot allocation or duplicated live ledger cash.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.risk.gate_common import (
    ProposedEntry,
    _allow,
    _book_products,
    _deny,
    _marked_exposure,
)
from thytrader.risk.models import RiskDecision, RiskPolicyDefinition, RiskReasonCode, RiskVerdict
from thytrader.trading.exposure import product_exposure, working_entry_notional
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    OrderSide,
    PositionSide,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID


_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_ENTRY, RuntimePhase.PENDING_EXIT}


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
    capital = _capital_base(policy, mode=mode, live_quote_cash=live_quote_cash, occupied=occupied)
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
            f"Account exposure exceeded: existing={existing_total}, "
            f"proposed={proposed.notional}, cap={portfolio_cap}, capital={capital}, "
            f"fraction={policy.max_portfolio_exposure_fraction}, "
            f"absolute={policy.max_portfolio_exposure_quote}.",
        )
    product_cap = capital * Decimal(policy.per_product_max_exposure_fraction)
    if existing_product + proposed.notional > product_cap:
        return _deny(
            RiskReasonCode.PRODUCT_EXPOSURE_EXCEEDED,
            f"Product exposure exceeded for {proposed.product_id}: existing={existing_product}, "
            f"proposed={proposed.notional}, cap={product_cap}, capital={capital}.",
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
            f"Strategy allocation exceeded: existing={strategy_exposure}, "
            f"proposed={proposed.notional}, allocation={reserved}.",
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


def _capital_base(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    live_quote_cash: Decimal | None,
    occupied: Sequence[DeploymentSnapshot],
) -> Decimal:
    """Use mode capital, never a bot allocation or duplicated live ledger cash.

    Live capital is observed available quote plus managed long inventory cost and
    working buy-entry reservations. Short sale proceeds are already in venue quote;
    sell reservations hold base units, so neither contributes quote a second time.
    """
    if mode is DeploymentMode.PAPER:
        return Decimal(policy.paper_capital_quote)
    if live_quote_cash is None or live_quote_cash < 0:
        return Decimal("0")
    return live_quote_cash + sum((_held_quote_capital(item) for item in occupied), Decimal("0"))


def _held_quote_capital(snapshot: DeploymentSnapshot) -> Decimal:
    """Quote held in managed long books and buy entries, excluding exits and short proceeds."""
    inventory = sum(
        (
            position.quantity * position.entry_price
            for position in snapshot_positions(snapshot)
            if position.side is PositionSide.LONG
        ),
        Decimal("0"),
    )
    buys = replace(
        snapshot, orders=tuple(order for order in snapshot.orders if order.side is OrderSide.BUY)
    )
    reserved = sum(
        (working_entry_notional(buys, product) for product in _book_products(buys)), Decimal("0")
    )
    return inventory + reserved
