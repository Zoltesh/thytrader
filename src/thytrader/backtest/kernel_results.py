"""Result assembly of the backtest kernel: equity marks and the deterministic summary.

Equity points mark the shared quote book plus open inventory; the summary holds
exact ledger and drawdown statistics and the disclosed validity limits.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.backtest.models import BacktestSummary, BacktestTrade, EquityPoint
from thytrader.decimal_text import canonical_decimal
from thytrader.market_data.no_trade import is_no_trade_bar

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from thytrader.backtest.broker import FillModel
    from thytrader.backtest.kernel_state import _Book, _Position
    from thytrader.backtest.research_validity import ResearchValidityLimitCode


def _evaluated_no_trade_bars(
    books: Sequence[_Book], starts_at: datetime, bar: timedelta, evaluation_bars: int
) -> int:
    """Count flat zero-volume bars the evaluation loop processed across every book (ADR 0095)."""
    return sum(
        1
        for offset in range(evaluation_bars)
        for book in books
        if is_no_trade_bar(book.candle_by_start[starts_at + bar * offset])
    )


def _equity_point(
    starts_at: datetime,
    cash: Decimal,
    books: Sequence[_Book],
    fill_model: FillModel,
    *,
    use_open: bool,
) -> EquityPoint:
    """Mark one shared quote book plus every open product inventory at close (or open)."""
    equity = cash
    open_books: list[tuple[_Position, Decimal]] = []
    first_mark = Decimal("0")
    for index, book in enumerate(books):
        candle = book.candle_by_start[starts_at]
        raw = candle.open if use_open else candle.close
        position = book.position
        mark = raw if position is None else fill_model.mark(raw, position.side)
        if index == 0:
            first_mark = mark
        if position is not None:
            open_books.append((position, mark))
            equity += _signed_quantity(position) * mark
    if len(open_books) == 1:
        position, mark = open_books[0]
        base_quantity = _signed_quantity(position)
    else:
        base_quantity, mark = Decimal("0"), first_mark
    return EquityPoint(
        candle_starts_at=starts_at,
        cash=canonical_decimal(cash),
        base_quantity=canonical_decimal(base_quantity),
        mark_price=canonical_decimal(mark),
        equity=canonical_decimal(equity),
    )


def _signed_quantity(position: _Position) -> Decimal:
    """Return base inventory, negative for synthetic spot shorts."""
    quantity = Decimal(position.entry.quantity)
    return -quantity if position.side == "short" else quantity


def _summary(
    initial_cash: Decimal,
    final_cash: Decimal,
    trades: Sequence[BacktestTrade],
    equity_curve: Sequence[EquityPoint],
    *,
    include_spread_cost: bool,
    evaluation_bars: int,
    include_funding: bool = False,
    validity_limits: tuple[ResearchValidityLimitCode, ...],
) -> BacktestSummary:
    """Calculate only exact deterministic ledger and equity statistics.

    ``include_funding`` (perp futures runs) adds ``total_funding``, the signed sum of every
    trade's funding cash flow; spot summaries omit it.
    """
    peak = initial_cash
    maximum_drawdown = Decimal("0")
    maximum_drawdown_fraction = Decimal("0")
    for point in equity_curve:
        equity = Decimal(point.equity)
        peak = max(peak, equity)
        drawdown = peak - equity
        maximum_drawdown = max(maximum_drawdown, drawdown)
        maximum_drawdown_fraction = max(maximum_drawdown_fraction, drawdown / peak)
    net_pnls = tuple(Decimal(trade.net_pnl) for trade in trades)
    wins = tuple(pnl for pnl in net_pnls if pnl > 0)
    losses = tuple(pnl for pnl in net_pnls if pnl < 0)
    gross_profit = sum(wins, start=Decimal("0"))
    gross_loss = -sum(losses, start=Decimal("0"))
    trade_count = len(trades)
    total_spread_cost = (
        sum(
            (
                Decimal(fill.spread_cost or "0") * Decimal(fill.quantity)
                for trade in trades
                for fill in (trade.entry, trade.exit)
            ),
            start=Decimal("0"),
        )
        if include_spread_cost
        else None
    )
    return BacktestSummary(
        initial_equity=canonical_decimal(initial_cash),
        final_equity=canonical_decimal(final_cash),
        total_net_pnl=canonical_decimal(final_cash - initial_cash),
        total_return_fraction=canonical_decimal((final_cash - initial_cash) / initial_cash),
        gross_profit=canonical_decimal(gross_profit),
        gross_loss=canonical_decimal(gross_loss),
        win_rate=canonical_decimal(Decimal(len(wins)) / Decimal(trade_count))
        if trade_count
        else "0",
        profit_factor=canonical_decimal(gross_profit / gross_loss) if gross_loss else None,
        average_win=canonical_decimal(gross_profit / Decimal(len(wins))) if wins else None,
        average_loss=canonical_decimal(gross_loss / Decimal(len(losses))) if losses else None,
        trade_count=trade_count,
        winning_trade_count=len(wins),
        maximum_drawdown=canonical_decimal(maximum_drawdown),
        maximum_drawdown_fraction=canonical_decimal(maximum_drawdown_fraction),
        exposure_bars=sum(max(1, trade.holding_bars) for trade in trades),
        evaluation_bars=evaluation_bars,
        total_spread_cost=(
            canonical_decimal(total_spread_cost) if total_spread_cost is not None else None
        ),
        validity_limits=validity_limits,
        total_funding=(
            canonical_decimal(
                sum((Decimal(trade.funding or "0") for trade in trades), start=Decimal("0"))
            )
            if include_funding
            else None
        ),
    )
