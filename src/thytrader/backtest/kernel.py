"""Deterministic bar-level research simulation without broker or order authority."""

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

from thytrader.backtest.broker import (
    ConstantSpreadFillModel,
    FillModel,
    FillQuote,
    MakerLimitFillModel,
    MarkFillModel,
)
from thytrader.backtest.models import (
    BacktestEngineContract,
    BacktestExitFill,
    BacktestFill,
    BacktestResult,
    BacktestSummary,
    BacktestTrade,
    EquityPoint,
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


class BacktestSimulationError(ValueError):
    """Report a fail-closed simulation input or unsupported execution-condition failure."""


@dataclass(frozen=True, slots=True)
class _PendingEntry:
    """A close-time signal waiting for its sole permitted next-open modeled fill."""

    signal: SignalTraceRecord


@dataclass(frozen=True, slots=True)
class _OpenPosition:
    """The complete long or short position state required for deterministic bar processing."""

    entry: BacktestFill
    stop_price: Decimal
    target_price: Decimal
    entered_bar_index: int
    trail_extreme: Decimal | None = None
    side: Literal["long", "short"] = "long"
    add_count: int = 1


@dataclass(frozen=True, slots=True)
class _PendingMakerEntry:
    """A close-limit entry waiting for a later bar to trade through, matching the worker."""

    signal: SignalTraceRecord
    limit_price: Decimal
    quantity: Decimal
    stop_price: Decimal
    target_price: Decimal
    waited_bars: int
    side: Literal["long", "short"] = "long"
    is_pyramid_add: bool = False


@dataclass(frozen=True, slots=True)
class _MakerPosition:
    """An open position whose take-profit rests only after the fill bar, matching the worker."""

    entry: BacktestFill
    stop_price: Decimal
    target_price: Decimal
    entered_bar_index: int
    take_profit_resting: bool
    trail_extreme: Decimal | None = None
    side: Literal["long", "short"] = "long"
    add_count: int = 1


@dataclass(slots=True)
class _MakerRuntime:
    """Mutable maker-loop cash, pending order, position, and unfilled cooldown."""

    cash: Decimal
    pending: _PendingMakerEntry | None
    position: _MakerPosition | None
    cooldown_bars: int


@dataclass(slots=True)
class _LockstepBook:
    """Per-product taker state for a shared-cash multi-instrument simulation."""

    product_id: str
    candle_by_start: Mapping[datetime, Candle]
    evaluation_records: dict[datetime, SignalTraceRecord]
    pending: _PendingEntry | None = None
    position: _OpenPosition | None = None


class _FillEvidence(TypedDict, total=False):
    """Optional V2-only fill fields omitted from legacy V1 canonical documents."""

    reference_price: str
    executable_side: Literal["ask", "bid", "mark"]
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
                additional_instrument_candles,
                additional_htf_candles,
                additional_indicator_candles,
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
    additional_instrument_candles: Mapping[str, Sequence[Candle]] | None,
    additional_htf_candles: Mapping[str, Sequence[Candle]] | None,
    additional_indicator_candles: Mapping[str, Mapping[str, Sequence[Candle]]] | None,
) -> BacktestResult:
    """Simulate one published strategy with next-open taker fills and conservative OHLC exits."""
    specification, strategy = _validated_inputs(specification, strategy)
    extra = additional_instrument_candles or {}
    if strategy.additional_instruments:
        missing = [
            product_id
            for product_id in lockstep_product_ids(strategy)
            if product_id != strategy.instrument.product_id and product_id not in extra
        ]
        if missing:
            raise BacktestSimulationError(
                "Multi-instrument backtests require additional_instrument_candles "
                "for every extra product."
            )
        return _simulate_lockstep_backtest(
            specification,
            strategy,
            candles,
            htf_candles,
            indicator_timeframe_candles,
            extra,
            additional_htf_candles or {},
            additional_indicator_candles or {},
        )
    if _backtest_contract(specification) == "thytrader-bar-backtest-v3":
        return _simulate_maker_backtest(
            specification, strategy, candles, htf_candles, indicator_timeframe_candles
        )
    return _simulate_single_taker_backtest(
        specification, strategy, candles, htf_candles, indicator_timeframe_candles
    )


def _simulate_single_taker_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
) -> BacktestResult:
    """Simulate one product with next-open taker fills and conservative OHLC exits."""
    interval = _bar_interval(specification, strategy)
    bar = interval.duration
    fill_model = _fill_model(specification)
    try:
        trace = evaluate_signal_trace(
            specification, strategy, candles, htf_candles, indicator_timeframe_candles
        )
    except SignalEvaluationError as error:
        raise BacktestSimulationError("Backtest signal inputs could not be verified.") from error
    candle_by_start = _candle_map(specification, candles, interval)
    cash = Decimal(specification.capital.initial_quote_balance)
    initial_cash = cash
    pending: _PendingEntry | None = None
    position: _OpenPosition | None = None
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    evaluation_records = {record.candle_starts_at: record for record in trace.records}
    evaluation_span = specification.evaluation.ends_at - specification.evaluation.starts_at
    evaluation_bars = int(evaluation_span / bar)
    taker_fee_rate = Decimal(specification.costs.taker_fee_rate)
    slippage_bps = Decimal(specification.costs.fixed_slippage_bps)

    for offset in range(evaluation_bars + 1):
        starts_at = specification.evaluation.starts_at + bar * offset
        candle = candle_by_start[starts_at]
        if pending is not None:
            position, cash = _fill_taker_pending(
                pending,
                candle,
                position=position,
                strategy=strategy,
                cash=cash,
                entry_bar_index=offset,
                taker_fee_rate=taker_fee_rate,
                slippage_bps=slippage_bps,
                fill_model=fill_model,
            )
            pending = None
        if position is not None and offset < evaluation_bars:
            position = _trail_open_position(
                position,
                candle,
                strategy=strategy,
                record=evaluation_records[starts_at],
                is_fill_bar=offset == position.entered_bar_index,
            )
            trade, cash = _close_if_required(
                position,
                candle,
                cash=cash,
                bar_index=offset,
                taker_fee_rate=taker_fee_rate,
                slippage_bps=slippage_bps,
                max_bars_held=strategy.exits.time_exit.max_bars_held,
                fill_model=fill_model,
                bar_duration=bar,
            )
            if trade is not None:
                trades.append(trade)
                position = None
        if offset < evaluation_bars:
            pending = _queue_taker_signal(
                position=position,
                pending=pending,
                record=evaluation_records[starts_at],
                strategy=strategy,
                mark=candle.close,
                allow_new_book=True,
            )
        mark_reference = candle.open if offset == evaluation_bars else candle.close
        equity_curve.append(
            _equity_point(
                candle.starts_at,
                cash,
                position,
                _account_mark(fill_model, mark_reference, position),
            )
        )

    if position is not None:
        forced_exit, cash = _close_position(
            position,
            candle_by_start[specification.evaluation.ends_at],
            cash=cash,
            bar_index=evaluation_bars,
            raw_exit_price=candle_by_start[specification.evaluation.ends_at].open,
            reason="evaluation_end",
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
            fill_model=fill_model,
            bar_duration=bar,
        )
        trades.append(forced_exit)
        equity_curve[-1] = _equity_point(
            forced_exit.exit.candle_starts_at,
            cash,
            None,
            Decimal(forced_exit.exit.price),
        )

    engine_contract_version = _backtest_contract(specification)
    return BacktestResult(
        schema_version="1.0",
        engine_contract_version=engine_contract_version,
        broker=specification.broker,
        run_fingerprint=research_run_fingerprint(specification),
        strategy_fingerprint=specification.strategy_fingerprint,
        dataset_fingerprint=specification.dataset_fingerprint,
        signal_trace_fingerprint=signal_trace_fingerprint(trace),
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        summary=_summary(
            initial_cash,
            cash,
            trades,
            equity_curve,
            include_spread_cost=specification.broker is not None,
        ),
    )


