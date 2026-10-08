"""Optional per-order bounds: quantity, notional, and the available-quote reserve.

Each bound applies only when the published policy sets it; unset fields are absent from
compiled and legacy policy bytes and never deny. Quote bounds refuse a product quoted in a
different currency, and an unknown available quote fails the reserve closed.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.market_data.products import quote_currency
from thytrader.risk.gate_common import ProposedEntry, _book_products, _deny
from thytrader.risk.models import RiskPolicyDefinition, RiskReasonCode, RiskVerdict
from thytrader.trading.exposure import working_entry_notional
from thytrader.trading.ledger import effective_paper_fee_rates
from thytrader.trading.models import DeploymentMode, DeploymentSnapshot, OrderSide, OrderStatus

if TYPE_CHECKING:
    from collections.abc import Sequence


def _order_bound_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
) -> RiskVerdict | None:
    """Apply optional quantity, notional, and balance-reserve caps when published.

    Unset fields are absent from compiled and legacy policy bytes and do not deny.
    """
    quantity = _quantity_bound(policy, proposed)
    if quantity is not None:
        return quantity
    monetary_bound = (
        policy.max_order_notional_quote is not None
        or policy.min_available_quote_reserve is not None
    )
    if monetary_bound and quote_currency(proposed.product_id) != policy.quote_currency:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Optional quote bounds cannot be applied to a different quote currency: "
            f"policy={policy.quote_currency}, product={proposed.product_id}.",
        )
    notional = _notional_bound(policy, proposed)
    if notional is not None:
        return notional
    return _reserve_bound(
        policy,
        mode=mode,
        proposed=proposed,
        occupied=occupied,
        live_quote_cash=live_quote_cash,
    )


def _quantity_bound(policy: RiskPolicyDefinition, proposed: ProposedEntry) -> RiskVerdict | None:
    """Deny when an optional base-quantity cap is set and the entry exceeds it."""
    cap = policy.max_order_quantity
    if cap is None:
        return None
    if proposed.quantity is None:
        return _deny(
            RiskReasonCode.MAX_ORDER_QUANTITY,
            "Order quantity cap is set but the proposed quantity is missing.",
        )
    if proposed.quantity > Decimal(cap):
        return _deny(
            RiskReasonCode.MAX_ORDER_QUANTITY,
            f"Proposed quantity {proposed.quantity} exceeds max_order_quantity {cap}.",
        )
    return None


def _notional_bound(policy: RiskPolicyDefinition, proposed: ProposedEntry) -> RiskVerdict | None:
    """Deny when an optional quote-notional cap is set and the entry exceeds it."""
    cap = policy.max_order_notional_quote
    if cap is None or proposed.notional <= Decimal(cap):
        return None
    return _deny(
        RiskReasonCode.MAX_ORDER_NOTIONAL,
        f"Proposed notional {proposed.notional} exceeds max_order_notional_quote {cap}.",
    )


def _reserve_bound(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
) -> RiskVerdict | None:
    """Keep optional notional headroom, not a guaranteed post-fill venue balance.

    Live fees/slippage are unknown here and are not invented. Paper fees use the
    deployment's published schedule. Unknown venue holds fail closed.
    """
    reserve = policy.min_available_quote_reserve
    if reserve is None:
        return None
    required = Decimal(reserve)
    available = _available_after_entry(
        policy, mode=mode, proposed=proposed, occupied=occupied, live_quote_cash=live_quote_cash
    )
    if available is None:
        return _deny(
            RiskReasonCode.BALANCE_RESERVE,
            "Available quote is unknown; the balance reserve cannot be verified.",
        )
    if available < required:
        return _deny(
            RiskReasonCode.BALANCE_RESERVE,
            f"Entry notional headroom {available} is below the reserve of {required}; "
            "live fees and slippage are not included.",
        )
    return None


def _available_after_entry(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
) -> Decimal | None:
    """Quote-scoped admission headroom; never subtract confirmed venue holds twice."""
    if mode is DeploymentMode.LIVE:
        if live_quote_cash is None or live_quote_cash < 0:
            return None
        reserved = _local_unheld_buy_quote(occupied)
        if reserved is None:
            return None
        return live_quote_cash - reserved - proposed.notional
    cash_change = Decimal("0")
    pending = Decimal("0")
    fee_rate = Decimal("0")
    for item in occupied:
        opening = item.deployment.initial_equity
        if opening is None:
            opening = item.deployment.paper_starting_cash
        if opening is None:
            return None
        cash_change += item.deployment.cash - opening
        buys = replace(item, orders=tuple(o for o in item.orders if o.side is OrderSide.BUY))
        pending += sum(
            (working_entry_notional(buys, product) for product in _book_products(buys)),
            Decimal("0"),
        )
        _, taker = effective_paper_fee_rates(
            item.deployment.paper_maker_fee_rate, item.deployment.paper_taker_fee_rate
        )
        fee_rate = max(fee_rate, taker)
    if not occupied:
        _, fee_rate = effective_paper_fee_rates(None, None)
    return (
        Decimal(policy.paper_capital_quote)
        + cash_change
        - pending * (1 + fee_rate)
        - proposed.notional * (1 + fee_rate)
    )


def _local_unheld_buy_quote(books: Sequence[DeploymentSnapshot]) -> Decimal | None:
    """Local buy remainders without venue holds; ambiguous submissions make holds unknown."""
    reserved = Decimal("0")
    for item in books:
        buys = replace(item, orders=tuple(o for o in item.orders if o.side is OrderSide.BUY))
        if any(o.status in {OrderStatus.PENDING, OrderStatus.UNKNOWN} for o in buys.orders):
            return None
        unheld = replace(buys, orders=tuple(o for o in buys.orders if o.venue_order_id is None))
        reserved += sum(
            (working_entry_notional(unheld, product) for product in _book_products(unheld)),
            Decimal("0"),
        )
    return reserved
