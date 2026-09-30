"""Deterministic bar-level research simulation for the unified backtest model (ADR 0083).

There is exactly one simulator. Paper and live rest a post-only entry at the completed
signal bar's close, wait up to ``max_entry_wait_bars``, cancel or reprice when unfilled,
and can stop on the fill bar. This kernel reproduces that loop over completed OHLC bars:

1. A ``matched`` close-time signal rests a limit at that bar's close (buy for longs, sell
   for shorts), sized by ATR risk. It never fills on the signal bar.
2. A later bar fills the resting limit iff it trades through (``low <= limit`` for longs,
   ``high >= limit`` for shorts) at the posted price with the **maker** fee, no slippage,
   and no spread. Unfilled bars count toward ``max_entry_wait_bars``; expiry cancels (with
   at least one bar of cooldown) or reprices at the expiry bar's close.
3. On the fill bar only the stop is eligible: the take-profit rests after the fill bar,
   as in the worker. The stop is a marketable exit at ``min(open, stop)`` (``max`` for
   shorts) with the **taker** fee and fixed slippage.
4. On later bars a resting take-profit that the bar touches fills first, at the target
   with the maker fee (the worker matches resting orders before managing the position).
   Otherwise the stop is checked against the bar extreme, the ATR trail ratchets after
   that check, and a reached ``max_bars_held`` time exit sells at the close as a taker.
5. Equity marks at each evaluation close. The bar at ``evaluation.ends_at`` only
   liquidates open inventory at its **open** as a taker (``evaluation_end``); no entry,
   take-profit, or stop is processed there.

Multi-instrument documents evaluate covered products in lexicographic ``product_id``
order on each shared bar against one quote book. The optional ``costs.spread_bps``
stress applies half the spread to every taker leg, to stop triggers, and to open-position
marks; maker fills stay at their limit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from typing import TYPE_CHECKING, Literal, TypedDict

from pydantic import ValidationError

from thytrader.backtest.broker import FillModel, FillQuote
from thytrader.backtest.models import (
    BacktestExitFill,
    BacktestFill,
    BacktestResult,
    BacktestSummary,
    BacktestTrade,
    EquityPoint,
)
from thytrader.backtest.research_validity import (
    ResearchValidityLimitCode,
    collect_backtest_validity_limits,
)
from thytrader.execution.trailing import ratcheted_long_stop, ratcheted_short_stop
from thytrader.market_data.models import CandleInterval, parse_candle_interval
from thytrader.market_data.quality import (
    CandleQualityError,
    validate_candle_timestamp,
    validate_candle_values,
)
from thytrader.research.indicators import canonical_decimal
from thytrader.research.models import (
    BACKTEST_ENGINE,
    ResearchRunSpecification,
    research_run_fingerprint,
    specification_bar_interval,
)
from thytrader.research.signal_evaluator import SignalEvaluationError, evaluate_signal_trace
from thytrader.research.trace import (
    EntryConditionOutcome,
    SignalTrace,
    SignalTraceRecord,
    signal_trace_fingerprint,
)
from thytrader.strategies.models import (
    StrategyDefinition,
    atr_trailing_stop,
    can_pyramid_add,
    lockstep_product_ids,
    strategy_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.market_data.models import Candle


_SIMULATION_CONTEXT = Context(
    prec=64,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

ExitReason = Literal["stop_loss", "take_profit", "time_exit", "evaluation_end"]
PositionSide = Literal["long", "short"]


class BacktestSimulationError(ValueError):
    """Report a fail-closed simulation input or unsupported execution-condition failure."""


@dataclass(frozen=True, slots=True)
class _PendingEntry:
    """A close-limit entry (or same-side add) resting until a later bar trades through it."""

    signal: SignalTraceRecord
    limit_price: Decimal
    quantity: Decimal
    stop_price: Decimal
    target_price: Decimal
    waited_bars: int
    side: PositionSide = "long"
    is_pyramid_add: bool = False


@dataclass(frozen=True, slots=True)
class _Position:
    """An open position whose take-profit rests only after the fill bar, matching the worker."""

    entry: BacktestFill
    stop_price: Decimal
    target_price: Decimal
    entered_bar_index: int
    take_profit_resting: bool = False
    trail_extreme: Decimal | None = None
    side: PositionSide = "long"
    add_count: int = 1


@dataclass(slots=True)
class _Book:
    """Per-product resting entry, open position, and cooldown sharing one quote balance."""

    product_id: str
    candle_by_start: Mapping[datetime, Candle]
    records: Mapping[datetime, SignalTraceRecord]
    pending: _PendingEntry | None = None
    position: _Position | None = None
    cooldown_bars: int = 0

    @property
    def is_open(self) -> bool:
        """Whether this book holds a position or a resting entry that may open one."""
        return self.position is not None or self.pending is not None


@dataclass(frozen=True, slots=True)
class _Costs:
    """Published fee, slippage, and spread-stress assumptions resolved to Decimals."""

    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    slippage_bps: Decimal
    fill_model: FillModel


class _FillEvidence(TypedDict, total=False):
    """Spread-stress fill fields, omitted from maker fills and zero-spread runs."""

    reference_price: str
    executable_side: Literal["ask", "bid"]
    spread_cost: str


def simulate_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_instrument_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_htf_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_indicator_candles: Mapping[str, Mapping[str, Sequence[Candle]]] | None = None,
) -> BacktestResult:
    """Simulate under a private Decimal64 context that ignores ambient process settings."""
    try:
        with localcontext(_SIMULATION_CONTEXT):
            return _simulate_backtest(
                specification,
                strategy,
                candles,
                htf_candles,
                indicator_timeframe_candles,
                additional_instrument_candles or {},
                additional_htf_candles or {},
                additional_indicator_candles or {},
            )
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
    htf_candles: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
    additional_instrument_candles: Mapping[str, Sequence[Candle]],
    additional_htf_candles: Mapping[str, Sequence[Candle]],
    additional_indicator_candles: Mapping[str, Mapping[str, Sequence[Candle]]],
) -> BacktestResult:
    """Verify inputs, evaluate every covered product's trace, and run the shared-cash loop."""
    specification, strategy = _validated_inputs(specification, strategy)
    primary = strategy.instrument.product_id
    product_ids = lockstep_product_ids(strategy)
    if any(
        product_id != primary and product_id not in additional_instrument_candles
        for product_id in product_ids
    ):
        raise BacktestSimulationError(
            "Multi-instrument backtests require additional_instrument_candles "
            "for every extra product."
        )
    candles_by_product: dict[str, Sequence[Candle]] = {
        **additional_instrument_candles,
        primary: candles,
    }
    htf_by_product: dict[str, Sequence[Candle]] = {**additional_htf_candles, primary: htf_candles}
    extra_by_product: dict[str, Mapping[str, Sequence[Candle]] | None] = {
        **additional_indicator_candles,
        primary: indicator_timeframe_candles,
    }
    traces: dict[str, SignalTrace] = {}
    for product_id in product_ids:
        try:
            traces[product_id] = evaluate_signal_trace(
                specification,
                strategy,
                candles_by_product[product_id],
                htf_by_product.get(product_id, ()),
                extra_by_product.get(product_id),
            )
        except SignalEvaluationError as error:
            raise BacktestSimulationError(
                "Backtest signal inputs could not be verified."
            ) from error
    return _simulate_books(specification, strategy, candles_by_product, traces)


