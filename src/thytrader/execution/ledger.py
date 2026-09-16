"""Mark-to-market paper/live PnL from recorded fills, without inventing missing marks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.models import OrderKind, OrderSide, PositionSide
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.execution.models import DeploymentSnapshot, Fill, Order

PAPER_MAKER_FEE_RATE = Decimal("0.001")
PAPER_TAKER_FEE_RATE = Decimal("0.002")
MAX_PAPER_FEE_RATE = Decimal("0.1")


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


def resolve_paper_fee_schedule(
    *,
    live: bool,
    maker_fee_rate: Decimal | None,
    taker_fee_rate: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Resolve paper maker/taker assumptions; live never stores modeled venue rates.

    Omitted paper rates become the documented ``0.001`` / ``0.002`` defaults. Provided
    rates must both be present, finite, in ``[0, 0.1]``, and maker must not exceed taker.
    Live rejects any supplied rates so Coinbase remains the fee authority.
    """
    if live:
        if maker_fee_rate is not None or taker_fee_rate is not None:
            raise ValueError("Live deployments do not accept paper fee rates.")
        return None, None
    if (maker_fee_rate is None) != (taker_fee_rate is None):
        raise ValueError("Paper fee rates require both maker_fee_rate and taker_fee_rate.")
    if maker_fee_rate is None or taker_fee_rate is None:
        return PAPER_MAKER_FEE_RATE, PAPER_TAKER_FEE_RATE
    _require_paper_fee_pair(maker_fee_rate, taker_fee_rate)
    return maker_fee_rate, taker_fee_rate


