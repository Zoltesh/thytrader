"""Authoritative loading and evaluation of published research-run specifications."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from thytrader.research.signal_evaluator import evaluate_signal_trace

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle
    from thytrader.research.publication import PublishedResearchRunSpecification
    from thytrader.research.trace import SignalTrace
    from thytrader.strategies.publication import PublishedStrategy


class VerifiedCandleReader(Protocol):
    """Read exact verified candles by immutable dataset identity."""

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Load and reverify one exact immutable candle dataset."""
        ...


_DatasetReaderT = TypeVar("_DatasetReaderT", bound=VerifiedCandleReader)


class PublishedRunReader(Protocol[_DatasetReaderT]):
    """Read and reverify immutable run publications against their dataset."""

    async def load(
        self,
        run_fingerprint_value: str,
        *,
        dataset_store: _DatasetReaderT,
    ) -> PublishedResearchRunSpecification:
        """Load one exact published run specification or fail closed."""
        ...


class PublishedStrategyReader(Protocol):
    """Read and reverify immutable strategy publications by identity."""

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Load one exact published strategy definition or fail closed."""
        ...


async def evaluate_published_signal_run(  # noqa: UP047 - tooling parses legacy generics.
    run_fingerprint: str,
    *,
    run_store: PublishedRunReader[_DatasetReaderT],
    strategy_store: PublishedStrategyReader,
    dataset_store: _DatasetReaderT,
) -> SignalTrace:
    """Load exact published artifacts and emit a deterministic signal trace."""
    published_run = await run_store.load(run_fingerprint, dataset_store=dataset_store)
    specification = published_run.specification
    published_strategy = await strategy_store.load(specification.strategy_fingerprint)
    candles = dataset_store.load_candles(specification.dataset_fingerprint)
    return evaluate_signal_trace(specification, published_strategy.definition, candles)
