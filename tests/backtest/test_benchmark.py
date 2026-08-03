"""Behavior tests for the derived deterministic buy-and-hold benchmark."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.backtest.benchmark import calculate_buy_and_hold_benchmark
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
    assert Decimal(benchmark.total_fees) > 0
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
    assert benchmark.total_spread_cost is not None
    assert Decimal(benchmark.total_spread_cost) > 0
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
