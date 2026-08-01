"""Deterministic bar-level research simulation without broker or order authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from thytrader.backtest.models import (
    BacktestExitFill,
    BacktestFill,
    BacktestResult,
    BacktestSummary,
    BacktestTrade,
    EquityPoint,
)
from thytrader.research.indicators import canonical_decimal
from thytrader.research.models import ResearchRunSpecification, research_run_fingerprint
from thytrader.research.signal_evaluator import SignalEvaluationError, evaluate_signal_trace
from thytrader.research.trace import EntryConditionOutcome, signal_trace_fingerprint
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.market_data.models import Candle
    from thytrader.research.trace import SignalTraceRecord


_SIMULATION_CONTEXT = Context(
    prec=64,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


class BacktestSimulationError(ValueError):
    """Report a fail-closed simulation input or unsupported execution-condition failure."""


@dataclass(frozen=True, slots=True)
class _PendingEntry:
    """A close-time signal waiting for its sole permitted next-open modeled fill."""

    signal: SignalTraceRecord


@dataclass(frozen=True, slots=True)
class _OpenPosition:
    """The complete long-position state required for deterministic bar processing."""

    entry: BacktestFill
    stop_price: Decimal
    target_price: Decimal
    entered_bar_index: int


def simulate_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
) -> BacktestResult:
    """Simulate under a private Decimal64 context that ignores ambient process settings."""
    try:
        with localcontext(_SIMULATION_CONTEXT):
            return _simulate_backtest(specification, strategy, candles)
    except (DecimalException, ValueError) as error:
        if isinstance(error, BacktestSimulationError):
            raise
        raise BacktestSimulationError(
            "Backtest arithmetic failed under the deterministic Decimal contract."
        ) from error


def _simulate_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
) -> BacktestResult:
    """Simulate one published strategy with next-open taker fills and conservative OHLC exits."""
    specification, strategy = _validated_inputs(specification, strategy)
    try:
        trace = evaluate_signal_trace(specification, strategy, candles)
    except SignalEvaluationError as error:
        raise BacktestSimulationError("Backtest signal inputs could not be verified.") from error
    candle_by_start = _candle_map(specification, candles)
    cash = Decimal(specification.capital.initial_quote_balance)
    initial_cash = cash
    pending: _PendingEntry | None = None
    position: _OpenPosition | None = None
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    evaluation_records = {record.candle_starts_at: record for record in trace.records}
    evaluation_hours = int(
        (specification.evaluation.ends_at - specification.evaluation.starts_at) / timedelta(hours=1)
    )

    for offset in range(evaluation_hours + 1):
        starts_at = specification.evaluation.starts_at + timedelta(hours=offset)
        candle = candle_by_start[starts_at]
        if pending is not None:
            position, cash = _open_position(
                pending,
                candle,
                strategy=strategy,
                cash=cash,
                entry_bar_index=offset,
                taker_fee_rate=Decimal(specification.costs.taker_fee_rate),
                slippage_bps=Decimal(specification.costs.fixed_slippage_bps),
            )
            pending = None
        if position is not None and offset < evaluation_hours:
            trade, cash = _close_if_required(
                position,
                candle,
                cash=cash,
                bar_index=offset,
                taker_fee_rate=Decimal(specification.costs.taker_fee_rate),
                slippage_bps=Decimal(specification.costs.fixed_slippage_bps),
                max_bars_held=strategy.exits.time_exit.max_bars_held,
            )
            if trade is not None:
                trades.append(trade)
                position = None
        if offset < evaluation_hours:
            record = evaluation_records[starts_at]
            if position is None and record.entry_condition is EntryConditionOutcome.MATCHED:
                pending = _PendingEntry(signal=record)
        mark_price = candle.open if offset == evaluation_hours else candle.close
        equity_curve.append(_equity_point(candle.starts_at, cash, position, mark_price))

    if position is not None:
        forced_exit, cash = _close_position(
            position,
            candle_by_start[specification.evaluation.ends_at],
            cash=cash,
            bar_index=evaluation_hours,
            raw_exit_price=candle_by_start[specification.evaluation.ends_at].open,
            reason="evaluation_end",
            taker_fee_rate=Decimal(specification.costs.taker_fee_rate),
            slippage_bps=Decimal(specification.costs.fixed_slippage_bps),
        )
        trades.append(forced_exit)
        equity_curve[-1] = _equity_point(
            forced_exit.exit.candle_starts_at,
            cash,
            None,
            Decimal(forced_exit.exit.price),
        )

    return BacktestResult(
        schema_version="1.0",
        engine_contract_version="thytrader-bar-backtest-v1",
        run_fingerprint=research_run_fingerprint(specification),
        strategy_fingerprint=specification.strategy_fingerprint,
        dataset_fingerprint=specification.dataset_fingerprint,
        signal_trace_fingerprint=signal_trace_fingerprint(trace),
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        summary=_summary(initial_cash, cash, trades, equity_curve),
    )


def _validated_inputs(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
) -> tuple[ResearchRunSpecification, StrategyDefinition]:
    """Reconstruct typed inputs before performing identity or decimal calculations."""
    try:
        validated_specification = ResearchRunSpecification.model_validate(
            specification.model_dump(mode="python")
        )
        validated_strategy = StrategyDefinition.model_validate(
            strategy.model_dump(mode="python", by_alias=True)
        )
    except ValidationError as error:
        raise BacktestSimulationError("Backtest inputs are invalid.") from error
    if validated_specification.engine_contract_version != "thytrader-bar-backtest-v1":
        raise BacktestSimulationError("Backtest requires the backtest engine contract.")
    if strategy_fingerprint(validated_strategy) != validated_specification.strategy_fingerprint:
        raise BacktestSimulationError("Backtest strategy identity failed verification.")
    return validated_specification, validated_strategy


def _candle_map(
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
) -> Mapping[datetime, Candle]:
    """Require exactly one well-formed candle through the required final next-open fill."""
    starts_at = specification.warmup.starts_at
    ends_at = specification.evaluation.ends_at + timedelta(hours=1)
    selected = tuple(candle for candle in candles if starts_at <= candle.starts_at < ends_at)
    expected_count = int((ends_at - starts_at) / timedelta(hours=1))
    if len(selected) != expected_count:
        raise BacktestSimulationError("Backtest candle coverage is incomplete or duplicated.")
    mapped = {candle.starts_at: candle for candle in selected}
    if len(mapped) != expected_count:
        raise BacktestSimulationError("Backtest candle coverage is duplicated.")
    for offset in range(expected_count):
        expected_start = starts_at + timedelta(hours=offset)
        candle = mapped.get(expected_start)
        if candle is None or candle.open <= 0 or candle.high < candle.low:
            raise BacktestSimulationError(
                "Backtest candles are not valid contiguous hourly OHLC bars."
            )
    return mapped


def _open_position(
    pending: _PendingEntry,
    candle: Candle,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_bar_index: int,
    taker_fee_rate: Decimal,
    slippage_bps: Decimal,
) -> tuple[_OpenPosition | None, Decimal]:
    """Model a next-open taker entry using ATR risk sizing and never overdraw quote cash."""
    atr = _indicator_value(pending.signal, strategy.exits.initial_stop.atr_indicator)
    entry_price = _buy_price(candle.open, slippage_bps)
    stop_distance = atr * Decimal(strategy.exits.initial_stop.multiple)
    if stop_distance <= 0:
        return None, cash
    stop_price = entry_price - stop_distance
    if stop_price <= 0:
        return None, cash
    requested_risk = cash * Decimal(strategy.sizing.risk_fraction)
    risk_quantity = requested_risk / stop_distance
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        cash / (Decimal("1") + taker_fee_rate),
    )
    notional = min(risk_quantity * entry_price, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return None, cash
    quantity = notional / entry_price
    fee = notional * taker_fee_rate
    entry = BacktestFill(
        candle_starts_at=candle.starts_at,
        price=canonical_decimal(entry_price),
        quantity=canonical_decimal(quantity),
        notional=canonical_decimal(notional),
        fee=canonical_decimal(fee),
        fee_rate=canonical_decimal(taker_fee_rate),
    )
    target_price = entry_price + stop_distance * Decimal(strategy.exits.take_profit.multiple)
    return (
        _OpenPosition(
            entry=entry,
            stop_price=stop_price,
            target_price=target_price,
            entered_bar_index=entry_bar_index,
        ),
        cash - notional - fee,
    )


def _close_if_required(
    position: _OpenPosition,
    candle: Candle,
    *,
    cash: Decimal,
    bar_index: int,
    taker_fee_rate: Decimal,
    slippage_bps: Decimal,
    max_bars_held: int,
) -> tuple[BacktestTrade | None, Decimal]:
    """Close one position using stop-first ambiguity, then target and time-exit ordering."""
    if bar_index - position.entered_bar_index >= max_bars_held:
        return _close_position(
            position,
            candle,
            cash=cash,
            bar_index=bar_index,
            raw_exit_price=candle.open,
            reason="time_exit",
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
        )
    if candle.low <= position.stop_price:
        return _close_position(
            position,
            candle,
            cash=cash,
            bar_index=bar_index,
            raw_exit_price=position.stop_price,
            reason="stop_loss",
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
        )
    if candle.high >= position.target_price:
        return _close_position(
            position,
            candle,
            cash=cash,
            bar_index=bar_index,
            raw_exit_price=position.target_price,
            reason="take_profit",
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
        )
    return None, cash


def _close_position(
    position: _OpenPosition,
    candle: Candle,
    *,
    cash: Decimal,
    bar_index: int,
    raw_exit_price: Decimal,
    reason: Literal["stop_loss", "take_profit", "time_exit", "evaluation_end"],
    taker_fee_rate: Decimal,
    slippage_bps: Decimal,
) -> tuple[BacktestTrade, Decimal]:
    """Apply a modeled sell fill, fee, cash transition, and exact complete-trade evidence."""
    del bar_index
    exit_price = _sell_price(raw_exit_price, slippage_bps)
    quantity = Decimal(position.entry.quantity)
    exit_notional = quantity * exit_price
    exit_fee = exit_notional * taker_fee_rate
    exit_fill = BacktestExitFill(
        candle_starts_at=candle.starts_at,
        price=canonical_decimal(exit_price),
        quantity=position.entry.quantity,
        notional=canonical_decimal(exit_notional),
        fee=canonical_decimal(exit_fee),
        fee_rate=canonical_decimal(taker_fee_rate),
        reason=reason,
    )
    entry_cost = Decimal(position.entry.notional) + Decimal(position.entry.fee)
    net_pnl = exit_notional - exit_fee - entry_cost
    gross_pnl = exit_notional - Decimal(position.entry.notional)
    return (
        BacktestTrade(
            entry=position.entry,
            exit=exit_fill,
            gross_pnl=canonical_decimal(gross_pnl),
            net_pnl=canonical_decimal(net_pnl),
            holding_bars=_holding_bars(position.entry.candle_starts_at, candle.starts_at),
        ),
        cash + exit_notional - exit_fee,
    )


def _indicator_value(record: SignalTraceRecord, indicator_id: str) -> Decimal:
    """Load the exact ATR value that was available when the entry signal closed."""
    for value in record.indicator_values:
        if value.indicator_id == indicator_id and value.value is not None:
            return Decimal(value.value)
    raise BacktestSimulationError("Backtest entry signal lacks its required ATR value.")


def _buy_price(open_price: Decimal, slippage_bps: Decimal) -> Decimal:
    """Apply adverse fixed basis-point slippage to a marketable long entry."""
    return open_price * (Decimal("1") + slippage_bps / Decimal("10000"))


def _sell_price(raw_price: Decimal, slippage_bps: Decimal) -> Decimal:
    """Apply adverse fixed basis-point slippage to a marketable long exit."""
    return raw_price * (Decimal("1") - slippage_bps / Decimal("10000"))


def _equity_point(
    starts_at: datetime,
    cash: Decimal,
    position: _OpenPosition | None,
    mark_price: Decimal,
) -> EquityPoint:
    """Create one exact mark-to-market equity observation without decimal-float conversion."""
    quantity = Decimal("0") if position is None else Decimal(position.entry.quantity)
    equity = cash + quantity * mark_price
    return EquityPoint(
        candle_starts_at=starts_at,
        cash=canonical_decimal(cash),
        base_quantity=canonical_decimal(quantity),
        mark_price=canonical_decimal(mark_price),
        equity=canonical_decimal(equity),
    )


def _summary(
    initial_cash: Decimal,
    final_cash: Decimal,
    trades: Sequence[BacktestTrade],
    equity_curve: Sequence[EquityPoint],
) -> BacktestSummary:
    """Calculate only exact deterministic ledger and equity statistics in the V1 result."""
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
    exposure_bars = sum(max(1, trade.holding_bars) for trade in trades)
    evaluation_bars = len(equity_curve) - 1
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
        exposure_bars=exposure_bars,
        evaluation_bars=evaluation_bars,
    )


def _holding_bars(entry: datetime, exit_: datetime) -> int:
    """Return exact whole hourly bars between modeled entry and exit boundaries."""
    return int((exit_ - entry) / timedelta(hours=1))