def _simulate_books(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles_by_product: Mapping[str, Sequence[Candle]],
    traces: Mapping[str, SignalTrace],
) -> BacktestResult:
    """Run every product book in lexicographic order on each shared bar with one quote book."""
    interval = _bar_interval(specification, strategy)
    bar = interval.duration
    product_ids = lockstep_product_ids(strategy)
    books = {
        product_id: _Book(
            product_id=product_id,
            candle_by_start=_candle_map(specification, candles_by_product[product_id], interval),
            records={record.candle_starts_at: record for record in traces[product_id].records},
        )
        for product_id in product_ids
    }
    costs = _Costs(
        maker_fee_rate=Decimal(specification.costs.maker_fee_rate),
        taker_fee_rate=Decimal(specification.costs.taker_fee_rate),
        slippage_bps=Decimal(specification.costs.fixed_slippage_bps),
        fill_model=FillModel(Decimal(specification.costs.spread_bps)),
    )
    initial_cash = Decimal(specification.capital.initial_quote_balance)
    cash = initial_cash
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    evaluation_span = specification.evaluation.ends_at - specification.evaluation.starts_at
    evaluation_bars = int(evaluation_span / bar)
    max_books = strategy.portfolio_limits.max_concurrent_positions

    for offset in range(evaluation_bars):
        starts_at = specification.evaluation.starts_at + bar * offset
        for product_id in product_ids:
            book = books[product_id]
            cash = _process_bar(
                book,
                book.candle_by_start[starts_at],
                offset=offset,
                strategy=strategy,
                costs=costs,
                cash=cash,
                bar_duration=bar,
                trades=trades,
            )
            _maybe_rest_entry(
                book,
                book.records[starts_at],
                strategy=strategy,
                costs=costs,
                cash=cash,
                limit_price=book.candle_by_start[starts_at].close,
                may_open_book=sum(1 for item in books.values() if item.is_open) < max_books,
            )
        equity_curve.append(
            _equity_point(
                starts_at,
                cash,
                [books[product_id] for product_id in product_ids],
                costs.fill_model,
                use_open=False,
            )
        )

    ends_at = specification.evaluation.ends_at
    for product_id in product_ids:
        book = books[product_id]
        book.pending = None
        if book.position is None:
            continue
        end_candle = book.candle_by_start[ends_at]
        trade, cash = _close_position(
            book.position,
            end_candle,
            cash=cash,
            quote=_taker_exit_quote(book.position, end_candle.open, costs),
            reason="evaluation_end",
            fee_rate=costs.taker_fee_rate,
            bar_duration=bar,
        )
        trades.append(trade)
        book.position = None
    equity_curve.append(
        _equity_point(
            ends_at,
            cash,
            [books[product_id] for product_id in product_ids],
            costs.fill_model,
            use_open=True,
        )
    )

    return BacktestResult(
        schema_version="1.0",
        engine=BACKTEST_ENGINE,
        run_fingerprint=research_run_fingerprint(specification),
        strategy_fingerprint=specification.strategy_fingerprint,
        dataset_fingerprint=specification.dataset_fingerprint,
        signal_trace_fingerprint=signal_trace_fingerprint(traces[strategy.instrument.product_id]),
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        summary=_summary(
            initial_cash,
            cash,
            trades,
            equity_curve,
            include_spread_cost=costs.fill_model.spread_stressed,
            evaluation_bars=evaluation_bars,
            validity_limits=collect_backtest_validity_limits(strategy),
        ),
    )


