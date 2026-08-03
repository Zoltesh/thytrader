"""Deterministic buy-and-hold comparisons derived from verified backtest inputs."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, DecimalException, localcontext
from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.backtest.broker import ConstantSpreadFillModel, FillModel, MarkFillModel
from thytrader.backtest.kernel import _SIMULATION_CONTEXT
from thytrader.backtest.models import (
    BacktestBenchmark,
    BacktestResult,
    backtest_result_fingerprint,
)
from thytrader.research.indicators import canonical_decimal
from thytrader.research.models import ResearchRunSpecification, research_run_fingerprint

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle


class BacktestBenchmarkError(ValueError):
    """Report invalid or incomplete inputs for one derived benchmark comparison."""


def calculate_buy_and_hold_benchmark(
    result: BacktestResult,
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
) -> BacktestBenchmark:
    """Calculate a fully invested buy-and-hold round trip over the published evaluation window.

    The benchmark buys at the first evaluation candle's open, marks at each completed
    evaluation close, and liquidates at the required final next-open boundary. It uses
    the published taker fee, fixed slippage, and V1/V2 fill model, but remains a derived
    report and is not included in the immutable backtest-result bytes.
    """
    try:
        validated_result = BacktestResult.model_validate(result.model_dump(mode="python"))
        validated_specification = ResearchRunSpecification.model_validate(
            specification.model_dump(mode="python")
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise BacktestBenchmarkError("Benchmark source inputs are invalid.") from error

    if (
        validated_result.run_fingerprint != research_run_fingerprint(validated_specification)
        or validated_result.strategy_fingerprint != validated_specification.strategy_fingerprint
        or validated_result.dataset_fingerprint != validated_specification.dataset_fingerprint
        or validated_result.engine_contract_version
        != validated_specification.engine_contract_version
        or validated_result.broker != validated_specification.broker
    ):
        raise BacktestBenchmarkError("Benchmark source identities do not match.")

    try:
        with localcontext(_SIMULATION_CONTEXT):
            selected = _selected_candles(validated_specification, candles)
            evaluation_bars = int(
                (
                    validated_specification.evaluation.ends_at
                    - validated_specification.evaluation.starts_at
                ).total_seconds()
                // 3600
            )
            evaluation_candles = selected[:evaluation_bars]
            terminal_candle = selected[evaluation_bars]
            fill_model = _fill_model(validated_specification)
            taker_fee_rate = Decimal(validated_specification.costs.taker_fee_rate)
            slippage_bps = Decimal(validated_specification.costs.fixed_slippage_bps)
            initial_cash = Decimal(validated_specification.capital.initial_quote_balance)
            entry_quote = fill_model.buy(evaluation_candles[0].open, slippage_bps)
            entry_price = entry_quote.price
            entry_notional = initial_cash / (Decimal("1") + taker_fee_rate)
            entry_fee = entry_notional * taker_fee_rate
            quantity = entry_notional / entry_price
            exit_quote = fill_model.sell(terminal_candle.open, slippage_bps)
            exit_notional = quantity * exit_quote.price
            exit_fee = exit_notional * taker_fee_rate
            final_equity = exit_notional - exit_fee
            mark_equities = tuple(
                quantity * fill_model.mark_price(candle.close) for candle in evaluation_candles
            )
            maximum_drawdown, maximum_drawdown_fraction = _drawdown(
                (initial_cash, *mark_equities, final_equity)
            )
            total_spread_cost = (
                (entry_quote.spread_cost + exit_quote.spread_cost) * quantity
                if validated_specification.broker is not None
                else None
            )
            return BacktestBenchmark(
                benchmark_contract_version="thytrader-buy-and-hold-v1",
                result_fingerprint=backtest_result_fingerprint(validated_result),
                run_fingerprint=validated_result.run_fingerprint,
                dataset_fingerprint=validated_result.dataset_fingerprint,
                engine_contract_version=validated_result.engine_contract_version,
                broker=validated_result.broker,
                entry_candle_starts_at=evaluation_candles[0].starts_at,
                exit_candle_starts_at=terminal_candle.starts_at,
                entry_price=canonical_decimal(entry_price),
                exit_price=canonical_decimal(exit_quote.price),
                initial_equity=canonical_decimal(initial_cash),
                final_equity=canonical_decimal(final_equity),
                total_net_pnl=canonical_decimal(final_equity - initial_cash),
                total_return_fraction=canonical_decimal(
                    (final_equity - initial_cash) / initial_cash
                ),
                total_fees=canonical_decimal(entry_fee + exit_fee),
                total_spread_cost=(
                    canonical_decimal(total_spread_cost) if total_spread_cost is not None else None
                ),
                maximum_drawdown=canonical_decimal(maximum_drawdown),
                maximum_drawdown_fraction=canonical_decimal(maximum_drawdown_fraction),
                evaluation_bars=evaluation_bars,
            )
    except (DecimalException, IndexError, KeyError, ValueError) as error:
        if isinstance(error, BacktestBenchmarkError):
            raise
        raise BacktestBenchmarkError(
            "Benchmark arithmetic or coverage validation failed."
        ) from error


def _selected_candles(
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
) -> tuple[Candle, ...]:
    """Require exactly the evaluation candles plus the published terminal next-open candle."""
    starts_at = specification.evaluation.starts_at
    ends_at = specification.evaluation.ends_at
    selected = tuple(candle for candle in candles if starts_at <= candle.starts_at <= ends_at)
    evaluation_bars = int((ends_at - starts_at).total_seconds() // 3600)
    if len(selected) != evaluation_bars + 1:
        raise BacktestBenchmarkError("Benchmark candle coverage is incomplete or duplicated.")
    if len({candle.starts_at for candle in selected}) != len(selected):
        raise BacktestBenchmarkError("Benchmark candle coverage is duplicated.")
    for offset, candle in enumerate(selected):
        expected_start = starts_at + timedelta(hours=offset)
        if (
            candle.starts_at != expected_start
            or candle.open <= 0
            or candle.close <= 0
            or candle.high < candle.low
            or candle.high < max(candle.open, candle.close)
            or candle.low > min(candle.open, candle.close)
        ):
            raise BacktestBenchmarkError("Benchmark candles are not valid contiguous OHLC bars.")
    return selected


def _fill_model(specification: ResearchRunSpecification) -> FillModel:
    """Construct the fill model selected by the verified run contract."""
    if specification.engine_contract_version == "thytrader-bar-backtest-v1":
        return MarkFillModel()
    if specification.broker is None:
        raise BacktestBenchmarkError("Benchmark V2 broker assumptions are missing.")
    return ConstantSpreadFillModel(Decimal(specification.broker.spread_bps))


def _drawdown(equities: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    """Return exact absolute and fractional maximum drawdown over benchmark marks."""
    peak = equities[0]
    maximum_drawdown = Decimal("0")
    maximum_drawdown_fraction = Decimal("0")
    for equity in equities:
        peak = max(peak, equity)
        drawdown = peak - equity
        maximum_drawdown = max(maximum_drawdown, drawdown)
        maximum_drawdown_fraction = max(maximum_drawdown_fraction, drawdown / peak)
    return maximum_drawdown, maximum_drawdown_fraction
