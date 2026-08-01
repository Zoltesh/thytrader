"""Authoritative loading, simulation, and append-only publication of research backtests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from thytrader.backtest.kernel import simulate_backtest

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestResult
    from thytrader.market_data.models import Candle
    from thytrader.research.publication import PublishedResearchRunSpecification
    from thytrader.strategies.publication import PublishedStrategy


class VerifiedCandleReader(Protocol):
    """Read one exact verified immutable candle dataset by fingerprint."""

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Load and cryptographically reverify exact immutable candle content."""
        ...


_DatasetReaderT = TypeVar("_DatasetReaderT", bound=VerifiedCandleReader)


class PublishedRunReader(Protocol[_DatasetReaderT]):
    """Read and reverify one immutable published research run against its dataset."""

    async def load(
        self,
        run_fingerprint_value: str,
        *,
        dataset_store: _DatasetReaderT,
    ) -> PublishedResearchRunSpecification:
        """Load one exact published run or fail closed."""
        ...


class PublishedStrategyReader(Protocol):
    """Read and reverify an immutable strategy definition by fingerprint."""

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Load one exact published strategy or fail closed."""
        ...


class BacktestResultWriter(Protocol):
    """Append one verified canonical simulation result."""

    async def publish(self, result: BacktestResult) -> BacktestResult:
        """Persist one result idempotently and return its reverified canonical form."""
        ...


async def evaluate_and_publish_backtest(  # noqa: UP047 - tooling parses legacy generics.
    run_fingerprint: str,
    *,
    run_store: PublishedRunReader[_DatasetReaderT],
    strategy_store: PublishedStrategyReader,
    dataset_store: _DatasetReaderT,
    result_store: BacktestResultWriter,
) -> BacktestResult:
    """Load exact source publications, simulate deterministically, then append the result."""
    published_run = await run_store.load(run_fingerprint, dataset_store=dataset_store)
    specification = published_run.specification
    published_strategy = await strategy_store.load(specification.strategy_fingerprint)
    candles = dataset_store.load_candles(specification.dataset_fingerprint)
    result = simulate_backtest(specification, published_strategy.definition, candles)
    return await result_store.publish(result)