def _process_bar(
    book: _Book,
    candle: Candle,
    *,
    offset: int,
    strategy: StrategyDefinition,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
    trades: list[BacktestTrade],
) -> Decimal:
    """Match the resting entry, then the resting take-profit, then manage the open position."""
    if book.cooldown_bars > 0:
        book.cooldown_bars -= 1
    cash = _match_entry(book, candle, offset=offset, strategy=strategy, costs=costs, cash=cash)
    trade, cash = _match_take_profit(
        book, candle, costs=costs, cash=cash, bar_duration=bar_duration
    )
    if trade is None:
        trade, cash = _manage_position(
            book,
            candle,
            offset=offset,
            strategy=strategy,
            costs=costs,
            cash=cash,
            bar_duration=bar_duration,
        )
    if trade is not None:
        trades.append(trade)
        book.cooldown_bars = strategy.entry.cooldown_bars
    return cash


def _match_entry(
    book: _Book,
    candle: Candle,
    *,
    offset: int,
    strategy: StrategyDefinition,
    costs: _Costs,
    cash: Decimal,
) -> Decimal:
    """Fill a resting limit when the closed bar trades through, else wait, cancel, or reprice."""
    pending = book.pending
    if pending is None:
        return cash
    if book.position is not None and not pending.is_pyramid_add:
        return cash
    traded_through = (
        candle.high >= pending.limit_price
        if pending.side == "short"
        else candle.low <= pending.limit_price
    )
    if traded_through:
        book.pending = None
        if book.position is not None:
            book.position, cash = _scale_in(pending, position=book.position, cash=cash, costs=costs)
            return cash
        book.position, cash = _open_position(
            pending, candle, cash=cash, entry_bar_index=offset, costs=costs
        )
        return cash
    waited = pending.waited_bars + 1
    if waited < strategy.execution.max_entry_wait_bars:
        book.pending = replace(pending, waited_bars=waited)
        return cash
    if strategy.execution.on_unfilled_entry == "reprice":
        book.pending = replace(pending, limit_price=candle.close, waited_bars=0)
        return cash
    book.pending = None
    book.cooldown_bars = max(strategy.entry.cooldown_bars, 1)
    return cash