def _fill_taker_pending(
    pending: _PendingEntry,
    candle: Candle,
    *,
    position: _OpenPosition | None,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_bar_index: int,
    taker_fee_rate: Decimal,
    slippage_bps: Decimal,
    fill_model: FillModel,
) -> tuple[_OpenPosition | None, Decimal]:
    """Open or scale in at the pending signal's next-open fill."""
    if position is not None:
        return _scale_in_open_position(
            pending,
            candle,
            position=position,
            strategy=strategy,
            cash=cash,
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
            fill_model=fill_model,
        )
    return _open_position(
        pending,
        candle,
        strategy=strategy,
        cash=cash,
        entry_bar_index=entry_bar_index,
        taker_fee_rate=taker_fee_rate,
        slippage_bps=slippage_bps,
        fill_model=fill_model,
    )


def _queue_taker_signal(
    *,
    position: _OpenPosition | None,
    pending: _PendingEntry | None,
    record: SignalTraceRecord,
    strategy: StrategyDefinition,
    mark: Decimal,
    allow_new_book: bool,
) -> _PendingEntry | None:
    """Queue a next-open entry or same-side add when the close-time signal matches."""
    if record.entry_condition is not EntryConditionOutcome.MATCHED:
        return pending
    if position is None:
        if allow_new_book:
            return _PendingEntry(signal=record)
        return pending
    if pending is None and can_pyramid_add(
        strategy=strategy,
        side=position.side,
        entry_price=Decimal(position.entry.price),
        mark=mark,
        add_count=position.add_count,
    ):
        return _PendingEntry(signal=record)
    return pending


def _simulate_lockstep_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
    additional_instrument_candles: Mapping[str, Sequence[Candle]],
    additional_htf_candles: Mapping[str, Sequence[Candle]],
    additional_indicator_candles: Mapping[str, Mapping[str, Sequence[Candle]]],
) -> BacktestResult:
    """Evaluate covered products in lex order on each shared bar with one quote book."""
    primary = strategy.instrument.product_id
    candles_by_product: dict[str, Sequence[Candle]] = {
        primary: candles,
        **additional_instrument_candles,
    }
    htf_by_product: dict[str, Sequence[Candle]] = {primary: htf_candles, **additional_htf_candles}
    extra_by_product: dict[str, Mapping[str, Sequence[Candle]] | None] = {
        primary: indicator_timeframe_candles,
        **{
            product_id: additional_indicator_candles.get(product_id)
            for product_id in additional_instrument_candles
        },
    }
    traces: dict[str, SignalTrace] = {}
    for product_id in lockstep_product_ids(strategy):
        product_candles = candles_by_product.get(product_id)
        if product_candles is None:
            raise BacktestSimulationError(
                f"Multi-instrument backtests require candles for {product_id}."
            )
        try:
            traces[product_id] = evaluate_signal_trace(
                specification,
                strategy,
                product_candles,
                htf_by_product.get(product_id, ()),
                extra_by_product.get(product_id),
            )
        except SignalEvaluationError as error:
            raise BacktestSimulationError(
                "Backtest signal inputs could not be verified."
            ) from error
    if _backtest_contract(specification) == "thytrader-bar-backtest-v3":
        return _simulate_lockstep_maker_backtest(
            specification,
            strategy,
            candles_by_product,
            traces,
        )
    return _simulate_lockstep_taker_backtest(
        specification,
        strategy,
        candles_by_product,
        traces,
    )


