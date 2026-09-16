"""Tests for authoritative published-run backtest execution and result publication."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from thytrader.backtest.service import evaluate_and_publish_backtest
from thytrader.research.models import research_run_fingerprint
from thytrader.research.publication import PublishedResearchRunSpecification
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy

from .test_kernel import _candles, _run, _strategy

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestResult
    from thytrader.market_data.models import Candle
    from thytrader.research.trace import SignalTrace


class _RunStore:
    """Return the already publication-verified executable test request."""

    def __init__(self) -> None:
        """Record exact immutable run identities requested by the service."""
        self.loaded: list[str] = []

    async def load(
        self,
        run_fingerprint_value: str,
        *,
        dataset_store: _DatasetStore,
    ) -> PublishedResearchRunSpecification:
        """Return the request after recording its caller-supplied fingerprint."""
        del dataset_store
        self.loaded.append(run_fingerprint_value)
        strategy = _strategy()
        return PublishedResearchRunSpecification(
            run_fingerprint=run_fingerprint_value,
            specification=_run(strategy),
        )


class _StrategyStore:
    """Return the exact published strategy addressed by fingerprint."""

    def __init__(self) -> None:
        """Record strategy identities resolved by the service."""
        self.loaded: list[str] = []

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return the deterministic strategy after asserting exact identity use."""
        self.loaded.append(strategy_fingerprint_value)
        strategy = _strategy()
        assert strategy_fingerprint(strategy) == strategy_fingerprint_value
        return PublishedStrategy(
            strategy_fingerprint=strategy_fingerprint_value,
            definition=strategy,
        )


class _DatasetStore:
    """Return verified fixture candles by their exact immutable dataset identity."""

    def __init__(self) -> None:
        """Record each dataset identity requested by the service."""
        self.loaded: list[str] = []

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Return deterministic candles after recording the requested fingerprint."""
        self.loaded.append(content_fingerprint)
        return _candles()


class _SlowDatasetStore(_DatasetStore):
    """A dataset store whose load blocks the calling thread for a fixed delay."""

    def __init__(self, delay_seconds: float) -> None:
        """Bind the artificial per-call delay used to simulate a large study."""
        super().__init__()
        self._delay_seconds = delay_seconds

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Block the calling thread, then return the deterministic fixture candles."""
        time.sleep(self._delay_seconds)
        return super().load_candles(content_fingerprint)


class _ResultStore:
    """Capture canonical result publication at the service boundary."""

    def __init__(self) -> None:
        """Initialize an empty immutable publication capture."""
        self.published: list[BacktestResult] = []

    async def publish(self, result: BacktestResult, *, trace: SignalTrace) -> BacktestResult:
        """Record and return exactly the canonical candidate result."""
        assert trace.run_fingerprint == result.run_fingerprint
        self.published.append(result)
        return result


def test_service_loads_exact_artifacts_simulates_and_publishes_one_result() -> None:
    """Backtesting must consume source publications before appending a derived result."""
    run_fingerprint = "sha256:" + "b" * 64
    run_store = _RunStore()
    strategy_store = _StrategyStore()
    dataset_store = _DatasetStore()
    result_store = _ResultStore()

    result = asyncio.run(
        evaluate_and_publish_backtest(
            run_fingerprint,
            run_store=run_store,
            strategy_store=strategy_store,
            dataset_store=dataset_store,
            result_store=result_store,
        )
    )

    specification = _run(_strategy())
    assert run_store.loaded == [run_fingerprint]
    assert strategy_store.loaded == [specification.strategy_fingerprint]
    assert dataset_store.loaded == [specification.dataset_fingerprint]
    assert result_store.published == [result]
    assert result.run_fingerprint == research_run_fingerprint(specification)


@pytest.mark.anyio
async def test_service_offloads_blocking_simulation_off_the_event_loop() -> None:
    """A slow dataset load/simulation must not stall concurrent event-loop work (F16).

    Blocking the event loop thread would serialize this backtest with the concurrent
    ticker below, so the combined wall-clock time would be close to their sum.
    Offloading the blocking segment to a worker thread lets both run concurrently.
    """
    delay_seconds = 0.2
    run_fingerprint = "sha256:" + "c" * 64
    run_store = _RunStore()
    strategy_store = _StrategyStore()
    dataset_store = _SlowDatasetStore(delay_seconds)
    result_store = _ResultStore()
    ticks = 0

    async def _tick_while_waiting() -> None:
        nonlocal ticks
        for _ in range(8):
            await asyncio.sleep(delay_seconds / 8)
            ticks += 1

    started_at = time.monotonic()
    result, _ = await asyncio.gather(
        evaluate_and_publish_backtest(
            run_fingerprint,
            run_store=run_store,
            strategy_store=strategy_store,
            dataset_store=dataset_store,
            result_store=result_store,
        ),
        _tick_while_waiting(),
    )
    elapsed = time.monotonic() - started_at
    assert result_store.published == [result]
    assert ticks == 8
    assert elapsed < delay_seconds * 1.5
