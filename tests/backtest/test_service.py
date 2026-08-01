"""Tests for authoritative published-run backtest execution and result publication."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from thytrader.backtest.service import evaluate_and_publish_backtest
from thytrader.research.models import research_run_fingerprint
from thytrader.research.publication import PublishedResearchRunSpecification
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy

from .test_kernel import _candles, _run, _strategy

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestResult
    from thytrader.market_data.models import Candle


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


class _ResultStore:
    """Capture canonical result publication at the service boundary."""

    def __init__(self) -> None:
        """Initialize an empty immutable publication capture."""
        self.published: list[BacktestResult] = []

    async def publish(self, result: BacktestResult) -> BacktestResult:
        """Record and return exactly the canonical candidate result."""
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
