"""Behavior tests for the derived deterministic buy-and-hold benchmark."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from thytrader.backtest.benchmark import (
    BacktestBenchmarkError,
    calculate_buy_and_hold_benchmark,
)
from thytrader.backtest.kernel import simulate_backtest
from thytrader.backtest.models import (
    BacktestResult,
    backtest_result_fingerprint,
    canonical_backtest_result_bytes,
)
from thytrader.persistence.backtest_benchmarks import PostgresBacktestBenchmarkReader

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle
    from thytrader.research.models import ResearchRunSpecification

from .test_kernel import _candles, _run, _strategy, _v2_run


class _SingleResultReader:
    """Minimal result boundary for benchmark composition coverage."""

    def __init__(self, result: BacktestResult) -> None:
        """Keep one immutable result available to the derived reader."""
        self._result = result

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return the bound result for the requested identity."""
        assert result_fingerprint == backtest_result_fingerprint(self._result)
        return self._result


class _SourceReader:
    """Minimal verified research-run boundary for benchmark composition coverage."""

    def __init__(self, specification: ResearchRunSpecification) -> None:
        """Keep the verified source specification available to the reader."""
        self._specification = specification

    async def load_source_specification(self, result: BacktestResult) -> ResearchRunSpecification:
        """Return the source specification bound to the supplied result."""
        del result
        return self._specification


class _DatasetReader:
    """Minimal verified candle boundary for benchmark composition coverage."""

    def __init__(self, candles: tuple[Candle, ...]) -> None:
        """Keep the verified candle vector available to the reader."""
        self._candles = candles

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Return the candle vector for the requested dataset identity."""
        del content_fingerprint
        return self._candles


def test_postgres_benchmark_reader_composes_only_verified_boundaries() -> None:
    """The production derived reader must call result, source, and dataset boundaries."""
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())
    reader = PostgresBacktestBenchmarkReader(
        _SingleResultReader(result),
        _SourceReader(specification),
        _DatasetReader(_candles()),
    )

    benchmark = asyncio.run(reader.load(backtest_result_fingerprint(result)))

    assert benchmark.result_fingerprint == backtest_result_fingerprint(result)
    assert benchmark.dataset_fingerprint == specification.dataset_fingerprint


def test_buy_and_hold_uses_the_verified_run_window_and_cost_contract() -> None:
    """The benchmark must use the same source, terminal boundary, and published V1 costs."""
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())

    benchmark = calculate_buy_and_hold_benchmark(result, specification, _candles())

    assert benchmark.benchmark_contract_version == "thytrader-buy-and-hold-v1"
    assert benchmark.result_fingerprint == backtest_result_fingerprint(result)
    assert benchmark.run_fingerprint == result.run_fingerprint
    assert benchmark.dataset_fingerprint == result.dataset_fingerprint
    assert benchmark.engine_contract_version == "thytrader-bar-backtest-v1"
    assert benchmark.entry_candle_starts_at == specification.evaluation.starts_at
    assert benchmark.exit_candle_starts_at == specification.evaluation.ends_at
    assert benchmark.initial_equity == specification.capital.initial_quote_balance
    assert benchmark.entry_price == "14.014"
    assert benchmark.exit_price == "9.99"
    assert benchmark.final_equity == (
        "7100.128272070102694568049572326732292515012788751026561120658641"
    )
    assert benchmark.total_fees == (
        "34.18879381240373541485603076706583977755919586544051899056176216"
    )
    assert benchmark.maximum_drawdown == (
        "2899.871727929897305431950427673267707484987211248973438879341359"
    )
    assert benchmark.maximum_drawdown_fraction == (
        "0.2899871727929897305431950427673267707484987211248973438879341359"
    )
    assert benchmark.total_spread_cost is None
    assert benchmark.evaluation_bars == 2


def test_buy_and_hold_v2_discloses_spread_and_is_not_part_of_result_identity() -> None:
    """V2 benchmark friction follows its broker while existing result bytes stay untouched."""
    strategy = _strategy()
    specification = _v2_run(strategy, "10")
    result = simulate_backtest(specification, strategy, _candles())

    before = canonical_backtest_result_bytes(result)
    benchmark = calculate_buy_and_hold_benchmark(result, specification, _candles())

    assert benchmark.engine_contract_version == "thytrader-bar-backtest-v2"
    assert benchmark.broker == result.broker
    assert benchmark.entry_price == "14.021007"
    assert benchmark.exit_price == "9.985005"
    assert benchmark.final_equity == (
        "7093.031692088023631405063016032552650043733415648826634522836892"
    )
    assert benchmark.total_fees == (
        "34.17457220923323629028090740775886654615382638026576963866231978"
    )
    assert benchmark.total_spread_cost == (
        "8.541503405705179924999014598782721564786477582432043182848276451"
    )
    assert benchmark.maximum_drawdown == (
        "2906.968307911976368594936983967447349956266584351173365477163108"
    )
    assert benchmark.maximum_drawdown_fraction == (
        "0.2906968307911976368594936983967447349956266584351173365477163108"
    )
    assert canonical_backtest_result_bytes(result) == before
    assert benchmark.result_fingerprint == backtest_result_fingerprint(result)


def test_buy_and_hold_rejects_candle_coverage_outside_the_published_window() -> None:
    """A derived benchmark must not silently use candles outside the verified run range."""
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())

    with_missing_terminal = _candles()[:-1]

    try:
        calculate_buy_and_hold_benchmark(result, specification, with_missing_terminal)
    except ValueError as error:
        assert "coverage" in str(error)
    else:
        raise AssertionError("incomplete benchmark coverage must fail closed")


def test_buy_and_hold_rejects_nonfinite_or_invalid_candles() -> None:
    """The direct benchmark boundary must fail closed even outside DatasetStore composition."""
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())
    invalid_candles = _candles()
    invalid_candles = (
        *invalid_candles[:2],
        replace(invalid_candles[2], volume=Decimal("NaN")),
        *invalid_candles[3:],
    )

    with pytest.raises(BacktestBenchmarkError, match="OHLCV"):
        calculate_buy_and_hold_benchmark(result, specification, invalid_candles)


def test_buy_and_hold_rejects_naive_candle_timestamps() -> None:
    """Malformed candle timestamps must fail at the controlled benchmark boundary."""
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())
    source_candles = _candles()
    invalid_candles = (
        *source_candles[:2],
        replace(source_candles[2], starts_at=source_candles[2].starts_at.replace(tzinfo=None)),
        *source_candles[3:],
    )

    with pytest.raises(BacktestBenchmarkError, match="timestamp"):
        calculate_buy_and_hold_benchmark(result, specification, invalid_candles)
