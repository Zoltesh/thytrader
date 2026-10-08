"""Combine independently simulated sleeves into one portfolio backtest result (ADR 0088).

Inputs are the verified child backtest results of every sleeve, all over the same
half-open evaluation window ``[start, end)``. A child equity point stamped at a bar's
start marks equity at that bar's **close**, so it is placed on the time grid at
``candle_starts_at + bar``; the terminal point (stamped ``end``) is the liquidation at
the open of the window-end bar and replaces the last close at instant ``end``.

* The grid is ``start`` plus the union of every sleeve's mark instants.
* Each sleeve's equity is forward-filled on the grid (its capital before its first mark).
* Combined equity is the sum of sleeve equity plus cash (reserve and unallocated capital).
* Drawdown, idle capital, overlap, and long time are time-weighted over grid intervals:
  the state at ``grid[k]`` holds until ``grid[k + 1]``.
* Correlations use returns sampled every ``lcm`` of the sleeves' bar durations, where every
  sleeve has a fresh mark, so no forward-filled zero returns enter them.
* The equal-weight basket buys each primary product with an equal share of the invested
  capital using the unified model's taker semantics, like the single-backtest benchmark.

Arithmetic runs under the simulation's Decimal64 context, so results are deterministic.
"""

from __future__ import annotations

from collections import Counter
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
from itertools import combinations, pairwise
from math import lcm
from typing import TYPE_CHECKING

from thytrader.backtest.broker import FillModel
from thytrader.backtest.metrics import series_ratio_metrics
from thytrader.decimal_text import canonical_decimal
from thytrader.market_data.models import parse_candle_interval
from thytrader.portfolios.backtest import (
    BASKET_DISCLOSURE,
    CASH_DISCLOSURE,
    CORRELATION_DISCLOSURE,
    GRID_DISCLOSURE,
    INDEPENDENT_SLEEVES_DISCLOSURE,
    MODEL_DISCLOSURE,
    MULTI_PRODUCT_DISCLOSURE,
    BasketLeg,
    CorrelationPair,
    OverlapPair,
    PortfolioBacktestPlan,
    PortfolioBacktestResult,
    PortfolioBacktestSummary,
    PortfolioBasketBenchmark,
    PortfolioCorrelation,
    PortfolioCurveMetrics,
    PortfolioEquityPoint,
    PortfolioOverlap,
    PortfolioSleeveResult,
)
from thytrader.portfolios.models import asset_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.backtest.models import BacktestResult
    from thytrader.market_data.models import Candle, DatasetTimeframe
    from thytrader.portfolios.backtest import PlannedSleeve

