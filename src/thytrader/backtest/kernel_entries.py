"""Entries of the backtest kernel: resting, gating, sizing, and matching close limits.

A matched signal rests a post-only limit at the signal close (or is counted under
one skip reason); a later bar fills, waits, cancels, or reprices it. Sizing uses ATR
risk bounded by strategy, exposure, and cash caps, including pyramid adds.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.backtest.kernel_atr import _indicator_value
from thytrader.backtest.kernel_fills import _fill_resting_entry
from thytrader.backtest.kernel_state import (
    PositionSide,
    _Book,
    _Costs,
    _PendingEntry,
    _Position,
    _Tally,
)
from thytrader.backtest.models import BacktestGateReason
from thytrader.execution.economics import target_guard_allows
from thytrader.execution.geometry import EntrySkipReason, entry_levels
from thytrader.execution.models import PositionSide as RuntimePositionSide
from thytrader.research.stress import maker_touched
from thytrader.research.trace import EntryConditionOutcome, SignalTraceRecord
from thytrader.strategies.models import StrategyDefinition, can_pyramid_add, reward_risk_multiple

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle


_CASH_CAP_HEADROOM = Decimal("1e-12")
"""Fraction of fee-adjusted cash a cash-capped entry leaves unspent, so it always funds."""


@dataclass(frozen=True, slots=True)
class _SizedNotional:
    """ATR-risk notional after the strategy, exposure, and cash caps, and whether one bound."""

    notional: Decimal
    capped: bool


def _match_entry(
    book: _Book,
    candle: Candle,
    *,
    offset: int,
    strategy: StrategyDefinition,
    costs: _Costs,
    cash: Decimal,
    tally: _Tally,
) -> Decimal:
    """Fill a resting limit when the closed bar trades through, else wait, cancel, or reprice."""
    pending = book.pending
    if pending is None:
        return cash
    if book.position is not None and not pending.is_pyramid_add:
        return cash
    latency = 0 if costs.execution_stress is None else costs.execution_stress.entry_latency_bars
    if pending.waited_bars < latency:
        book.pending = replace(pending, waited_bars=pending.waited_bars + 1)
        return cash
    traded_through = maker_touched(
        high=candle.high,
        low=candle.low,
        price=pending.limit_price,
        buy=pending.side == "long",
        stress=costs.execution_stress,
    )
    if traded_through:
        book.pending = None
        if costs.execution_stress is not None:
            pending = replace(
                pending,
                quantity=pending.quantity * Decimal(costs.execution_stress.entry_fill_fraction),
            )
        return _fill_resting_entry(
            book, pending, candle, offset=offset, costs=costs, cash=cash, tally=tally
        )
    waited = pending.waited_bars + 1
    if waited - latency < strategy.execution.max_entry_wait_bars:
        book.pending = replace(pending, waited_bars=waited)
        return cash
    if strategy.execution.on_unfilled_entry == "reprice":
        if not target_guard_allows(
            strategy.entry.economic_guard,
            side=pending.side,
            entry=candle.close,
            target=pending.target_price,
            fee=costs.maker_fee_rate,
        ):
            book.pending = None
            book.cooldown_bars = max(strategy.entry.cooldown_bars, 1)
            tally.entries_expired += 1
            return cash
        book.pending = replace(pending, limit_price=candle.close, waited_bars=latency)
        tally.entries_repriced += 1
        return cash
    book.pending = None
    book.cooldown_bars = max(strategy.entry.cooldown_bars, 1)
    tally.entries_expired += 1
    return cash


def _maybe_rest_entry(
    book: _Book,
    record: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    costs: _Costs,
    cash: Decimal,
    limit_price: Decimal,
    may_open_book: bool,
    tally: _Tally,
) -> None:
    """Rest a post-only entry at the signal bar close when matched, off cooldown, and allowed.

    Every matched signal either rests an entry or is counted under exactly one skip
    reason, so a zero-trade result explains itself (ADR 0090). The checks are pure and
    decide exactly as before; only the counting is new.
    """
    if record.entry_condition is EntryConditionOutcome.UNDEFINED:
        tally.warmup_bars += 1
        return
    if record.entry_condition is not EntryConditionOutcome.MATCHED:
        return
    tally.signals_matched += 1
    gate = _entry_gate(
        book, strategy=strategy, limit_price=limit_price, may_open_book=may_open_book
    )
    if gate is not None:
        tally.skip(gate)
        return
    position = book.position
    sized = (
        _size_entry(
            record,
            strategy=strategy,
            cash=cash,
            limit_price=limit_price,
            maker_fee_rate=costs.maker_fee_rate,
        )
        if position is None
        else _size_pyramid_add(
            record,
            strategy=strategy,
            cash=cash,
            limit_price=limit_price,
            maker_fee_rate=costs.maker_fee_rate,
            position=position,
        )
    )
    if isinstance(sized, EntrySkipReason):
        tally.skip(sized)
        return
    book.pending = sized
    tally.rested(sized)


def _entry_gate(
    book: _Book,
    *,
    strategy: StrategyDefinition,
    limit_price: Decimal,
    may_open_book: bool,
) -> BacktestGateReason | None:
    """Name the book state that prevents resting an entry, or None when sizing may proceed."""
    if book.pending is not None:
        return BacktestGateReason.PENDING_ENTRY
    if book.cooldown_bars > 0:
        return BacktestGateReason.COOLDOWN
    position = book.position
    if position is None:
        return None if may_open_book else BacktestGateReason.MAX_POSITIONS
    if can_pyramid_add(
        strategy=strategy,
        side=position.side,
        entry_price=Decimal(position.entry.price),
        mark=limit_price,
        add_count=position.add_count,
    ):
        return None
    return BacktestGateReason.IN_POSITION


def _bounded_notional(
    strategy: StrategyDefinition,
    *,
    cash: Decimal,
    stop_distance: Decimal,
    limit_price: Decimal,
    maker_fee_rate: Decimal,
) -> _SizedNotional | EntrySkipReason:
    """Return ATR-risk notional bounded by strategy, exposure, and cash limits, if tradable.

    The cash bound keeps ``_CASH_CAP_HEADROOM`` back from ``cash / (1 + maker fee)``. The
    fill re-derives notional from ``quantity = notional / price``, so sizing to exactly
    that bound left funding to last-digit rounding, and about a third of cash-capped
    entries were refused at fill. Live sizing rounds down to venue increments for the same
    reason.
    """
    risk_quantity = cash * Decimal(strategy.sizing.risk_fraction) / stop_distance
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        cash / (Decimal("1") + maker_fee_rate) * (Decimal("1") - _CASH_CAP_HEADROOM),
    )
    requested = risk_quantity * limit_price
    notional = min(requested, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return EntrySkipReason.NOTIONAL_BELOW_MINIMUM
    return _SizedNotional(notional=notional, capped=requested > maximum_notional)


def _size_entry(
    signal: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    limit_price: Decimal,
    maker_fee_rate: Decimal,
) -> _PendingEntry | EntrySkipReason:
    """Size a resting entry at the signal close using ATR risk, without filling yet.

    Geometry is the shared ``entry_levels`` that paper and live use (without venue
    increments): a short whose target would be at or below zero is ``target_not_positive``.
    """
    atr = _indicator_value(signal, strategy.exits.initial_stop.atr_indicator)
    stop_distance = atr * Decimal(strategy.exits.initial_stop.multiple)
    side: PositionSide = strategy.entry.side
    levels = entry_levels(
        side=RuntimePositionSide(side),
        entry_price=limit_price,
        stop_distance=stop_distance,
        reward_multiple=reward_risk_multiple(strategy.exits),
    )
    if isinstance(levels, EntrySkipReason):
        return levels
    if not target_guard_allows(
        strategy.entry.economic_guard,
        side=side,
        entry=limit_price,
        target=levels.target_price,
        fee=maker_fee_rate,
    ):
        return EntrySkipReason.NET_TARGET_BELOW_MINIMUM
    sized = _bounded_notional(
        strategy,
        cash=cash,
        stop_distance=stop_distance,
        limit_price=limit_price,
        maker_fee_rate=maker_fee_rate,
    )
    if isinstance(sized, EntrySkipReason):
        return sized
    return _PendingEntry(
        signal=signal,
        limit_price=limit_price,
        quantity=sized.notional / limit_price,
        stop_price=levels.stop_price,
        target_price=levels.target_price,
        waited_bars=0,
        side=side,
        size_capped=sized.capped,
    )


def _size_pyramid_add(
    signal: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    limit_price: Decimal,
    maker_fee_rate: Decimal,
    position: _Position,
) -> _PendingEntry | EntrySkipReason:
    """Size a same-side add against the existing stop without worsening the target."""
    if not target_guard_allows(
        strategy.entry.economic_guard,
        side=position.side,
        entry=limit_price,
        target=position.target_price,
        fee=maker_fee_rate,
    ):
        return EntrySkipReason.NET_TARGET_BELOW_MINIMUM
    stop_distance = (
        limit_price - position.stop_price
        if position.side == "long"
        else position.stop_price - limit_price
    )
    if limit_price <= 0:
        return EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE
    if stop_distance <= 0:
        return EntrySkipReason.STOP_DISTANCE_NOT_POSITIVE
    if cash <= 0:
        return EntrySkipReason.INSUFFICIENT_CASH
    sized = _bounded_notional(
        strategy,
        cash=cash,
        stop_distance=stop_distance,
        limit_price=limit_price,
        maker_fee_rate=maker_fee_rate,
    )
    if isinstance(sized, EntrySkipReason):
        return sized
    return _PendingEntry(
        signal=signal,
        limit_price=limit_price,
        quantity=sized.notional / limit_price,
        stop_price=position.stop_price,
        target_price=position.target_price,
        waited_bars=0,
        side=position.side,
        is_pyramid_add=True,
        size_capped=sized.capped,
    )