def _simulate_lockstep_taker_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles_by_product: Mapping[str, Sequence[Candle]],
    traces: Mapping[str, SignalTrace],
) -> BacktestResult:
    """Shared-cash V1/V2 lockstep over lexicographic product order."""
    interval = _bar_interval(specification, strategy)
    bar = interval.duration
    fill_model = _fill_model(specification)
    books = {
        product_id: _LockstepBook(
            product_id=product_id,
            candle_by_start=_candle_map(specification, candles_by_product[product_id], interval),
            evaluation_records={
                record.candle_starts_at: record for record in traces[product_id].records
            },
        )
        for product_id in lockstep_product_ids(strategy)
    }
    cash = Decimal(specification.capital.initial_quote_balance)
    initial_cash = cash
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    evaluation_span = specification.evaluation.ends_at - specification.evaluation.starts_at
    evaluation_bars = int(evaluation_span / bar)
    taker_fee_rate = Decimal(specification.costs.taker_fee_rate)
    slippage_bps = Decimal(specification.costs.fixed_slippage_bps)
    max_books = strategy.portfolio_limits.max_concurrent_positions

    for offset in range(evaluation_bars + 1):
        starts_at = specification.evaluation.starts_at + bar * offset
        for product_id in lockstep_product_ids(strategy):
            book = books[product_id]
            candle = book.candle_by_start[starts_at]
            if book.pending is not None:
                book.position, cash = _fill_taker_pending(
                    book.pending,
                    candle,
                    position=book.position,
                    strategy=strategy,
                    cash=cash,
                    entry_bar_index=offset,
                    taker_fee_rate=taker_fee_rate,
                    slippage_bps=slippage_bps,
                    fill_model=fill_model,
                )
                book.pending = None
            if book.position is not None and offset < evaluation_bars:
                book.position = _trail_open_position(
                    book.position,
                    candle,
                    strategy=strategy,
                    record=book.evaluation_records[starts_at],
                    is_fill_bar=offset == book.position.entered_bar_index,
                )
                trade, cash = _close_if_required(
                    book.position,
                    candle,
                    cash=cash,
                    bar_index=offset,
                    taker_fee_rate=taker_fee_rate,
                    slippage_bps=slippage_bps,
                    max_bars_held=strategy.exits.time_exit.max_bars_held,
                    fill_model=fill_model,
                    bar_duration=bar,
                )
                if trade is not None:
                    trades.append(trade)
                    book.position = None
            if offset < evaluation_bars:
                book.pending = _queue_taker_signal(
                    position=book.position,
                    pending=book.pending,
                    record=book.evaluation_records[starts_at],
                    strategy=strategy,
                    mark=candle.close,
                    allow_new_book=_lockstep_open_book_count(books) < max_books,
                )
        equity_curve.append(
            _lockstep_equity_point(
                starts_at,
                cash,
                tuple(books[product_id].position for product_id in lockstep_product_ids(strategy)),
                tuple(
                    _account_mark(
                        fill_model,
                        books[product_id].candle_by_start[starts_at].open
                        if offset == evaluation_bars
                        else books[product_id].candle_by_start[starts_at].close,
                        books[product_id].position,
                    )
                    for product_id in lockstep_product_ids(strategy)
                ),
            )
        )

    for product_id in lockstep_product_ids(strategy):
        book = books[product_id]
        if book.position is None:
            continue
        end_candle = book.candle_by_start[specification.evaluation.ends_at]
        forced_exit, cash = _close_position(
            book.position,
            end_candle,
            cash=cash,
            bar_index=evaluation_bars,
            raw_exit_price=end_candle.open,
            reason="evaluation_end",
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
            fill_model=fill_model,
            bar_duration=bar,
        )
        trades.append(forced_exit)
        book.position = None
    if equity_curve:
        end_marks = tuple(
            Decimal(books[product_id].candle_by_start[specification.evaluation.ends_at].open)
            for product_id in lockstep_product_ids(strategy)
        )
        equity_curve[-1] = _lockstep_equity_point(
            specification.evaluation.ends_at,
            cash,
            tuple(books[product_id].position for product_id in lockstep_product_ids(strategy)),
            end_marks,
        )

    return BacktestResult(
        schema_version="1.0",
        engine_contract_version=_backtest_contract(specification),
        broker=specification.broker,
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
            include_spread_cost=specification.broker is not None,
        ),
    )


def _simulate_lockstep_maker_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles_by_product: Mapping[str, Sequence[Candle]],
    traces: Mapping[str, SignalTrace],
) -> BacktestResult:
    """Shared-cash V3 lockstep over lexicographic product order."""
    interval = _bar_interval(specification, strategy)
    bar = interval.duration
    fill_model = _fill_model(specification)
    candle_maps = {
        product_id: _candle_map(specification, candles_by_product[product_id], interval)
        for product_id in lockstep_product_ids(strategy)
    }
    records = {
        product_id: {record.candle_starts_at: record for record in traces[product_id].records}
        for product_id in lockstep_product_ids(strategy)
    }
    initial_cash = Decimal(specification.capital.initial_quote_balance)
    cash = initial_cash
    runtimes = {
        product_id: _MakerRuntime(cash=cash, pending=None, position=None, cooldown_bars=0)
        for product_id in lockstep_product_ids(strategy)
    }
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    evaluation_span = specification.evaluation.ends_at - specification.evaluation.starts_at
    evaluation_bars = int(evaluation_span / bar)
    maker_fee_rate = Decimal(specification.costs.maker_fee_rate)
    taker_fee_rate = Decimal(specification.costs.taker_fee_rate)
    max_books = strategy.portfolio_limits.max_concurrent_positions

    for offset in range(evaluation_bars + 1):
        starts_at = specification.evaluation.starts_at + bar * offset
        for product_id in lockstep_product_ids(strategy):
            runtime = runtimes[product_id]
            runtime.cash = cash
            candle = candle_maps[product_id][starts_at]
            if runtime.cooldown_bars > 0:
                runtime.cooldown_bars -= 1
            _match_maker_entry(
                runtime,
                candle,
                offset=offset,
                strategy=strategy,
                maker_fee_rate=maker_fee_rate,
                fill_model=fill_model,
            )
            trade = _match_maker_take_profit(
                runtime,
                candle,
                maker_fee_rate=maker_fee_rate,
                fill_model=fill_model,
                bar_duration=bar,
            )
            if trade is None:
                trade = _manage_maker_position(
                    runtime,
                    candle,
                    offset=offset,
                    strategy=strategy,
                    taker_fee_rate=taker_fee_rate,
                    fill_model=fill_model,
                    bar_duration=bar,
                    record=records[product_id].get(starts_at),
                )
            if trade is not None:
                trades.append(trade)
                runtime.cooldown_bars = strategy.entry.cooldown_bars
            if offset < evaluation_bars:
                _maybe_rest_maker_entry(
                    runtime,
                    records[product_id][starts_at],
                    strategy=strategy,
                    maker_fee_rate=maker_fee_rate,
                    limit_price=candle.close,
                    open_book_count=_lockstep_maker_open_count(runtimes),
                    max_open_books=max_books,
                )
            cash = runtime.cash
        equity_curve.append(
            _lockstep_equity_point(
                starts_at,
                cash,
                tuple(
                    _maker_as_open_position(runtimes[product_id].position)
                    for product_id in lockstep_product_ids(strategy)
                ),
                tuple(
                    _account_mark(
                        fill_model,
                        candle_maps[product_id][starts_at].close,
                        _maker_as_open_position(runtimes[product_id].position),
                    )
                    for product_id in lockstep_product_ids(strategy)
                ),
            )
        )

    for product_id in lockstep_product_ids(strategy):
        runtime = runtimes[product_id]
        if runtime.position is None:
            continue
        runtime.cash = cash
        end_candle = candle_maps[product_id][specification.evaluation.ends_at]
        forced_exit, runtime.cash = _close_maker_position(
            runtime.position,
            end_candle,
            cash=runtime.cash,
            raw_exit_price=end_candle.close,
            reason="evaluation_end",
            fee_rate=taker_fee_rate,
            fill_model=fill_model,
            bar_duration=bar,
        )
        trades.append(forced_exit)
        runtime.position = None
        cash = runtime.cash
    if equity_curve:
        equity_curve[-1] = _lockstep_equity_point(
            specification.evaluation.ends_at,
            cash,
            tuple(
                _maker_as_open_position(runtimes[product_id].position)
                for product_id in lockstep_product_ids(strategy)
            ),
            tuple(
                Decimal(candle_maps[product_id][specification.evaluation.ends_at].close)
                for product_id in lockstep_product_ids(strategy)
            ),
        )

    return BacktestResult(
        schema_version="1.0",
        engine_contract_version="thytrader-bar-backtest-v3",
        broker=specification.broker,
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
            include_spread_cost=False,
        ),
    )