def effective_paper_fee_rates(
    maker_fee_rate: Decimal | None,
    taker_fee_rate: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Return stored paper rates, or the documented defaults when a book predates the columns."""
    if maker_fee_rate is None or taker_fee_rate is None:
        return PAPER_MAKER_FEE_RATE, PAPER_TAKER_FEE_RATE
    return maker_fee_rate, taker_fee_rate


def _require_paper_fee_pair(maker_fee_rate: Decimal, taker_fee_rate: Decimal) -> None:
    """Reject out-of-range or inverted paper fee assumptions."""
    for rate in (maker_fee_rate, taker_fee_rate):
        if not rate.is_finite() or rate < 0 or rate > MAX_PAPER_FEE_RATE:
            raise ValueError("Paper fee rates must be finite decimals in [0, 0.1].")
    if maker_fee_rate > taker_fee_rate:
        raise ValueError("maker_fee_rate must not exceed taker_fee_rate.")


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
    quantity = Decimal("0")
    entry_price = None
    if position is not None:
        quantity = position.quantity
        if position.side is PositionSide.SHORT:
            quantity = -quantity
        entry_price = position.entry_price
    return mark_deployment_ledger(
        starting_cash=starting,
        cash=deployment.cash,
        fills=ledger_fills_from_snapshot(snapshot),
        position_quantity=quantity,
        position_entry_price=entry_price,
        mark_price=mark_price,
    )


def realized_pnl_since(snapshot: DeploymentSnapshot, *, since: datetime) -> Decimal:
    """Return realized PnL attributed to fills at or after ``since``."""
    fills = ledger_fills_from_snapshot(snapshot)
    state = _LotState(
        quantity=Decimal("0"),
        entry_price=None,
        entry_fees=Decimal("0"),
        realized=Decimal("0"),
        trade_count=0,
    )
    attributed = Decimal("0")
    for fill in fills:
        before = state.realized
        if fill.side is OrderSide.BUY:
            state = _fold_buy(state, fill)
        else:
            state = _fold_sell(state, fill)
        if fill.filled_at >= since:
            attributed += state.realized - before
    return attributed


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
    base_quantity = reconstructed_qty if position_quantity == 0 else position_quantity
    entry_price = position_entry_price if position_quantity != 0 else reconstructed_entry
    needs_mark = base_quantity != 0
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


@dataclass(frozen=True, slots=True)
class _LotState:
    """Open-lot bookkeeping used while folding fills into realized PnL."""

    quantity: Decimal
    entry_price: Decimal | None
    entry_fees: Decimal
    realized: Decimal
    trade_count: int


def _fill_sort_key(fill: Fill) -> tuple[datetime, str]:
    """Order fills by time, then venue id, so the ledger is deterministic."""
    return (fill.filled_at, fill.venue_fill_id)


def _flatten(state: _LotState) -> _LotState:
    """Close the current lot after a covering fill reaches or passes zero."""
    return _LotState(
        quantity=Decimal("0"),
        entry_price=None,
        entry_fees=Decimal("0"),
        realized=state.realized,
        trade_count=state.trade_count + 1,
    )


def _fold_buy(state: _LotState, fill: LedgerFill) -> _LotState:
    """Apply one buy: open or add to a long, or cover a short without flipping."""
    if state.quantity == 0:
        return _LotState(
            quantity=fill.quantity,
            entry_price=fill.price,
            entry_fees=fill.fee,
            realized=state.realized,
            trade_count=state.trade_count,
        )
    if state.quantity > 0:
        combined = state.quantity + fill.quantity
        basis = fill.price if state.entry_price is None else state.entry_price
        entry_price = ((basis * state.quantity) + (fill.price * fill.quantity)) / combined
        return _LotState(
            quantity=combined,
            entry_price=entry_price,
            entry_fees=state.entry_fees + fill.fee,
            realized=state.realized,
            trade_count=state.trade_count,
        )
    covered = min(fill.quantity, -state.quantity)
    realized = state.realized
    entry_fees = state.entry_fees
    if state.entry_price is not None:
        allocated_entry_fees = entry_fees * covered / -state.quantity
        exit_notional = fill.price * covered
        fee_share = fill.fee * covered / fill.quantity
        realized += (state.entry_price * covered) - exit_notional - fee_share - allocated_entry_fees
        entry_fees -= allocated_entry_fees
    quantity = state.quantity + fill.quantity
    updated = _LotState(
        quantity=quantity,
        entry_price=state.entry_price,
        entry_fees=entry_fees,
        realized=realized,
        trade_count=state.trade_count,
    )
    if quantity >= 0:
        return _flatten(updated)
    return updated


def _fold_sell(state: _LotState, fill: LedgerFill) -> _LotState:
    """Apply one sell: open or add to a short, or reduce a long without flipping."""
    exit_notional = fill.price * fill.quantity
    if state.quantity == 0:
        return _LotState(
            quantity=-fill.quantity,
            entry_price=fill.price,
            entry_fees=fill.fee,
            realized=state.realized,
            trade_count=state.trade_count,
        )
    if state.quantity < 0:
        combined = -state.quantity + fill.quantity
        basis = fill.price if state.entry_price is None else state.entry_price
        entry_price = ((basis * -state.quantity) + (fill.price * fill.quantity)) / combined
        return _LotState(
            quantity=state.quantity - fill.quantity,
            entry_price=entry_price,
            entry_fees=state.entry_fees + fill.fee,
            realized=state.realized,
            trade_count=state.trade_count,
        )
    realized = state.realized
    entry_fees = state.entry_fees
    if state.entry_price is not None and state.quantity > 0:
        allocated_entry_fees = entry_fees * fill.quantity / state.quantity
        realized += exit_notional - fill.fee - (state.entry_price * fill.quantity)
        realized -= allocated_entry_fees
        entry_fees -= allocated_entry_fees
    quantity = state.quantity - fill.quantity
    updated = _LotState(
        quantity=quantity,
        entry_price=state.entry_price,
        entry_fees=entry_fees,
        realized=realized,
        trade_count=state.trade_count,
    )
    if quantity <= 0:
        return _flatten(updated)
    return updated


def _fold_fills(
    starting_cash: Decimal, fills: Sequence[LedgerFill]
) -> tuple[Decimal, int, Decimal, Decimal | None, tuple[Decimal, ...]]:
    """Replay buys and sells for realized PnL, open quantity, and fill-event equity."""
    cash = starting_cash
    state = _LotState(
        quantity=Decimal("0"),
        entry_price=None,
        entry_fees=Decimal("0"),
        realized=Decimal("0"),
        trade_count=0,
    )
    equities: list[Decimal] = [starting_cash]
    for fill in fills:
        if fill.side is OrderSide.BUY:
            cash -= fill.price * fill.quantity + fill.fee
            state = _fold_buy(state, fill)
        else:
            cash += fill.price * fill.quantity - fill.fee
            state = _fold_sell(state, fill)
        equities.append(cash + state.quantity * fill.price)
    return state.realized, state.trade_count, state.quantity, state.entry_price, tuple(equities)


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