def _match_take_profit(
    book: _Book,
    candle: Candle,
    *,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
) -> tuple[BacktestTrade | None, Decimal]:
    """Fill a resting take-profit at the target, as a maker, when a later bar touches it."""
    position = book.position
    if position is None or not position.take_profit_resting:
        return None, cash
    hit = (
        candle.low <= position.target_price
        if position.side == "short"
        else candle.high >= position.target_price
    )
    if not hit:
        return None, cash
    trade, cash = _close_position(
        position,
        candle,
        cash=cash,
        quote=costs.fill_model.maker(position.target_price),
        reason="take_profit",
        fee_rate=costs.maker_fee_rate,
        bar_duration=bar_duration,
    )
    book.position = None
    return trade, cash


def _manage_position(
    book: _Book,
    candle: Candle,
    *,
    offset: int,
    strategy: StrategyDefinition,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
) -> tuple[BacktestTrade | None, Decimal]:
    """Check the entering stop, ratchet the trail, time-exit at close, then rest take-profit."""
    position = book.position
    if position is None:
        return None, cash
    if _stop_hit(position, candle, costs.fill_model):
        trade, cash = _close_position(
            position,
            candle,
            cash=cash,
            quote=_taker_exit_quote(position, _stop_reference(position, candle, costs), costs),
            reason="stop_loss",
            fee_rate=costs.taker_fee_rate,
            bar_duration=bar_duration,
        )
        book.position = None
        return trade, cash
    position = _trail_position(
        position,
        candle,
        strategy=strategy,
        record=book.records.get(candle.starts_at),
        is_fill_bar=offset == position.entered_bar_index,
    )
    if offset - position.entered_bar_index >= strategy.exits.time_exit.max_bars_held:
        trade, cash = _close_position(
            position,
            candle,
            cash=cash,
            quote=_taker_exit_quote(position, candle.close, costs),
            reason="time_exit",
            fee_rate=costs.taker_fee_rate,
            bar_duration=bar_duration,
        )
        book.position = None
        return trade, cash
    book.position = replace(position, take_profit_resting=True)
    return None, cash


def _stop_hit(position: _Position, candle: Candle, fill_model: FillModel) -> bool:
    """Return whether the bar's executable extreme trades through the working stop."""
    if position.side == "short":
        return fill_model.ask(candle.high) >= position.stop_price
    return fill_model.bid(candle.low) <= position.stop_price


def _stop_reference(position: _Position, candle: Candle, costs: _Costs) -> Decimal:
    """Return the raw reference price of a stop exit, gapping to the adverse open."""
    if position.side == "short":
        return max(candle.open, costs.fill_model.raw_for_ask(position.stop_price))
    return min(candle.open, costs.fill_model.raw_for_bid(position.stop_price))


def _taker_exit_quote(position: _Position, reference_price: Decimal, costs: _Costs) -> FillQuote:
    """Price one marketable closing leg: sell longs at bid, cover shorts at ask, with slippage."""
    if position.side == "short":
        return costs.fill_model.taker_buy(reference_price, costs.slippage_bps)
    return costs.fill_model.taker_sell(reference_price, costs.slippage_bps)


def _maybe_rest_entry(
    book: _Book,
    record: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    costs: _Costs,
    cash: Decimal,
    limit_price: Decimal,
    may_open_book: bool,
) -> None:
    """Rest a post-only entry at the signal bar close when matched, off cooldown, and allowed."""
    if book.pending is not None or book.cooldown_bars > 0:
        return
    if record.entry_condition is not EntryConditionOutcome.MATCHED:
        return
    if book.position is None:
        if may_open_book:
            book.pending = _size_entry(
                record,
                strategy=strategy,
                cash=cash,
                limit_price=limit_price,
                maker_fee_rate=costs.maker_fee_rate,
            )
        return
    if can_pyramid_add(
        strategy=strategy,
        side=book.position.side,
        entry_price=Decimal(book.position.entry.price),
        mark=limit_price,
        add_count=book.position.add_count,
    ):
        book.pending = _size_pyramid_add(
            record,
            strategy=strategy,
            cash=cash,
            limit_price=limit_price,
            maker_fee_rate=costs.maker_fee_rate,
            position=book.position,
        )


