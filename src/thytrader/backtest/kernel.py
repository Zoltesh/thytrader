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
4. On later bars the stop is checked first against the bar extreme: when one bar touches
   both the stop and the resting take-profit, the stop wins (the candle cannot say which
   traded first, so the conservative exit is assumed; paper does the same). Otherwise a
   touched take-profit fills at the target with the maker fee, the ATR trail ratchets,
   a matched ``exits.signal_exit`` rule sells at the close as a taker (ADR 0093; never on
   the fill bar), and a reached ``max_bars_held`` time exit sells at the close as a taker.
5. Equity marks at each evaluation close. The bar at ``evaluation.ends_at`` only
   liquidates open inventory at its **open** as a taker (``evaluation_end``); no entry,
   take-profit, or stop is processed there.

Futures runs (ADR 0128) keep this loop and ledger with whole-contract sizing under margin
bounds, a liquidation check before the stop, hourly funding at the bar close, and a
dated-contract flatten; ``kernel_futures`` holds those rules and spot runs never reach them.

Multi-instrument documents evaluate covered products in lexicographic ``product_id``
order on each shared bar against one quote book. The optional ``costs.spread_bps``
stress applies half the spread to every taker leg, to stop triggers, and to open-position
marks; maker fills stay at their limit.

``simulate_backtest`` and ``simulate_backtest_with_diagnostics`` are the entry points;
this module verifies inputs and runs the shared-cash bar loop. Simulation state,
the bar grid, entries, exits, fills, ATR reads, and result assembly live in the
``kernel_*`` sibling modules; names other modules import from here are re-exported
(``__all__``).
"""

from __future__ import annotations

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
from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.backtest.broker import FillModel
from thytrader.backtest.kernel_bars import _bar_interval, _candle_map
from thytrader.backtest.kernel_entries import _match_entry, _maybe_rest_entry, _size_entry
from thytrader.backtest.kernel_exits import (
    _manage_position,
    _match_take_profit,
    _stop_out,
    _taker_exit_quote,
)
from thytrader.backtest.kernel_fills import _close_position
from thytrader.backtest.kernel_futures import (
    _charge_funding,
    _flatten_for_expiry,
    _futures_terms,
    _futures_validity_limits,
    _in_expiry_window,
    _liquidate,
)
from thytrader.backtest.kernel_results import _equity_point, _evaluated_no_trade_bars, _summary
from thytrader.backtest.kernel_state import (
    BacktestSimulationError,
    PositionSide,
    _Book,
    _Costs,
    _FuturesTerms,
    _Tally,
)
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
)
from thytrader.backtest.research_validity import collect_backtest_validity_limits
from thytrader.evaluation.models import (
    BACKTEST_ENGINE,
    ResearchRunSpecification,
    research_run_fingerprint,
)
from thytrader.evaluation.signal_evaluator import SignalEvaluationError, evaluate_signal_trace
from thytrader.evaluation.trace import SignalTrace, signal_trace_fingerprint
from thytrader.strategies.models import (
    StrategyDefinition,
    lockstep_product_ids,
    strategy_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime, timedelta

    from thytrader.market_data.models import Candle

__all__ = [
    "_SIMULATION_CONTEXT",
    "BacktestSimulationError",
    "PositionSide",
    "_size_entry",
    "simulate_backtest",
    "simulate_backtest_with_diagnostics",
]


_SIMULATION_CONTEXT = Context(
    prec=64,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


def simulate_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_instrument_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_htf_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_indicator_candles: Mapping[str, Mapping[str, Sequence[Candle]]] | None = None,
    *,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
    funding_rates: Mapping[datetime, Decimal] | None = None,
) -> BacktestResult:
    """Simulate under a private Decimal64 context that ignores ambient process settings."""
    result, _diagnostics = simulate_backtest_with_diagnostics(
        specification,
        strategy,
        candles,
        htf_candles,
        indicator_timeframe_candles,
        additional_instrument_candles,
        additional_htf_candles,
        additional_indicator_candles,
        reference_candles=reference_candles,
        funding_rates=funding_rates,
    )
    return result


def simulate_backtest_with_diagnostics(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_instrument_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_htf_candles: Mapping[str, Sequence[Candle]] | None = None,
    additional_indicator_candles: Mapping[str, Mapping[str, Sequence[Candle]]] | None = None,
    *,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
    funding_rates: Mapping[datetime, Decimal] | None = None,
) -> tuple[BacktestResult, BacktestDiagnostics]:
    """Simulate and also return the entry-funnel counters kept outside the canonical result.

    ``reference_candles`` (ADR 0096) are read-only reference-instrument bars by reference
    id. They feed indicator values only; they never change fill semantics.
    ``funding_rates`` (ADR 0128) are the settled hourly funding rates of a perp run by
    funding hour, exactly the rows whose fingerprint the run bound.
    """
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
                reference_candles or {},
                funding_rates,
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
    reference_candles: Mapping[str, Sequence[Candle]],
    funding_rates: Mapping[datetime, Decimal] | None,
) -> tuple[BacktestResult, BacktestDiagnostics]:
    """Verify inputs, evaluate every covered product's trace, and run the shared-cash loop.

    Every covered product's trace reads the same reference-instrument bars.
    """
    specification, strategy = _validated_inputs(specification, strategy)
    futures = _futures_terms(specification, strategy, funding_rates)
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
                reference_candles=reference_candles,
            )
        except SignalEvaluationError as error:
            raise BacktestSimulationError(
                "Backtest signal inputs could not be verified."
            ) from error
    return _simulate_books(specification, strategy, candles_by_product, traces, futures)


def _simulate_books(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles_by_product: Mapping[str, Sequence[Candle]],
    traces: Mapping[str, SignalTrace],
    futures: _FuturesTerms | None = None,
) -> tuple[BacktestResult, BacktestDiagnostics]:
    """Run every product book in lexicographic order on each shared bar with one quote book."""
    tally = _Tally()
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
        execution_stress=specification.costs.execution_stress,
        futures=futures,
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
                tally=tally,
            )
            _maybe_rest_entry(
                book,
                book.records[starts_at],
                strategy=strategy,
                costs=costs,
                cash=cash,
                limit_price=book.candle_by_start[starts_at].close,
                may_open_book=sum(1 for item in books.values() if item.is_open) < max_books,
                tally=tally,
                expiry_window=_in_expiry_window(costs, starts_at + bar),
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
        if book.pending is not None:
            tally.entries_unfilled_at_end += 1
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
        tally.closed(trade)
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

    result = BacktestResult(
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
            include_funding=futures is not None and futures.perpetual,
            evaluation_bars=evaluation_bars,
            validity_limits=collect_backtest_validity_limits(
                strategy,
                execution_stress=costs.execution_stress,
                no_trade_bars=_evaluated_no_trade_bars(
                    [books[product_id] for product_id in product_ids],
                    specification.evaluation.starts_at,
                    bar,
                    evaluation_bars,
                ),
                futures_limits=_futures_validity_limits(futures, bar),
            ),
        ),
    )
    return result, tally.diagnostics()


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
    tally: _Tally,
) -> Decimal:
    """Match the resting entry, then manage the open position (stop first), then the target.

    When one bar touches both the stop and a resting take-profit, the candle cannot say
    which traded first, so the stop wins: ``_manage_position`` runs before the target.
    Futures (ADR 0128) first flatten a dated contract past its flatten time, check
    liquidation before the stop, and charge funding to a position still open at the close.
    """
    if book.cooldown_bars > 0:
        book.cooldown_bars -= 1
    if _in_expiry_window(costs, candle.starts_at):
        trade, cash = _flatten_for_expiry(
            book, candle, costs=costs, cash=cash, bar_duration=bar_duration, tally=tally
        )
        _record_close(book, trade, strategy=strategy, trades=trades, tally=tally)
        return cash
    cash = _match_entry(
        book, candle, offset=offset, strategy=strategy, costs=costs, cash=cash, tally=tally
    )
    trade, cash = _liquidate(book, candle, costs=costs, cash=cash, bar_duration=bar_duration)
    if trade is None:
        trade, cash = _stop_out(book, candle, costs=costs, cash=cash, bar_duration=bar_duration)
    if trade is None:
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
    _record_close(book, trade, strategy=strategy, trades=trades, tally=tally)
    return _charge_funding(book, candle, costs=costs, cash=cash, bar_duration=bar_duration)


def _record_close(
    book: _Book,
    trade: BacktestTrade | None,
    *,
    strategy: StrategyDefinition,
    trades: list[BacktestTrade],
    tally: _Tally,
) -> None:
    """Record one closed trade and start the strategy's cooldown."""
    if trade is not None:
        trades.append(trade)
        tally.closed(trade)
        book.cooldown_bars = strategy.entry.cooldown_bars


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