_CONTEXT = Context(
    prec=64,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_ZERO = Decimal(0)
_ONE = Decimal(1)
_MINUS_ONE = Decimal(-1)


class PortfolioCombinationError(ValueError):
    """The child results cannot be combined truthfully (mismatched window or inputs)."""


@dataclass(frozen=True, slots=True)
class SleeveRun:
    """One sleeve's plan entry with its verified child backtest result."""

    planned: PlannedSleeve
    run_fingerprint: str
    result_fingerprint: str
    result: BacktestResult


@dataclass(frozen=True, slots=True)
class BasketSource:
    """Which dataset prices one basket product (the finest-clock sleeve trading it)."""

    product_id: str
    dataset_fingerprint: str
    timeframe: DatasetTimeframe


@dataclass(frozen=True, slots=True)
class BasketInput:
    """One basket product's verified candles."""

    source: BasketSource
    candles: tuple[Candle, ...]


@dataclass(frozen=True, slots=True)
class SleeveMark:
    """One sleeve's account after a bar: equity, cash, and signed base inventory."""

    equity: Decimal
    cash: Decimal
    base_quantity: Decimal

    @property
    def position_value(self) -> Decimal:
        """Absolute marked value of open inventory (equity minus cash)."""
        return abs(self.equity - self.cash)


@dataclass(frozen=True, slots=True)
class _Window:
    """The evaluation window and the grid built on it."""

    start: datetime
    end: datetime
    grid: tuple[datetime, ...]
    durations: tuple[Decimal, ...]
    total_seconds: Decimal


def basket_sources(plan: PortfolioBacktestPlan) -> tuple[BasketSource, ...]:
    """Pick, per primary product, the dataset of the finest-clock sleeve trading it."""
    chosen: dict[str, PlannedSleeve] = {}
    for sleeve in plan.sleeves:
        current = chosen.get(sleeve.product_id)
        if current is None or _bar(sleeve.timeframe) < _bar(current.timeframe):
            chosen[sleeve.product_id] = sleeve
    return tuple(
        BasketSource(
            product_id=product_id,
            dataset_fingerprint=chosen[product_id].submission.dataset_fingerprint,
            timeframe=chosen[product_id].timeframe,
        )
        for product_id in sorted(chosen)
    )


def combine_portfolio(
    plan: PortfolioBacktestPlan,
    runs: Sequence[SleeveRun],
    basket: Sequence[BasketInput],
) -> PortfolioBacktestResult:
    """Combine verified child results into the canonical portfolio backtest result."""
    try:
        with localcontext(_CONTEXT):
            return _combine(plan, tuple(runs), tuple(basket))
    except DecimalException as error:
        raise PortfolioCombinationError(
            "Portfolio combination arithmetic failed under the deterministic Decimal contract."
        ) from error


def _combine(
    plan: PortfolioBacktestPlan,
    runs: tuple[SleeveRun, ...],
    basket: tuple[BasketInput, ...],
) -> PortfolioBacktestResult:
    """Build every section of the combined result."""
    _require_runs_match_plan(plan, runs)
    start, end = plan.evaluation_start, plan.evaluation_end
    series = tuple(sleeve_marks(run, start=start, end=end) for run in runs)
    window = _window(start, end, series)
    filled = tuple(
        forward_fill(window.grid, marks, initial=_initial_mark(run))
        for run, marks in zip(runs, series, strict=True)
    )
    capital = Decimal(plan.capital_quote)
    invested = sum((Decimal(run.planned.capital_quote) for run in runs), start=_ZERO)
    cash = capital - invested
    combined = tuple(
        cash + sum((marks[index].equity for marks in filled), start=_ZERO)
        for index in range(len(window.grid))
    )
    maximum_drawdown, drawdown_fraction = drawdown(combined, initial=capital)
    correlation, to_rest = correlations(window, filled, plan.sleeves)
    overlap, long_fractions = overlap_report(window, filled, plan.sleeves)
    basket_report, basket_curve = equal_weight_basket(
        plan, basket, window, invested=invested, cash=cash
    )
    sleeves = tuple(
        _sleeve_result(run, capital, to_rest[index], long_fractions[index])
        for index, run in enumerate(runs)
    )
    return PortfolioBacktestResult(
        portfolio_id=plan.portfolio_id,
        portfolio_revision=plan.portfolio_revision,
        portfolio_name=plan.portfolio_name,
        mode=plan.mode,
        quote_currency=plan.quote_currency,
        capital_quote=plan.capital_quote,
        cash_reserve_fraction=plan.cash_reserve_fraction,
        evaluation_start=start,
        evaluation_end=end,
        costs=plan.costs,
        sleeves=sleeves,
        summary=_summary(
            runs,
            capital=capital,
            invested=invested,
            cash=cash,
            combined=combined,
            maximum_drawdown=maximum_drawdown,
            drawdown_fraction=drawdown_fraction,
            idle=idle_fraction(window, filled, combined),
        ),
        metrics=_curve_metrics(window.grid, combined, drawdown_fraction),
        correlation=correlation,
        overlap=overlap,
        basket=basket_report,
        equity_curve=tuple(
            PortfolioEquityPoint(
                at=instant,
                equity=canonical_decimal(value),
                basket_equity=canonical_decimal(basket_value),
            )
            for instant, value, basket_value in zip(
                window.grid, combined, basket_curve, strict=True
            )
        ),
        disclosures=_disclosures(plan),
    )


def _require_runs_match_plan(plan: PortfolioBacktestPlan, runs: tuple[SleeveRun, ...]) -> None:
    """Refuse child results that do not match the planned sleeves, window, or capital."""
    if len(runs) != len(plan.sleeves):
        raise PortfolioCombinationError("Every planned sleeve needs exactly one child result.")
    for planned, run in zip(plan.sleeves, runs, strict=True):
        result = run.result
        if run.planned != planned or result.strategy_fingerprint != planned.strategy_fingerprint:
            raise PortfolioCombinationError("A child result does not belong to its sleeve.")
        curve = result.equity_curve
        if (
            curve[0].candle_starts_at != plan.evaluation_start
            or curve[-1].candle_starts_at != plan.evaluation_end
        ):
            raise PortfolioCombinationError(
                "A child result does not cover the portfolio's evaluation window."
            )
        if Decimal(result.summary.initial_equity) != Decimal(planned.capital_quote):
            raise PortfolioCombinationError("A child result did not start with its sleeve capital.")


def _bar(timeframe: str) -> timedelta:
    """Return one clock's bar duration."""
    return parse_candle_interval(timeframe).duration


def sleeve_marks(
    run: SleeveRun, *, start: datetime, end: datetime
) -> tuple[tuple[datetime, SleeveMark], ...]:
    """Place one child equity curve on close instants (the terminal point wins at ``end``)."""
    bar = _bar(run.planned.timeframe)
    marks: dict[datetime, SleeveMark] = {}
    for point in run.result.equity_curve:
        if point.candle_starts_at < start:
            raise PortfolioCombinationError("A child equity point precedes the window.")
        instant = end if point.candle_starts_at >= end else point.candle_starts_at + bar
        marks[instant] = SleeveMark(
            equity=Decimal(point.equity),
            cash=Decimal(point.cash),
            base_quantity=Decimal(point.base_quantity),
        )
    return tuple(sorted(marks.items()))


def _initial_mark(run: SleeveRun) -> SleeveMark:
    """A sleeve before its first close: all capital in cash, no inventory."""
    capital = Decimal(run.planned.capital_quote)
    return SleeveMark(equity=capital, cash=capital, base_quantity=_ZERO)


def _window(
    start: datetime, end: datetime, series: tuple[tuple[tuple[datetime, SleeveMark], ...], ...]
) -> _Window:
    """Build the union grid and its interval durations (seconds)."""
    instants = {start}
    for marks in series:
        instants.update(instant for instant, _mark in marks)
    grid = tuple(sorted(instants))
    if grid[-1] != end or len(grid) < 2:
        raise PortfolioCombinationError("Sleeve marks do not end at the evaluation end.")
    durations = tuple(
        Decimal(int((grid[index + 1] - grid[index]).total_seconds()))
        for index in range(len(grid) - 1)
    )
    return _Window(
        start=start,
        end=end,
        grid=grid,
        durations=durations,
        total_seconds=Decimal(int((end - start).total_seconds())),
    )


def forward_fill(
    grid: Sequence[datetime],
    marks: Sequence[tuple[datetime, SleeveMark]],
    *,
    initial: SleeveMark,
) -> tuple[SleeveMark, ...]:
    """Return the latest mark at or before every grid instant (``initial`` before the first)."""
    filled: list[SleeveMark] = []
    position = 0
    current = initial
    for instant in grid:
        while position < len(marks) and marks[position][0] <= instant:
            current = marks[position][1]
            position += 1
        filled.append(current)
    return tuple(filled)


def drawdown(values: Sequence[Decimal], *, initial: Decimal) -> tuple[Decimal, Decimal]:
    """Return the largest peak-to-trough fall in quote and as a fraction of the peak."""
    peak = initial
    largest = _ZERO
    largest_fraction = _ZERO
    for value in values:
        peak = max(peak, value)
        fall = peak - value
        largest = max(largest, fall)
        if peak > 0:
            largest_fraction = max(largest_fraction, fall / peak)
    return largest, largest_fraction


def idle_fraction(
    window: _Window, filled: Sequence[Sequence[SleeveMark]], combined: Sequence[Decimal]
) -> Decimal:
    """Time-weighted share of portfolio equity not held in open positions."""
    weighted = _ZERO
    for index, duration in enumerate(window.durations):
        value = combined[index]
        if value <= 0:
            continue
        deployed = sum((marks[index].position_value for marks in filled), start=_ZERO)
        weighted += max(_ZERO, value - deployed) / value * duration
    return weighted / window.total_seconds


def correlations(
    window: _Window,
    filled: Sequence[Sequence[SleeveMark]],
    sleeves: Sequence[PlannedSleeve],
) -> tuple[PortfolioCorrelation, tuple[Decimal | None, ...]]:
    """Pairwise and to-the-rest correlations of sleeve returns on the common clock."""
    clock = lcm(*(int(_bar(sleeve.timeframe).total_seconds()) for sleeve in sleeves))
    samples = _sample_indexes(window, clock)
    returns = tuple(_returns(tuple(marks[index].equity for index in samples)) for marks in filled)
    pairs = tuple(
        CorrelationPair(
            sleeve_ids=(sleeves[left].sleeve_id, sleeves[right].sleeve_id),
            coefficient=_optional(pearson(returns[left], returns[right])),
        )
        for left, right in combinations(range(len(sleeves)), 2)
    )
    to_rest = tuple(
        _correlation_to_rest(filled, samples, returns, index) for index in range(len(sleeves))
    )
    report = PortfolioCorrelation(
        return_clock_seconds=clock, observations=max(0, len(samples) - 1), pairs=pairs
    )
    return report, to_rest


def _sample_indexes(window: _Window, clock_seconds: int) -> tuple[int, ...]:
    """Grid indexes at ``start + k * clock`` (and ``end``), where every sleeve has a mark."""
    position = {instant: index for index, instant in enumerate(window.grid)}
    step = timedelta(seconds=clock_seconds)
    indexes: list[int] = []
    instant = window.start
    while instant <= window.end:
        found = position.get(instant)
        if found is None:
            raise PortfolioCombinationError("A sleeve has no mark on the common clock.")
        indexes.append(found)
        instant += step
    last = len(window.grid) - 1
    if indexes[-1] != last:
        indexes.append(last)
    return tuple(indexes)


def _returns(values: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Simple returns between consecutive samples (0 after a zero-equity sample)."""
    return tuple(
        current / previous - _ONE if previous != 0 else _ZERO
        for previous, current in pairwise(values)
    )


def _correlation_to_rest(
    filled: Sequence[Sequence[SleeveMark]],
    samples: tuple[int, ...],
    returns: tuple[tuple[Decimal, ...], ...],
    index: int,
) -> Decimal | None:
    """Correlate one sleeve with the summed equity of every other sleeve."""
    if len(filled) < 2:
        return None
    rest = tuple(
        sum(
            (marks[sample].equity for other, marks in enumerate(filled) if other != index),
            start=_ZERO,
        )
        for sample in samples
    )
    return pearson(returns[index], _returns(rest))


def pearson(left: Sequence[Decimal], right: Sequence[Decimal]) -> Decimal | None:
    """Pearson correlation, or None with fewer than 3 observations or zero variance."""
    count = len(left)
    if count != len(right) or count < 3:
        return None
    left_mean = sum(left, start=_ZERO) / count
    right_mean = sum(right, start=_ZERO) / count
    covariance = _ZERO
    left_square = _ZERO
    right_square = _ZERO
    for x_value, y_value in zip(left, right, strict=True):
        x_delta = x_value - left_mean
        y_delta = y_value - right_mean
        covariance += x_delta * y_delta
        left_square += x_delta * x_delta
        right_square += y_delta * y_delta
    if left_square == 0 or right_square == 0:
        return None
    coefficient = covariance / (left_square * right_square).sqrt()
    return min(_ONE, max(_MINUS_ONE, coefficient))


def overlap_report(
    window: _Window,
    filled: Sequence[Sequence[SleeveMark]],
    sleeves: Sequence[PlannedSleeve],
) -> tuple[PortfolioOverlap, tuple[Decimal | None, ...]]:
    """Time-weighted same-asset overlap, long-together time, and each sleeve's long time."""
    single = tuple(index for index, sleeve in enumerate(sleeves) if _single_product(sleeve))
    assets = {index: asset_of(sleeves[index].product_id) for index in single}
    shared = tuple(
        (left, right) for left, right in combinations(single, 2) if assets[left] == assets[right]
    )
    times = _LongTimes(
        long=dict.fromkeys(single, _ZERO), pairs=dict.fromkeys(shared, _ZERO), same=_ZERO
    )
    for position, duration in enumerate(window.durations):
        longs = tuple(index for index in single if filled[index][position].base_quantity > 0)
        _accumulate_long_time(times, longs, assets, duration)
    total = window.total_seconds
    report = PortfolioOverlap(
        same_asset_fraction=canonical_decimal(times.same / total),
        long_together_fraction=canonical_decimal(times.together / total),
        pairs=tuple(
            OverlapPair(
                sleeve_ids=(sleeves[left].sleeve_id, sleeves[right].sleeve_id),
                asset=assets[left],
                fraction=canonical_decimal(times.pairs[(left, right)] / total),
            )
            for left, right in shared
        ),
        excluded_sleeve_ids=tuple(
            sleeve.sleeve_id for sleeve in sleeves if not _single_product(sleeve)
        ),
    )
    long_fractions = tuple(
        times.long[index] / total if index in times.long else None for index in range(len(sleeves))
    )
    return report, long_fractions


@dataclass(slots=True)
class _LongTimes:
    """Running long-time totals (seconds) while walking the grid."""

    long: dict[int, Decimal]
    pairs: dict[tuple[int, int], Decimal]
    same: Decimal
    together: Decimal = _ZERO


def _accumulate_long_time(
    times: _LongTimes, longs: tuple[int, ...], assets: dict[int, str], duration: Decimal
) -> None:
    """Add one grid interval to the sleeves that were long during it."""
    for index in longs:
        times.long[index] += duration
    if len(longs) >= 2:
        times.together += duration
    if any(count >= 2 for count in Counter(assets[index] for index in longs).values()):
        times.same += duration
    for pair in times.pairs:
        if pair[0] in longs and pair[1] in longs:
            times.pairs[pair] += duration


def _single_product(sleeve: PlannedSleeve) -> bool:
    """True when the sleeve's strategy covers exactly one product."""
    return len(sleeve.covered_product_ids) == 1


def equal_weight_basket(
    plan: PortfolioBacktestPlan,
    inputs: Sequence[BasketInput],
    window: _Window,
    *,
    invested: Decimal,
    cash: Decimal,
) -> tuple[PortfolioBasketBenchmark, tuple[Decimal, ...]]:
    """Buy-and-hold an equal-weight basket of the primary products with the same cash."""
    expected = tuple(source.product_id for source in basket_sources(plan))
    if tuple(item.source.product_id for item in inputs) != expected:
        raise PortfolioCombinationError("Basket inputs do not match the sleeves' products.")
    share = invested / len(inputs)
    legs: list[BasketLeg] = []
    curves: list[tuple[SleeveMark, ...]] = []
    fees = _ZERO
    for item in inputs:
        leg, marks, leg_fees = _basket_leg(plan, item, window, share=share)
        legs.append(leg)
        fees += leg_fees
        initial = SleeveMark(equity=share, cash=share, base_quantity=_ZERO)
        curves.append(forward_fill(window.grid, marks, initial=initial))
    curve = tuple(
        cash + sum((marks[index].equity for marks in curves), start=_ZERO)
        for index in range(len(window.grid))
    )
    capital = invested + cash
    _largest, drawdown_fraction = drawdown(curve, initial=capital)
    report = PortfolioBasketBenchmark(
        legs=tuple(legs),
        invested_quote=canonical_decimal(invested),
        cash_quote=canonical_decimal(cash),
        final_equity=canonical_decimal(curve[-1]),
        total_return_fraction=canonical_decimal(curve[-1] / capital - _ONE),
        maximum_drawdown_fraction=canonical_decimal(drawdown_fraction),
        total_fees=canonical_decimal(fees),
    )
    return report, curve


def _basket_leg(
    plan: PortfolioBacktestPlan, item: BasketInput, window: _Window, *, share: Decimal
) -> tuple[BasketLeg, tuple[tuple[datetime, SleeveMark], ...], Decimal]:
    """Price one basket product: taker entry at the first open, taker exit at ``end``'s open."""
    bar = _bar(item.source.timeframe)
    evaluation, terminal = _basket_candles(item, window.start, window.end, bar)
    fill_model = FillModel(Decimal(plan.costs.spread_bps))
    taker = Decimal(plan.costs.taker_fee_rate)
    slippage = Decimal(plan.costs.fixed_slippage_bps)
    entry = fill_model.taker_buy(evaluation[0].open, slippage)
    entry_notional = share / (_ONE + taker)
    quantity = entry_notional / entry.price
    exit_quote = fill_model.taker_sell(terminal.open, slippage)
    exit_notional = quantity * exit_quote.price
    exit_fee = exit_notional * taker
    final = exit_notional - exit_fee
    marks: dict[datetime, SleeveMark] = {}
    for candle in evaluation:
        value = quantity * fill_model.bid(candle.close)
        marks[candle.starts_at + bar] = SleeveMark(value, _ZERO, quantity)
    marks[window.end] = SleeveMark(final, final, _ZERO)
    leg = BasketLeg(
        product_id=item.source.product_id,
        dataset_fingerprint=item.source.dataset_fingerprint,
        timeframe=item.source.timeframe,
        entry_price=canonical_decimal(entry.price),
        exit_price=canonical_decimal(exit_quote.price),
        quantity=canonical_decimal(quantity),
        return_fraction=canonical_decimal(final / share - _ONE),
    )
    return leg, tuple(sorted(marks.items())), entry_notional * taker + exit_fee


def _basket_candles(
    item: BasketInput, start: datetime, end: datetime, bar: timedelta
) -> tuple[tuple[Candle, ...], Candle]:
    """Return the evaluation candles and the window-end candle, or refuse a gap."""
    by_start = {candle.starts_at: candle for candle in item.candles}
    count = int((end - start) / bar)
    evaluation = tuple(by_start.get(start + bar * offset) for offset in range(count))
    terminal = by_start.get(end)
    if terminal is None or count < 1 or any(candle is None for candle in evaluation):
        raise PortfolioCombinationError(
            f"Basket candles for {item.source.product_id} do not cover the evaluation window."
        )
    return tuple(candle for candle in evaluation if candle is not None), terminal


def _sleeve_result(
    run: SleeveRun,
    capital: Decimal,
    correlation_to_rest: Decimal | None,
    long_fraction: Decimal | None,
) -> PortfolioSleeveResult:
    """Project one child result and its contribution to the portfolio."""
    planned = run.planned
    summary = run.result.summary
    pnl = Decimal(summary.final_equity) - Decimal(summary.initial_equity)
    return PortfolioSleeveResult(
        sleeve_id=planned.sleeve_id,
        strategy_id=planned.strategy_id,
        strategy_name=planned.strategy_name,
        strategy_fingerprint=planned.strategy_fingerprint,
        product_id=planned.product_id,
        covered_product_ids=planned.covered_product_ids,
        timeframe=planned.timeframe,
        weight_fraction=planned.weight_fraction,
        capital_quote=planned.capital_quote,
        run_fingerprint=run.run_fingerprint,
        result_fingerprint=run.result_fingerprint,
        dataset_fingerprint=run.result.dataset_fingerprint,
        final_equity=summary.final_equity,
        total_return_fraction=summary.total_return_fraction,
        maximum_drawdown_fraction=summary.maximum_drawdown_fraction,
        trade_count=summary.trade_count,
        win_rate=summary.win_rate,
        contribution_fraction=canonical_decimal(pnl / capital),
        correlation_to_rest=_optional(correlation_to_rest),
        long_fraction=_optional(long_fraction),
    )


def _summary(
    runs: tuple[SleeveRun, ...],
    *,
    capital: Decimal,
    invested: Decimal,
    cash: Decimal,
    combined: tuple[Decimal, ...],
    maximum_drawdown: Decimal,
    drawdown_fraction: Decimal,
    idle: Decimal,
) -> PortfolioBacktestSummary:
    """Headline numbers of the combined curve."""
    final = combined[-1]
    best = max(runs, key=lambda run: Decimal(run.result.summary.total_return_fraction))
    return PortfolioBacktestSummary(
        initial_equity=canonical_decimal(capital),
        final_equity=canonical_decimal(final),
        total_net_pnl=canonical_decimal(final - capital),
        total_return_fraction=canonical_decimal(final / capital - _ONE),
        maximum_drawdown=canonical_decimal(maximum_drawdown),
        maximum_drawdown_fraction=canonical_decimal(drawdown_fraction),
        idle_capital_fraction=canonical_decimal(idle),
        allocated_fraction=canonical_decimal(invested / capital),
        cash_quote=canonical_decimal(cash),
        trade_count=sum(run.result.summary.trade_count for run in runs),
        best_sleeve_return_fraction=best.result.summary.total_return_fraction,
        best_sleeve_maximum_drawdown_fraction=best.result.summary.maximum_drawdown_fraction,
        grid_points=len(combined),
    )


def _curve_metrics(
    grid: tuple[datetime, ...], combined: tuple[Decimal, ...], drawdown_fraction: Decimal
) -> PortfolioCurveMetrics:
    """Ratio metrics of the combined curve through the performance-metrics formulas."""
    metrics = series_ratio_metrics(
        tuple(zip(grid, combined, strict=True)), maximum_drawdown_fraction=drawdown_fraction
    )
    return PortfolioCurveMetrics(
        bar_seconds=_optional(metrics.bar_seconds),
        bars_per_year=_optional(metrics.bars_per_year),
        sharpe=_optional(metrics.sharpe),
        sortino=_optional(metrics.sortino),
        calmar=_optional(metrics.calmar),
        cagr=_optional(metrics.cagr),
        annualized_volatility=_optional(metrics.annualized_volatility),
    )


def _disclosures(plan: PortfolioBacktestPlan) -> tuple[str, ...]:
    """Fixed honesty statements, plus the multi-product caveat when it applies."""
    disclosures = [
        INDEPENDENT_SLEEVES_DISCLOSURE,
        MODEL_DISCLOSURE,
        CASH_DISCLOSURE,
        GRID_DISCLOSURE,
        CORRELATION_DISCLOSURE,
        BASKET_DISCLOSURE,
    ]
    if any(not _single_product(sleeve) for sleeve in plan.sleeves):
        disclosures.append(MULTI_PRODUCT_DISCLOSURE)
    return tuple(disclosures)


def _optional(value: Decimal | None) -> str | None:
    """Canonicalize an optional metric."""
    return None if value is None else canonical_decimal(value)