def _bounded_notional(
    strategy: StrategyDefinition,
    *,
    cash: Decimal,
    stop_distance: Decimal,
    limit_price: Decimal,
    maker_fee_rate: Decimal,
) -> Decimal | None:
    """Return ATR-risk notional bounded by strategy, exposure, and cash limits, if tradable."""
    risk_quantity = cash * Decimal(strategy.sizing.risk_fraction) / stop_distance
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        cash / (Decimal("1") + maker_fee_rate),
    )
    notional = min(risk_quantity * limit_price, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return None
    return notional


def _size_entry(
    signal: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    limit_price: Decimal,
    maker_fee_rate: Decimal,
) -> _PendingEntry | None:
    """Size a resting entry at the signal close using ATR risk, without filling yet."""
    atr = _indicator_value(signal, strategy.exits.initial_stop.atr_indicator)
    stop_distance = atr * Decimal(strategy.exits.initial_stop.multiple)
    if stop_distance <= 0 or limit_price <= 0:
        return None
    side: PositionSide = strategy.entry.side
    reward = stop_distance * Decimal(strategy.exits.take_profit.multiple)
    if side == "short":
        stop_price = limit_price + stop_distance
        target_price = limit_price - reward
        if target_price <= 0:
            return None
    else:
        stop_price = limit_price - stop_distance
        if stop_price <= 0:
            return None
        target_price = limit_price + reward
    notional = _bounded_notional(
        strategy,
        cash=cash,
        stop_distance=stop_distance,
        limit_price=limit_price,
        maker_fee_rate=maker_fee_rate,
    )
    if notional is None:
        return None
    return _PendingEntry(
        signal=signal,
        limit_price=limit_price,
        quantity=notional / limit_price,
        stop_price=stop_price,
        target_price=target_price,
        waited_bars=0,
        side=side,
    )


def _size_pyramid_add(
    signal: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    limit_price: Decimal,
    maker_fee_rate: Decimal,
    position: _Position,
) -> _PendingEntry | None:
    """Size a same-side add against the existing stop without worsening the target."""
    stop_distance = (
        limit_price - position.stop_price
        if position.side == "long"
        else position.stop_price - limit_price
    )
    if stop_distance <= 0 or limit_price <= 0 or cash <= 0:
        return None
    notional = _bounded_notional(
        strategy,
        cash=cash,
        stop_distance=stop_distance,
        limit_price=limit_price,
        maker_fee_rate=maker_fee_rate,
    )
    if notional is None:
        return None
    return _PendingEntry(
        signal=signal,
        limit_price=limit_price,
        quantity=notional / limit_price,
        stop_price=position.stop_price,
        target_price=position.target_price,
        waited_bars=0,
        side=position.side,
        is_pyramid_add=True,
    )


def _open_position(
    pending: _PendingEntry,
    candle: Candle,
    *,
    cash: Decimal,
    entry_bar_index: int,
    costs: _Costs,
) -> tuple[_Position | None, Decimal]:
    """Fill the resting limit at the posted price with the published maker fee."""
    short = pending.side == "short"
    quote = costs.fill_model.maker(pending.limit_price)
    notional = pending.quantity * quote.price
    fee = notional * costs.maker_fee_rate
    if not short and notional + fee > cash:
        return None, cash
    entry = BacktestFill(
        candle_starts_at=candle.starts_at,
        price=canonical_decimal(quote.price),
        quantity=canonical_decimal(pending.quantity),
        notional=canonical_decimal(notional),
        fee=canonical_decimal(fee),
        fee_rate=canonical_decimal(costs.maker_fee_rate),
    )
    next_cash = cash + notional - fee if short else cash - notional - fee
    return (
        _Position(
            entry=entry,
            stop_price=pending.stop_price,
            target_price=pending.target_price,
            entered_bar_index=entry_bar_index,
            side=pending.side,
        ),
        next_cash,
    )


def _scale_in(
    pending: _PendingEntry,
    *,
    position: _Position,
    cash: Decimal,
    costs: _Costs,
) -> tuple[_Position, Decimal]:
    """VWAP a same-side maker add onto the open book without worsening stop or target."""
    short = pending.side == "short"
    quote = costs.fill_model.maker(pending.limit_price)
    add_quantity = pending.quantity
    notional = add_quantity * quote.price
    fee = notional * costs.maker_fee_rate
    if not short and notional + fee > cash:
        return position, cash
    quantity = Decimal(position.entry.quantity) + add_quantity
    entry_price = (
        Decimal(position.entry.price) * Decimal(position.entry.quantity)
        + quote.price * add_quantity
    ) / quantity
    entry = BacktestFill(
        candle_starts_at=position.entry.candle_starts_at,
        price=canonical_decimal(entry_price),
        quantity=canonical_decimal(quantity),
        notional=canonical_decimal(quantity * entry_price),
        fee=canonical_decimal(Decimal(position.entry.fee) + fee),
        fee_rate=position.entry.fee_rate,
    )
    next_cash = cash + notional - fee if short else cash - notional - fee
    return replace(position, entry=entry, add_count=position.add_count + 1), next_cash


def _close_position(
    position: _Position,
    candle: Candle,
    *,
    cash: Decimal,
    quote: FillQuote,
    reason: ExitReason,
    fee_rate: Decimal,
    bar_duration: timedelta,
) -> tuple[BacktestTrade, Decimal]:
    """Apply one covering fill, fee, cash transition, and exact complete-trade evidence."""
    short = position.side == "short"
    quantity = Decimal(position.entry.quantity)
    exit_notional = quantity * quote.price
    exit_fee = exit_notional * fee_rate
    exit_fill = BacktestExitFill(
        candle_starts_at=candle.starts_at,
        price=canonical_decimal(quote.price),
        quantity=position.entry.quantity,
        notional=canonical_decimal(exit_notional),
        fee=canonical_decimal(exit_fee),
        fee_rate=canonical_decimal(fee_rate),
        reason=reason,
        **_fill_evidence(quote),
    )
    entry_notional = Decimal(position.entry.notional)
    entry_fee = Decimal(position.entry.fee)
    if short:
        net_pnl = entry_notional - entry_fee - exit_notional - exit_fee
        gross_pnl = entry_notional - exit_notional
        next_cash = cash - exit_notional - exit_fee
    else:
        net_pnl = exit_notional - exit_fee - entry_notional - entry_fee
        gross_pnl = exit_notional - entry_notional
        next_cash = cash + exit_notional - exit_fee
    return (
        BacktestTrade(
            entry=position.entry,
            exit=exit_fill,
            gross_pnl=canonical_decimal(gross_pnl),
            net_pnl=canonical_decimal(net_pnl),
            holding_bars=_holding_bars(
                position.entry.candle_starts_at, candle.starts_at, bar_duration
            ),
        ),
        next_cash,
    )


def _fill_evidence(quote: FillQuote) -> _FillEvidence:
    """Record executable-side spread evidence only for spread-stressed taker fills."""
    if quote.executable_side is None:
        return {}
    return {
        "reference_price": canonical_decimal(quote.reference_price),
        "executable_side": quote.executable_side,
        "spread_cost": canonical_decimal(quote.spread_cost),
    }


def _trail_position(
    position: _Position,
    candle: Candle,
    *,
    strategy: StrategyDefinition,
    record: SignalTraceRecord | None,
    is_fill_bar: bool,
) -> _Position:
    """Advance an ATR trailing stop after the fill bar; no-op when disabled."""
    policy = atr_trailing_stop(strategy.exits)
    if policy is None:
        return position
    atr = _optional_indicator_value(record, policy.atr_indicator)
    if position.side == "short":
        state = ratcheted_short_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_low=candle.low,
            atr=atr,
            multiple=Decimal(policy.multiple),
            price_increment=None,
            ratchet=not is_fill_bar,
        )
    else:
        state = ratcheted_long_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_high=candle.high,
            atr=atr,
            multiple=Decimal(policy.multiple),
            price_increment=None,
            ratchet=not is_fill_bar,
        )
    if state.stop_price == position.stop_price and state.trail_extreme == position.trail_extreme:
        return position
    return replace(position, stop_price=state.stop_price, trail_extreme=state.trail_extreme)


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
    if strategy_fingerprint(validated_strategy) != validated_specification.strategy_fingerprint:
        raise BacktestSimulationError("Backtest strategy identity failed verification.")
    return validated_specification, validated_strategy