def _lockstep_open_book_count(books: Mapping[str, _LockstepBook]) -> int:
    """Count distinct product books that are open or waiting to open."""
    return sum(
        1
        for book in books.values()
        if book.position is not None or (book.pending is not None and book.position is None)
    )


def _lockstep_maker_open_count(runtimes: Mapping[str, _MakerRuntime]) -> int:
    """Count distinct maker books that are open or waiting to open."""
    return sum(
        1
        for runtime in runtimes.values()
        if runtime.position is not None
        or (runtime.pending is not None and runtime.position is None)
    )


def _lockstep_equity_point(
    starts_at: datetime,
    cash: Decimal,
    positions: Sequence[_OpenPosition | None],
    marks: Sequence[Decimal],
) -> EquityPoint:
    """Mark one shared quote book plus every open product inventory."""
    equity = cash
    open_books = [
        (position, mark)
        for position, mark in zip(positions, marks, strict=True)
        if position is not None
    ]
    for position, mark in open_books:
        quantity = Decimal(position.entry.quantity)
        signed = -quantity if position.side == "short" else quantity
        equity += signed * mark
    if len(open_books) == 1:
        position, mark = open_books[0]
        quantity = Decimal(position.entry.quantity)
        signed = -quantity if position.side == "short" else quantity
        return EquityPoint(
            candle_starts_at=starts_at,
            cash=canonical_decimal(cash),
            base_quantity=canonical_decimal(signed),
            mark_price=canonical_decimal(mark),
            equity=canonical_decimal(equity),
        )
    return EquityPoint(
        candle_starts_at=starts_at,
        cash=canonical_decimal(cash),
        base_quantity="0",
        mark_price=canonical_decimal(marks[0] if marks else Decimal("0")),
        equity=canonical_decimal(equity),
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
    _backtest_contract(validated_specification)
    if strategy_fingerprint(validated_strategy) != validated_specification.strategy_fingerprint:
        raise BacktestSimulationError("Backtest strategy identity failed verification.")
    return validated_specification, validated_strategy


def _backtest_contract(specification: ResearchRunSpecification) -> BacktestEngineContract:
    """Narrow one fully validated research run to an implemented backtest contract."""
    contract = specification.engine_contract_version
    if contract == "thytrader-bar-backtest-v1":
        return contract
    if contract == "thytrader-bar-backtest-v2":
        return contract
    if contract == "thytrader-bar-backtest-v3":
        return contract
    raise BacktestSimulationError("Backtest requires the backtest engine contract.")


def _fill_model(specification: ResearchRunSpecification) -> FillModel:
    """Construct the one immutable pricing model selected by the published run."""
    contract = _backtest_contract(specification)
    if contract == "thytrader-bar-backtest-v1":
        return MarkFillModel()
    if specification.broker is None:
        raise BacktestSimulationError("Backtest broker assumptions are missing.")
    if contract == "thytrader-bar-backtest-v3":
        return MakerLimitFillModel()
    return ConstantSpreadFillModel(Decimal(specification.broker.spread_bps))


def _fill_evidence(quote: FillQuote) -> _FillEvidence:
    """Keep legacy V1 canonical bytes unchanged while recording V2 executable price evidence."""
    if quote.executable_side == "mark":
        return {}
    return {
        "reference_price": canonical_decimal(quote.reference_price),
        "executable_side": quote.executable_side,
        "spread_cost": canonical_decimal(quote.spread_cost),
    }


def _simulate_maker_backtest(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
) -> BacktestResult:
    """Simulate resting close-limit entries, unfilled expiry, and worker-ordered exits."""
    interval = _bar_interval(specification, strategy)
    bar = interval.duration
    fill_model = _fill_model(specification)
    try:
        trace = evaluate_signal_trace(
            specification, strategy, candles, htf_candles, indicator_timeframe_candles
        )
    except SignalEvaluationError as error:
        raise BacktestSimulationError("Backtest signal inputs could not be verified.") from error
    candle_by_start = _candle_map(specification, candles, interval)
    initial_cash = Decimal(specification.capital.initial_quote_balance)
    runtime = _MakerRuntime(cash=initial_cash, pending=None, position=None, cooldown_bars=0)
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    evaluation_records = {record.candle_starts_at: record for record in trace.records}
    evaluation_span = specification.evaluation.ends_at - specification.evaluation.starts_at
    evaluation_bars = int(evaluation_span / bar)
    maker_fee_rate = Decimal(specification.costs.maker_fee_rate)
    taker_fee_rate = Decimal(specification.costs.taker_fee_rate)

    for offset in range(evaluation_bars + 1):
        candle = candle_by_start[specification.evaluation.starts_at + bar * offset]
        if runtime.cooldown_bars > 0:
            runtime.cooldown_bars -= 1
        _match_maker_entry(
            runtime,
            candle,
            offset=offset,
            strategy=strategy,
            maker_fee_rate=maker_fee_rate,
            fill_model=fill_model,
        )
        trade = _match_maker_take_profit(
            runtime,
            candle,
            maker_fee_rate=maker_fee_rate,
            fill_model=fill_model,
            bar_duration=bar,
        )
        if trade is None:
            trade = _manage_maker_position(
                runtime,
                candle,
                offset=offset,
                strategy=strategy,
                taker_fee_rate=taker_fee_rate,
                fill_model=fill_model,
                bar_duration=bar,
                record=evaluation_records.get(candle.starts_at),
            )
        if trade is not None:
            trades.append(trade)
            runtime.cooldown_bars = strategy.entry.cooldown_bars
        if offset < evaluation_bars:
            _maybe_rest_maker_entry(
                runtime,
                evaluation_records[candle.starts_at],
                strategy=strategy,
                maker_fee_rate=maker_fee_rate,
                limit_price=candle.close,
            )
        equity_curve.append(
            _equity_point(
                candle.starts_at,
                runtime.cash,
                _maker_as_open_position(runtime.position),
                _account_mark(fill_model, candle.close, _maker_as_open_position(runtime.position)),
            )
        )

    if runtime.position is not None:
        forced_exit, runtime.cash = _close_maker_position(
            runtime.position,
            candle_by_start[specification.evaluation.ends_at],
            cash=runtime.cash,
            raw_exit_price=candle_by_start[specification.evaluation.ends_at].close,
            reason="evaluation_end",
            fee_rate=taker_fee_rate,
            fill_model=fill_model,
            bar_duration=bar,
        )
        trades.append(forced_exit)
        runtime.position = None
        equity_curve[-1] = _equity_point(
            forced_exit.exit.candle_starts_at,
            runtime.cash,
            None,
            Decimal(forced_exit.exit.price),
        )

    return BacktestResult(
        schema_version="1.0",
        engine_contract_version="thytrader-bar-backtest-v3",
        broker=specification.broker,
        run_fingerprint=research_run_fingerprint(specification),
        strategy_fingerprint=specification.strategy_fingerprint,
        dataset_fingerprint=specification.dataset_fingerprint,
        signal_trace_fingerprint=signal_trace_fingerprint(trace),
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        summary=_summary(
            initial_cash,
            runtime.cash,
            trades,
            equity_curve,
            include_spread_cost=False,
        ),
    )


def _match_maker_entry(
    runtime: _MakerRuntime,
    candle: Candle,
    *,
    offset: int,
    strategy: StrategyDefinition,
    maker_fee_rate: Decimal,
    fill_model: FillModel,
) -> None:
    """Fill a resting limit when the closed bar trades through, else wait, cancel, or reprice."""
    pending = runtime.pending
    if pending is None:
        return
    if runtime.position is not None and not pending.is_pyramid_add:
        return
    traded_through = (
        candle.high >= pending.limit_price
        if pending.side == "short"
        else candle.low <= pending.limit_price
    )
    if traded_through:
        if runtime.position is not None:
            scaled, runtime.cash = _scale_in_maker_position(
                pending,
                candle,
                position=runtime.position,
                cash=runtime.cash,
                maker_fee_rate=maker_fee_rate,
                fill_model=fill_model,
            )
            runtime.pending = None
            runtime.position = scaled
            return
        opened, runtime.cash = _open_maker_position(
            pending,
            candle,
            cash=runtime.cash,
            entry_bar_index=offset,
            maker_fee_rate=maker_fee_rate,
            fill_model=fill_model,
        )
        runtime.pending = None
        runtime.position = opened
        return
    waited = pending.waited_bars + 1
    if waited < strategy.execution.max_entry_wait_bars:
        runtime.pending = replace(pending, waited_bars=waited)
        return
    if strategy.execution.on_unfilled_entry == "reprice":
        runtime.pending = replace(pending, limit_price=candle.close, waited_bars=0)
        return
    runtime.pending = None
    runtime.cooldown_bars = max(strategy.entry.cooldown_bars, 1)


def _match_maker_take_profit(
    runtime: _MakerRuntime,
    candle: Candle,
    *,
    maker_fee_rate: Decimal,
    fill_model: FillModel,
    bar_duration: timedelta,
) -> BacktestTrade | None:
    """Fill a resting take-profit when a later bar trades through the target."""
    position = runtime.position
    if position is None or not position.take_profit_resting:
        return None
    hit = (
        candle.low <= position.target_price
        if position.side == "short"
        else candle.high >= position.target_price
    )
    if not hit:
        return None
    trade, runtime.cash = _close_maker_position(
        position,
        candle,
        cash=runtime.cash,
        raw_exit_price=position.target_price,
        reason="take_profit",
        fee_rate=maker_fee_rate,
        fill_model=fill_model,
        bar_duration=bar_duration,
    )
    runtime.position = None
    return trade


def _manage_maker_position(
    runtime: _MakerRuntime,
    candle: Candle,
    *,
    offset: int,
    strategy: StrategyDefinition,
    taker_fee_rate: Decimal,
    fill_model: FillModel,
    bar_duration: timedelta,
    record: SignalTraceRecord | None,
) -> BacktestTrade | None:
    """Stop on the fill bar, time-exit at close, then rest take-profit for later bars."""
    position = runtime.position
    if position is None:
        return None
    runtime.position = _trail_maker_position(
        position,
        candle,
        strategy=strategy,
        record=record,
        is_fill_bar=offset == position.entered_bar_index,
    )
    position = runtime.position
    bars_held = offset - position.entered_bar_index
    stop_hit = (
        candle.high >= position.stop_price
        if position.side == "short"
        else candle.low <= position.stop_price
    )
    if stop_hit:
        raw_exit = (
            max(candle.open, position.stop_price)
            if position.side == "short"
            else min(candle.open, position.stop_price)
        )
        trade, runtime.cash = _close_maker_position(
            position,
            candle,
            cash=runtime.cash,
            raw_exit_price=raw_exit,
            reason="stop_loss",
            fee_rate=taker_fee_rate,
            fill_model=fill_model,
            bar_duration=bar_duration,
        )
        runtime.position = None
        return trade
    if bars_held >= strategy.exits.time_exit.max_bars_held:
        trade, runtime.cash = _close_maker_position(
            position,
            candle,
            cash=runtime.cash,
            raw_exit_price=candle.close,
            reason="time_exit",
            fee_rate=taker_fee_rate,
            fill_model=fill_model,
            bar_duration=bar_duration,
        )
        runtime.position = None
        return trade
    runtime.position = replace(position, take_profit_resting=True)
    return None


def _maybe_rest_maker_entry(
    runtime: _MakerRuntime,
    record: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    maker_fee_rate: Decimal,
    limit_price: Decimal,
    open_book_count: int | None = None,
    max_open_books: int | None = None,
) -> None:
    """Rest a post-only entry at the signal bar close when flat, off cooldown, and matched."""
    if runtime.pending is not None or runtime.cooldown_bars > 0:
        return
    if record.entry_condition is not EntryConditionOutcome.MATCHED:
        return
    if runtime.position is None:
        if (
            open_book_count is not None
            and max_open_books is not None
            and open_book_count >= max_open_books
        ):
            return
        runtime.pending = _size_maker_entry(
            record,
            strategy=strategy,
            cash=runtime.cash,
            limit_price=limit_price,
            maker_fee_rate=maker_fee_rate,
        )
        return
    if can_pyramid_add(
        strategy=strategy,
        side=runtime.position.side,
        entry_price=Decimal(runtime.position.entry.price),
        mark=limit_price,
        add_count=runtime.position.add_count,
    ):
        runtime.pending = _size_maker_pyramid_add(
            record,
            strategy=strategy,
            cash=runtime.cash,
            limit_price=limit_price,
            maker_fee_rate=maker_fee_rate,
            position=runtime.position,
        )


def _size_maker_entry(
    signal: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    limit_price: Decimal,
    maker_fee_rate: Decimal,
) -> _PendingMakerEntry | None:
    """Size a resting entry at the signal close using ATR risk, without filling yet."""
    atr = _indicator_value(signal, strategy.exits.initial_stop.atr_indicator)
    stop_distance = atr * Decimal(strategy.exits.initial_stop.multiple)
    if stop_distance <= 0 or limit_price <= 0:
        return None
    side: Literal["long", "short"] = strategy.entry.side
    if side == "short":
        stop_price = limit_price + stop_distance
        target_price = limit_price - stop_distance * Decimal(strategy.exits.take_profit.multiple)
        if target_price <= 0:
            return None
    else:
        stop_price = limit_price - stop_distance
        if stop_price <= 0:
            return None
        target_price = limit_price + stop_distance * Decimal(strategy.exits.take_profit.multiple)
    requested_risk = cash * Decimal(strategy.sizing.risk_fraction)
    risk_quantity = requested_risk / stop_distance
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        cash / (Decimal("1") + maker_fee_rate),
    )
    notional = min(risk_quantity * limit_price, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return None
    quantity = notional / limit_price
    return _PendingMakerEntry(
        signal=signal,
        limit_price=limit_price,
        quantity=quantity,
        stop_price=stop_price,
        target_price=target_price,
        waited_bars=0,
        side=side,
    )


def _size_maker_pyramid_add(
    signal: SignalTraceRecord,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    limit_price: Decimal,
    maker_fee_rate: Decimal,
    position: _MakerPosition,
) -> _PendingMakerEntry | None:
    """Size a same-side add against the existing stop without worsening target."""
    stop_distance = (
        limit_price - position.stop_price
        if position.side == "long"
        else position.stop_price - limit_price
    )
    if stop_distance <= 0 or limit_price <= 0 or cash <= 0:
        return None
    requested_risk = cash * Decimal(strategy.sizing.risk_fraction)
    risk_quantity = requested_risk / stop_distance
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        cash / (Decimal("1") + maker_fee_rate),
    )
    notional = min(risk_quantity * limit_price, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return None
    return _PendingMakerEntry(
        signal=signal,
        limit_price=limit_price,
        quantity=notional / limit_price,
        stop_price=position.stop_price,
        target_price=position.target_price,
        waited_bars=0,
        side=position.side,
        is_pyramid_add=True,
    )


def _open_maker_position(
    pending: _PendingMakerEntry,
    candle: Candle,
    *,
    cash: Decimal,
    entry_bar_index: int,
    maker_fee_rate: Decimal,
    fill_model: FillModel,
) -> tuple[_MakerPosition | None, Decimal]:
    """Fill the resting limit at the posted price with the published maker fee."""
    short = pending.side == "short"
    entry_quote = (
        fill_model.sell(pending.limit_price, Decimal("0"))
        if short
        else fill_model.buy(pending.limit_price, Decimal("0"))
    )
    entry_price = entry_quote.price
    notional = pending.quantity * entry_price
    fee = notional * maker_fee_rate
    if not short and notional + fee > cash:
        return None, cash
    entry = BacktestFill(
        candle_starts_at=candle.starts_at,
        price=canonical_decimal(entry_price),
        quantity=canonical_decimal(pending.quantity),
        notional=canonical_decimal(notional),
        fee=canonical_decimal(fee),
        fee_rate=canonical_decimal(maker_fee_rate),
        **_fill_evidence(entry_quote),
    )
    next_cash = cash + notional - fee if short else cash - notional - fee
    return (
        _MakerPosition(
            entry=entry,
            stop_price=pending.stop_price,
            target_price=pending.target_price,
            entered_bar_index=entry_bar_index,
            take_profit_resting=False,
            side=pending.side,
        ),
        next_cash,
    )


def _scale_in_maker_position(
    pending: _PendingMakerEntry,
    candle: Candle,
    *,
    position: _MakerPosition,
    cash: Decimal,
    maker_fee_rate: Decimal,
    fill_model: FillModel,
) -> tuple[_MakerPosition | None, Decimal]:
    """VWAP a same-side maker add onto the open book without worsening stop or target."""
    del candle
    short = pending.side == "short"
    entry_quote = (
        fill_model.sell(pending.limit_price, Decimal("0"))
        if short
        else fill_model.buy(pending.limit_price, Decimal("0"))
    )
    add_price = entry_quote.price
    add_quantity = pending.quantity
    notional = add_quantity * add_price
    fee = notional * maker_fee_rate
    if not short and notional + fee > cash:
        return position, cash
    quantity = Decimal(position.entry.quantity) + add_quantity
    entry_price = (
        Decimal(position.entry.price) * Decimal(position.entry.quantity) + add_price * add_quantity
    ) / quantity
    combined_notional = quantity * entry_price
    combined_fee = Decimal(position.entry.fee) + fee
    entry = BacktestFill(
        candle_starts_at=position.entry.candle_starts_at,
        price=canonical_decimal(entry_price),
        quantity=canonical_decimal(quantity),
        notional=canonical_decimal(combined_notional),
        fee=canonical_decimal(combined_fee),
        fee_rate=position.entry.fee_rate,
        **_fill_evidence(entry_quote),
    )
    next_cash = cash + notional - fee if short else cash - notional - fee
    return (
        replace(
            position,
            entry=entry,
            add_count=position.add_count + 1,
        ),
        next_cash,
    )


def _close_maker_position(
    position: _MakerPosition,
    candle: Candle,
    *,
    cash: Decimal,
    raw_exit_price: Decimal,
    reason: Literal["stop_loss", "take_profit", "time_exit", "evaluation_end"],
    fee_rate: Decimal,
    fill_model: FillModel,
    bar_duration: timedelta,
) -> tuple[BacktestTrade, Decimal]:
    """Close a maker-path position through the shared fill ledger helper."""
    synthetic = _OpenPosition(
        entry=position.entry,
        stop_price=position.stop_price,
        target_price=position.target_price,
        entered_bar_index=position.entered_bar_index,
        side=position.side,
    )
    return _close_position(
        synthetic,
        candle,
        cash=cash,
        bar_index=position.entered_bar_index,
        raw_exit_price=raw_exit_price,
        reason=reason,
        taker_fee_rate=fee_rate,
        slippage_bps=Decimal("0"),
        fill_model=fill_model,
        bar_duration=bar_duration,
    )


def _maker_as_open_position(position: _MakerPosition | None) -> _OpenPosition | None:
    """Project maker state onto the V1 equity helper without sharing fill semantics."""
    if position is None:
        return None
    return _OpenPosition(
        entry=position.entry,
        stop_price=position.stop_price,
        target_price=position.target_price,
        entered_bar_index=position.entered_bar_index,
        trail_extreme=position.trail_extreme,
        side=position.side,
    )


def _trail_open_position(
    position: _OpenPosition,
    candle: Candle,
    *,
    strategy: StrategyDefinition,
    record: SignalTraceRecord | None,
    is_fill_bar: bool,
) -> _OpenPosition:
    """Advance an ATR trailing stop after the fill bar; no-op when disabled."""
    policy = atr_trailing_stop(strategy.exits)
    if policy is None:
        return position
    if position.side == "short":
        state = ratcheted_short_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_low=candle.low,
            atr=_optional_indicator_value(record, policy.atr_indicator),
            multiple=Decimal(policy.multiple),
            price_increment=None,
            ratchet=not is_fill_bar,
        )
    else:
        state = ratcheted_long_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_high=candle.high,
            atr=_optional_indicator_value(record, policy.atr_indicator),
            multiple=Decimal(policy.multiple),
            price_increment=None,
            ratchet=not is_fill_bar,
        )
    if state.stop_price == position.stop_price and state.trail_extreme == position.trail_extreme:
        return position
    return replace(position, stop_price=state.stop_price, trail_extreme=state.trail_extreme)


