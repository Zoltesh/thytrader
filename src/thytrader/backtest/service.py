"""Authoritative loading, simulation, and append-only publication of research backtests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from thytrader.backtest.kernel import simulate_backtest
from thytrader.research.signal_evaluator import evaluate_signal_trace
from thytrader.research.trace import signal_trace_fingerprint
from thytrader.strategies.models import lockstep_product_ids

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestResult
    from thytrader.market_data.models import Candle
    from thytrader.research.models import ResearchRunSpecification
    from thytrader.research.publication import PublishedResearchRunSpecification
    from thytrader.research.trace import SignalTrace
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

    async def publish(self, result: BacktestResult, *, trace: SignalTrace) -> BacktestResult:
        """Persist one result only when the supplied canonical trace matches its identity."""
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
    htf_candles = _optional_htf_candles(dataset_store, specification)
    extra_candles = _indicator_timeframe_candles(dataset_store, specification)
    additional_candles = _additional_instrument_candles(dataset_store, specification)
    additional_htf = _additional_htf_candles(dataset_store, specification)
    additional_indicator = _additional_indicator_candles(dataset_store, specification)
    definition = published_strategy.definition
    trace = evaluate_signal_trace(
        specification, definition, candles, htf_candles, extra_candles
    )
    for product_id in lockstep_product_ids(definition):
        if product_id == definition.instrument.product_id:
            continue
        evaluate_signal_trace(
            specification,
            definition,
            additional_candles[product_id],
            additional_htf.get(product_id, ()),
            additional_indicator.get(product_id),
        )
    result = simulate_backtest(
        specification,
        definition,
        candles,
        htf_candles,
        extra_candles,
        additional_candles,
        additional_htf,
        additional_indicator,
    )
    if result.signal_trace_fingerprint != signal_trace_fingerprint(trace):
        raise RuntimeError(
            "Backtest trace identity did not match the authoritative signal evaluation."
        )
    return await result_store.publish(result, trace=trace)


def _optional_htf_candles(
    dataset_store: VerifiedCandleReader, specification: ResearchRunSpecification
) -> tuple[Candle, ...]:
    """Load the HTF dataset when the research run fingerprints one."""
    fingerprint = specification.htf_dataset_fingerprint
    if fingerprint is None:
        return ()
    return dataset_store.load_candles(fingerprint)


def _indicator_timeframe_candles(
    dataset_store: VerifiedCandleReader, specification: ResearchRunSpecification
) -> dict[str, tuple[Candle, ...]]:
    """Load extra indicator-timeframe datasets when the research run fingerprints them."""
    return {
        item.timeframe: dataset_store.load_candles(item.dataset_fingerprint)
        for item in specification.indicator_dataset_fingerprints
    }


def _additional_instrument_candles(
    dataset_store: VerifiedCandleReader, specification: ResearchRunSpecification
) -> dict[str, tuple[Candle, ...]]:
    """Load extra covered-product decision datasets."""
    return {
        item.product_id: dataset_store.load_candles(item.dataset_fingerprint)
        for item in specification.additional_instrument_datasets
    }


def _additional_htf_candles(
    dataset_store: VerifiedCandleReader, specification: ResearchRunSpecification
) -> dict[str, tuple[Candle, ...]]:
    """Load extra covered-product HTF datasets when fingerprinted."""
    loaded: dict[str, tuple[Candle, ...]] = {}
    for item in specification.additional_instrument_datasets:
        if item.htf_dataset_fingerprint is None:
            continue
        loaded[item.product_id] = dataset_store.load_candles(item.htf_dataset_fingerprint)
    return loaded


def _additional_indicator_candles(
    dataset_store: VerifiedCandleReader, specification: ResearchRunSpecification
) -> dict[str, dict[str, tuple[Candle, ...]]]:
    """Load extra covered-product extra-TF datasets when fingerprinted."""
    loaded: dict[str, dict[str, tuple[Candle, ...]]] = {}
    for item in specification.additional_instrument_datasets:
        clocks = {
            clock.timeframe: dataset_store.load_candles(clock.dataset_fingerprint)
            for clock in item.indicator_dataset_fingerprints
        }
        if clocks:
            loaded[item.product_id] = clocks
    return loaded