def _candle_map(
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
    interval: CandleInterval,
) -> Mapping[datetime, Candle]:
    """Require exactly one well-formed candle from warmup through the terminal boundary bar."""
    starts_at = specification.warmup.starts_at
    bar = interval.duration
    try:
        ends_at = specification.evaluation.ends_at + bar
        for candle in candles:
            validate_candle_timestamp(candle.starts_at)
        selected = tuple(candle for candle in candles if starts_at <= candle.starts_at < ends_at)
        for candle in selected:
            validate_candle_values(candle)
        expected_count = int((ends_at - starts_at) / bar)
        mapped = {candle.starts_at: candle for candle in selected}
        expected_starts = tuple(starts_at + bar * offset for offset in range(expected_count))
    except CandleQualityError as error:
        message = "Backtest candles have invalid timestamps or values."
        raise BacktestSimulationError(message) from error
    except OverflowError as error:
        message = "Backtest candle coverage exceeds the representable timestamp range."
        raise BacktestSimulationError(message) from error
    except (AttributeError, TypeError, ValueError) as error:
        message = "Backtest candles have invalid timestamps or values."
        raise BacktestSimulationError(message) from error
    if len(selected) != expected_count:
        raise BacktestSimulationError("Backtest candle coverage is incomplete or duplicated.")
    if len(mapped) != expected_count:
        raise BacktestSimulationError("Backtest candle coverage is duplicated.")
    for expected_start in expected_starts:
        candle = mapped.get(expected_start)
        if candle is None or candle.open <= 0 or candle.high < candle.low:
            raise BacktestSimulationError("Backtest candles are not valid contiguous OHLC bars.")
    return mapped


