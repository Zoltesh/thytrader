"""Tests for authoritative published-run signal evaluation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from thytrader.research.publication import PublishedResearchRunSpecification
from thytrader.research.signal_service import evaluate_published_signal_run
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy

from .test_signal_evaluator import _candles, _run, _strategy

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle


class _RunStore:
    """Return one already publication-verified executable request."""

    def __init__(self) -> None:
        """Record load identities for the service boundary assertion."""
        self.loaded: list[str] = []

    async def load(
        self,
        run_fingerprint_value: str,
        *,
        dataset_store: _DatasetStore,
    ) -> PublishedResearchRunSpecification:
        """Return the exact test publication after recording the requested identity."""
        del dataset_store
        self.loaded.append(run_fingerprint_value)
        strategy = _strategy()
        return PublishedResearchRunSpecification(
            run_fingerprint=run_fingerprint_value,
            specification=_run(strategy),
        )


class _StrategyStore:
    """Return one exact published strategy by fingerprint."""

    def __init__(self) -> None:
        """Record strategy loads for exact-identity assertions."""
        self.loaded: list[str] = []

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return the canonical test strategy after checking its exact identity."""
        self.loaded.append(strategy_fingerprint_value)
        strategy = _strategy()
        assert strategy_fingerprint(strategy) == strategy_fingerprint_value
        return PublishedStrategy(
            strategy_fingerprint=strategy_fingerprint_value,
            definition=strategy,
        )


class _DatasetStore:
    """Return exact verified candles by immutable dataset fingerprint."""

    def __init__(self) -> None:
        """Record immutable dataset loads for exact-identity assertions."""
        self.loaded: list[str] = []

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Return the deterministic candle fixture after recording its identity."""
        self.loaded.append(content_fingerprint)
        return _candles()


def test_service_loads_exact_published_artifacts_before_evaluation() -> None:
    """The consumer must use publication, strategy, and dataset identities without loose inputs."""
    strategy = _strategy()
    specification = _run(strategy)
    run_store = _RunStore()
    strategy_store = _StrategyStore()
    dataset_store = _DatasetStore()
    run_fingerprint = "sha256:" + "9" * 64

    trace = asyncio.run(
        evaluate_published_signal_run(
            run_fingerprint,
            run_store=run_store,
            strategy_store=strategy_store,
            dataset_store=dataset_store,
        )
    )

    assert run_store.loaded == [run_fingerprint]
    assert strategy_store.loaded == [specification.strategy_fingerprint]
    assert dataset_store.loaded == [specification.dataset_fingerprint]
    assert len(trace.records) == 2
    assert Path("tests/research/golden/reference_signal_trace_v1.json").exists()