def _trail_maker_position(
    position: _MakerPosition,
    candle: Candle,
    *,
    strategy: StrategyDefinition,
    record: SignalTraceRecord | None,
    is_fill_bar: bool,
) -> _MakerPosition:
    """Project maker state through the shared ATR ratchet."""
    trailed = _trail_open_position(
        _OpenPosition(
            entry=position.entry,
            stop_price=position.stop_price,
            target_price=position.target_price,
            entered_bar_index=position.entered_bar_index,
            trail_extreme=position.trail_extreme,
            side=position.side,
        ),
        candle,
        strategy=strategy,
        record=record,
        is_fill_bar=is_fill_bar,
    )
    return replace(
        position,
        stop_price=trailed.stop_price,
        trail_extreme=trailed.trail_extreme,
    )


def _candle_map(
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
    interval: CandleInterval,
) -> Mapping[datetime, Candle]:
    """Require exactly one well-formed candle through the required final next-open fill."""
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


def _open_position(
    pending: _PendingEntry,
    candle: Candle,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_bar_index: int,
    taker_fee_rate: Decimal,
    slippage_bps: Decimal,
    fill_model: FillModel,
) -> tuple[_OpenPosition | None, Decimal]:
    """Model a next-open taker entry using ATR risk sizing and never overdraw quote cash."""
    if strategy.entry.side == "short":
        return _open_short_position(
            pending,
            candle,
            strategy=strategy,
            cash=cash,
            entry_bar_index=entry_bar_index,
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
            fill_model=fill_model,
        )
    atr = _indicator_value(pending.signal, strategy.exits.initial_stop.atr_indicator)
    entry_quote = fill_model.buy(candle.open, slippage_bps)
    entry_price = entry_quote.price
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
        **_fill_evidence(entry_quote),
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


