"""Mark-to-market paper/live PnL from recorded fills, without inventing missing marks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.models import OrderKind, OrderSide
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.execution.models import DeploymentSnapshot, Fill, Order

PAPER_MAKER_FEE_RATE = Decimal("0.001")
PAPER_TAKER_FEE_RATE = Decimal("0.002")


@dataclass(frozen=True, slots=True)
class LedgerFill:
    """One fill with the order side required to pair round trips."""

    side: OrderSide
    price: Decimal
    quantity: Decimal
    fee: Decimal
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class DeploymentLedger:
    """Exact fill-ledger statistics using the same Decimal rendering as research summaries."""

    starting_cash: Decimal
    cash: Decimal
    base_quantity: Decimal
    mark_price: Decimal | None
    equity: Decimal | None
    realized_net_pnl: Decimal
    unrealized_net_pnl: Decimal | None
    total_net_pnl: Decimal | None
    total_return_fraction: Decimal | None
    total_fees: Decimal
    maximum_drawdown: Decimal | None
    maximum_drawdown_fraction: Decimal | None
    trade_count: int
    mark_complete: bool

    def total_net_pnl_text(self) -> str | None:
        """Render total net PnL as a canonical decimal, or None when the mark is missing."""
        if self.total_net_pnl is None:
            return None
        return canonical_decimal(self.total_net_pnl)

    def total_return_fraction_text(self) -> str | None:
        """Render total return as a canonical decimal, or None when the mark is missing."""
        if self.total_return_fraction is None:
            return None
        return canonical_decimal(self.total_return_fraction)

    def maximum_drawdown_fraction_text(self) -> str | None:
        """Render fill-to-mark drawdown as a canonical decimal when a curve exists."""
        if self.maximum_drawdown_fraction is None:
            return None
        return canonical_decimal(self.maximum_drawdown_fraction)


def paper_fill_fee(
    *,
    kind: OrderKind,
    price: Decimal,
    quantity: Decimal,
    maker_fee_rate: Decimal = PAPER_MAKER_FEE_RATE,
    taker_fee_rate: Decimal = PAPER_TAKER_FEE_RATE,
) -> Decimal:
    """Return the documented paper fee for one fill: maker on post-only, taker on marketable."""
    rate = maker_fee_rate if kind is OrderKind.POST_ONLY_LIMIT else taker_fee_rate
    return price * quantity * rate


def ledger_fills_from_snapshot(snapshot: DeploymentSnapshot) -> tuple[LedgerFill, ...]:
    """Join fills to their orders so the ledger can pair buys and sells."""
    orders: dict[UUID, Order] = {order.id: order for order in snapshot.orders}
    fills: list[LedgerFill] = []
    for fill in sorted(snapshot.fills, key=_fill_sort_key):
        order = orders.get(fill.order_id)
        if order is None:
            continue
        fills.append(
            LedgerFill(
                side=order.side,
                price=fill.price,
                quantity=fill.quantity,
                fee=fill.fee,
                filled_at=fill.filled_at,
            )
        )
    return tuple(fills)


def ledger_from_snapshot(
    snapshot: DeploymentSnapshot,
    *,
    mark_price: Decimal | None,
) -> DeploymentLedger:
    """Fold one deployment snapshot plus an optional last-close mark into ledger statistics."""
    deployment = snapshot.deployment
    starting = (
        deployment.paper_starting_cash
        if deployment.paper_starting_cash is not None
        else Decimal("0")
    )
    position = snapshot.position
    quantity = position.quantity if position is not None else Decimal("0")
    entry_price = position.entry_price if position is not None else None
    return mark_deployment_ledger(
        starting_cash=starting,
        cash=deployment.cash,
        fills=ledger_fills_from_snapshot(snapshot),
        position_quantity=quantity,
        position_entry_price=entry_price,
        mark_price=mark_price,
    )


def mark_deployment_ledger(
    *,
    starting_cash: Decimal,
    cash: Decimal,
    fills: Sequence[LedgerFill],
    position_quantity: Decimal,
    position_entry_price: Decimal | None,
    mark_price: Decimal | None,
) -> DeploymentLedger:
    """Fold fills plus an optional last-close mark into realized, unrealized, and drawdown.

    Open inventory without a disclosed mark leaves total PnL unset instead of inventing equity.
    Drawdown walks fill-event marks, then the current last-close mark when it exists.
    Equity uses the operational cash balance, not a reconstructed fill replay.
    """
    realized, trade_count, reconstructed_qty, reconstructed_entry, fill_equities = _fold_fills(
        starting_cash, fills
    )
    total_fees = sum((fill.fee for fill in fills), start=Decimal("0"))
    base_quantity = position_quantity if position_quantity > 0 else reconstructed_qty
    entry_price = position_entry_price if position_quantity > 0 else reconstructed_entry
    needs_mark = base_quantity > 0
    mark_complete = (not needs_mark) or mark_price is not None
    unrealized: Decimal | None = None
    equity: Decimal | None = cash if not needs_mark else None
    if needs_mark and mark_price is not None and entry_price is not None:
        unrealized = base_quantity * (mark_price - entry_price)
        equity = cash + base_quantity * mark_price
    elif not needs_mark:
        unrealized = Decimal("0")
    total_net_pnl = None if equity is None else equity - starting_cash
    total_return_fraction = None
    if total_net_pnl is not None and starting_cash > 0:
        total_return_fraction = total_net_pnl / starting_cash
    curve = list(fill_equities)
    if equity is not None:
        curve.append(equity)
    maximum_drawdown, maximum_drawdown_fraction = _drawdown(curve)
    return DeploymentLedger(
        starting_cash=starting_cash,
        cash=cash,
        base_quantity=base_quantity,
        mark_price=mark_price,
        equity=equity,
        realized_net_pnl=realized,
        unrealized_net_pnl=unrealized,
        total_net_pnl=total_net_pnl,
        total_return_fraction=total_return_fraction,
        total_fees=total_fees,
        maximum_drawdown=maximum_drawdown,
        maximum_drawdown_fraction=maximum_drawdown_fraction,
        trade_count=trade_count,
        mark_complete=mark_complete,
    )


def _fill_sort_key(fill: Fill) -> tuple[datetime, str]:
    """Order fills by time, then venue id, so the ledger is deterministic."""
    return (fill.filled_at, fill.venue_fill_id)


def _fold_fills(
    starting_cash: Decimal, fills: Sequence[LedgerFill]
) -> tuple[Decimal, int, Decimal, Decimal | None, tuple[Decimal, ...]]:
    """Replay buys and sells for realized PnL, open quantity, and fill-event equity."""
    cash = starting_cash
    quantity = Decimal("0")
    entry_price: Decimal | None = None
    entry_fees = Decimal("0")
    realized = Decimal("0")
    trade_count = 0
    equities: list[Decimal] = [starting_cash]
    for fill in fills:
        if fill.side is OrderSide.BUY:
            cash -= fill.price * fill.quantity + fill.fee
            if quantity == 0:
                entry_price = fill.price
                quantity = fill.quantity
                entry_fees = fill.fee
            else:
                combined = quantity + fill.quantity
                if entry_price is None:
                    entry_price = fill.price
                else:
                    entry_price = (
                        (entry_price * quantity) + (fill.price * fill.quantity)
                    ) / combined
                entry_fees += fill.fee
                quantity = combined
        else:
            exit_notional = fill.price * fill.quantity
            cash += exit_notional - fill.fee
            sold = fill.quantity
            if entry_price is not None and quantity > 0:
                allocated_entry_fees = entry_fees * sold / quantity
                realized += exit_notional - fill.fee - (entry_price * sold) - allocated_entry_fees
                entry_fees -= allocated_entry_fees
            quantity -= sold
            if quantity <= 0:
                trade_count += 1
                quantity = Decimal("0")
                entry_price = None
                entry_fees = Decimal("0")
        mark = fill.price
        equities.append(cash + quantity * mark)
    return realized, trade_count, quantity, entry_price, tuple(equities)


def _drawdown(equities: Sequence[Decimal]) -> tuple[Decimal | None, Decimal | None]:
    """Return exact absolute and fractional maximum drawdown over disclosed marks."""
    if not equities:
        return None, None
    peak = equities[0]
    maximum_drawdown = Decimal("0")
    maximum_drawdown_fraction = Decimal("0")
    for equity in equities:
        peak = max(peak, equity)
        if peak <= 0:
            continue
        drawdown = peak - equity
        maximum_drawdown = max(maximum_drawdown, drawdown)
        maximum_drawdown_fraction = max(maximum_drawdown_fraction, drawdown / peak)
    return maximum_drawdown, maximum_drawdown_fraction