def _indicator_value(record: SignalTraceRecord, indicator_id: str) -> Decimal:
    """Load the exact ATR value that was available when the entry signal closed."""
    value = _optional_indicator_value(record, indicator_id)
    if value is None:
        raise BacktestSimulationError("Backtest entry signal lacks its required ATR value.")
    return value


def _optional_indicator_value(
    record: SignalTraceRecord | None, indicator_id: str
) -> Decimal | None:
    """Return a named indicator value when present, otherwise None."""
    if record is None:
        return None
    for value in record.indicator_values:
        if value.indicator_id == indicator_id and value.value is not None:
            return Decimal(value.value)
    return None


def _summary(
    initial_cash: Decimal,
    final_cash: Decimal,
    trades: Sequence[BacktestTrade],
    equity_curve: Sequence[EquityPoint],
    *,
    include_spread_cost: bool,
    evaluation_bars: int,
    validity_limits: tuple[ResearchValidityLimitCode, ...],
) -> BacktestSummary:
    """Calculate only exact deterministic ledger and equity statistics."""
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
    )


def _bar_interval(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
) -> CandleInterval:
    """Require the run windows and published strategy to share one supported bar duration."""
    strategy_interval = parse_candle_interval(strategy.timeframe)
    spec_interval = specification_bar_interval(specification)
    if strategy_interval is not spec_interval:
        raise BacktestSimulationError(
            "Backtest timeframe does not match the strategy and run windows."
        )
    span = specification.evaluation.ends_at - specification.evaluation.starts_at
    if span < strategy_interval.duration or span % strategy_interval.duration != timedelta(0):
        raise BacktestSimulationError(
            "Backtest evaluation window is not a positive multiple of the strategy timeframe."
        )
    return strategy_interval


def _holding_bars(entry: datetime, exit_: datetime, bar_duration: timedelta) -> int:
    """Return exact whole bars between modeled entry and exit boundaries."""
    return int((exit_ - entry) / bar_duration)