def _scale_in_open_position(
    pending: _PendingEntry,
    candle: Candle,
    *,
    position: _OpenPosition,
    strategy: StrategyDefinition,
    cash: Decimal,
    taker_fee_rate: Decimal,
    slippage_bps: Decimal,
    fill_model: FillModel,
) -> tuple[_OpenPosition | None, Decimal]:
    """VWAP a next-open add onto the open book without worsening stop or target."""
    short = position.side == "short"
    entry_quote = (
        fill_model.sell(candle.open, slippage_bps)
        if short
        else fill_model.buy(candle.open, slippage_bps)
    )
    add_price = entry_quote.price
    stop_distance = (
        add_price - position.stop_price if not short else position.stop_price - add_price
    )
    if stop_distance <= 0:
        return position, cash
    requested_risk = cash * Decimal(strategy.sizing.risk_fraction)
    risk_quantity = requested_risk / stop_distance
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        cash / (Decimal("1") + taker_fee_rate),
    )
    notional = min(risk_quantity * add_price, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return position, cash
    add_quantity = notional / add_price
    fee = notional * taker_fee_rate
    if not short and notional + fee > cash:
        return position, cash
    quantity = Decimal(position.entry.quantity) + add_quantity
    entry_price = (
        Decimal(position.entry.price) * Decimal(position.entry.quantity) + add_price * add_quantity
    ) / quantity
    combined_notional = quantity * entry_price
    combined_fee = Decimal(position.entry.fee) + fee
    entry = BacktestFill(
        candle_starts_at=position.entry.candle_starts_at,
        price=canonical_decimal(entry_price),
        quantity=canonical_decimal(quantity),
        notional=canonical_decimal(combined_notional),
        fee=canonical_decimal(combined_fee),
        fee_rate=position.entry.fee_rate,
        **_fill_evidence(entry_quote),
    )
    next_cash = cash + notional - fee if short else cash - notional - fee
    del pending
    return (
        replace(
            position,
            entry=entry,
            add_count=position.add_count + 1,
        ),
        next_cash,
    )


def _open_short_position(
    pending: _PendingEntry,
    candle: Candle,
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_bar_index: int,
    taker_fee_rate: Decimal,
    slippage_bps: Decimal,
    fill_model: FillModel,
) -> tuple[_OpenPosition | None, Decimal]:
    """Model a next-open taker short using ATR risk sizing; credits quote cash."""
    atr = _indicator_value(pending.signal, strategy.exits.initial_stop.atr_indicator)
    entry_quote = fill_model.sell(candle.open, slippage_bps)
    entry_price = entry_quote.price
    stop_distance = atr * Decimal(strategy.exits.initial_stop.multiple)
    if stop_distance <= 0:
        return None, cash
    stop_price = entry_price + stop_distance
    target_price = entry_price - stop_distance * Decimal(strategy.exits.take_profit.multiple)
    if target_price <= 0:
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
        **_fill_evidence(entry_quote),
    )
    return (
        _OpenPosition(
            entry=entry,
            stop_price=stop_price,
            target_price=target_price,
            entered_bar_index=entry_bar_index,
            side="short",
        ),
        cash + notional - fee,
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
    fill_model: FillModel,
    bar_duration: timedelta,
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
            fill_model=fill_model,
            bar_duration=bar_duration,
        )
    if position.side == "short":
        if fill_model.buy_trigger_price(candle.high) >= position.stop_price:
            return _close_position(
                position,
                candle,
                cash=cash,
                bar_index=bar_index,
                raw_exit_price=max(
                    candle.open,
                    fill_model.reference_for_buy_trigger(position.stop_price),
                ),
                reason="stop_loss",
                taker_fee_rate=taker_fee_rate,
                slippage_bps=slippage_bps,
                fill_model=fill_model,
                bar_duration=bar_duration,
            )
        if fill_model.buy_trigger_price(candle.low) <= position.target_price:
            return _close_position(
                position,
                candle,
                cash=cash,
                bar_index=bar_index,
                raw_exit_price=fill_model.reference_for_buy_trigger(position.target_price),
                reason="take_profit",
                taker_fee_rate=taker_fee_rate,
                slippage_bps=slippage_bps,
                fill_model=fill_model,
                bar_duration=bar_duration,
            )
        return None, cash
    if fill_model.sell_trigger_price(candle.low) <= position.stop_price:
        return _close_position(
            position,
            candle,
            cash=cash,
            bar_index=bar_index,
            raw_exit_price=min(
                candle.open,
                fill_model.reference_for_sell_trigger(position.stop_price),
            ),
            reason="stop_loss",
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
            fill_model=fill_model,
            bar_duration=bar_duration,
        )
    if fill_model.sell_trigger_price(candle.high) >= position.target_price:
        return _close_position(
            position,
            candle,
            cash=cash,
            bar_index=bar_index,
            raw_exit_price=fill_model.reference_for_sell_trigger(position.target_price),
            reason="take_profit",
            taker_fee_rate=taker_fee_rate,
            slippage_bps=slippage_bps,
            fill_model=fill_model,
            bar_duration=bar_duration,
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
    fill_model: FillModel,
    bar_duration: timedelta,
) -> tuple[BacktestTrade, Decimal]:
    """Apply a modeled covering fill, fee, cash transition, and exact complete-trade evidence."""
    del bar_index
    short = position.side == "short"
    exit_quote = (
        fill_model.buy(raw_exit_price, slippage_bps)
        if short
        else fill_model.sell(raw_exit_price, slippage_bps)
    )
    exit_price = exit_quote.price
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
        **_fill_evidence(exit_quote),
    )
    if short:
        entry_credit = Decimal(position.entry.notional) - Decimal(position.entry.fee)
        net_pnl = entry_credit - exit_notional - exit_fee
        gross_pnl = Decimal(position.entry.notional) - exit_notional
        next_cash = cash - exit_notional - exit_fee
    else:
        entry_cost = Decimal(position.entry.notional) + Decimal(position.entry.fee)
        net_pnl = exit_notional - exit_fee - entry_cost
        gross_pnl = exit_notional - Decimal(position.entry.notional)
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


def _equity_point(
    starts_at: datetime,
    cash: Decimal,
    position: _OpenPosition | None,
    mark_price: Decimal,
) -> EquityPoint:
    """Create one exact mark-to-market equity observation without decimal-float conversion."""
    quantity = Decimal("0") if position is None else Decimal(position.entry.quantity)
    signed = -quantity if position is not None and position.side == "short" else quantity
    equity = cash + signed * mark_price
    return EquityPoint(
        candle_starts_at=starts_at,
        cash=canonical_decimal(cash),
        base_quantity=canonical_decimal(signed),
        mark_price=canonical_decimal(mark_price),
        equity=canonical_decimal(equity),
    )


def _account_mark(
    fill_model: FillModel, raw_price: Decimal, position: _OpenPosition | None
) -> Decimal:
    """Mark longs at liquidation bid/mid and shorts at cover ask/mid."""
    if position is not None and position.side == "short":
        return fill_model.buy_trigger_price(raw_price)
    return fill_model.mark_price(raw_price)


def _summary(
    initial_cash: Decimal,
    final_cash: Decimal,
    trades: Sequence[BacktestTrade],
    equity_curve: Sequence[EquityPoint],
    *,
    include_spread_cost: bool,
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
        exposure_bars=exposure_bars,
        evaluation_bars=evaluation_bars,
        total_spread_cost=(
            canonical_decimal(total_spread_cost) if total_spread_cost is not None else None
        ),
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
