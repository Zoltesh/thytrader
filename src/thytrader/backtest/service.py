"""Authoritative loading, simulation, and append-only publication of research backtests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol, TypeVar

from thytrader.backtest.kernel import simulate_backtest_with_diagnostics
from thytrader.backtest.submission_futures import load_funding_rates
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.evaluation.trace import signal_trace_fingerprint
from thytrader.strategies.models import lockstep_product_ids

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal

    from thytrader.backtest.models import BacktestDiagnostics, BacktestResult
    from thytrader.backtest.submission_futures import FuturesRunSource
    from thytrader.evaluation.models import ResearchRunSpecification
    from thytrader.evaluation.publication import PublishedResearchRunSpecification
    from thytrader.evaluation.trace import SignalTrace
    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import StrategySnapshot


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

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Load one exact published strategy or fail closed."""
        ...


class BacktestResultWriter(Protocol):
    """Append one verified canonical simulation result."""

    async def publish(
        self,
        result: BacktestResult,
        *,
        trace: SignalTrace,
        diagnostics: BacktestDiagnostics | None = None,
    ) -> BacktestResult:
        """Persist one result only when the supplied canonical trace matches its identity.

        ``diagnostics`` are stored beside the result, never inside its canonical bytes.
        """
        ...


async def evaluate_and_publish_backtest(  # noqa: UP047 - tooling parses legacy generics.
    run_fingerprint: str,
    *,
    run_store: PublishedRunReader[_DatasetReaderT],
    strategy_store: PublishedStrategyReader,
    dataset_store: _DatasetReaderT,
    result_store: BacktestResultWriter,
    futures_source: FuturesRunSource | None = None,
) -> BacktestResult:
    """Load exact source publications, simulate deterministically, then append the result.

    A perp run bound to a recorded funding series reads its settled hours from
    ``futures_source``; the kernel verifies them against the bound fingerprint and fails
    ``FUNDING_HISTORY_MISSING`` when no source or no row is available (ADR 0128).
    """
    published_run = await run_store.load(run_fingerprint, dataset_store=dataset_store)
    specification = published_run.specification
    published_strategy = await strategy_store.load(specification.strategy_fingerprint)
    definition = published_strategy.definition
    funding_rates = await _recorded_funding(specification, futures_source)
    # Dataset reverification, signal-trace evaluation, and bar-level simulation are
    # synchronous CPU-bound work. A large study run inline on the event loop would
    # delay concurrent pause/stop/status requests handled by the same API process
    # (audit F16). Run the bounded blocking segment on a worker thread instead.
    result, diagnostics, trace = await asyncio.to_thread(
        _load_and_simulate, dataset_store, specification, definition, funding_rates
    )
    if result.signal_trace_fingerprint != signal_trace_fingerprint(trace):
        raise RuntimeError(
            "Backtest trace identity did not match the authoritative signal evaluation."
        )
    return await result_store.publish(result, trace=trace, diagnostics=diagnostics)


def _load_and_simulate(
    dataset_store: VerifiedCandleReader,
    specification: ResearchRunSpecification,
    definition: StrategyDefinition,
    funding_rates: Mapping[datetime, Decimal] | None = None,
) -> tuple[BacktestResult, BacktestDiagnostics, SignalTrace]:
    """Reverify datasets and run the deterministic simulation off the event loop."""
    candles = dataset_store.load_candles(specification.dataset_fingerprint)
    htf_candles = _optional_htf_candles(dataset_store, specification)
    extra_candles = _indicator_timeframe_candles(dataset_store, specification)
    additional_candles = _additional_instrument_candles(dataset_store, specification)
    additional_htf = _additional_htf_candles(dataset_store, specification)
    additional_indicator = _additional_indicator_candles(dataset_store, specification)
    references = reference_instrument_candles(dataset_store, specification)
    trace = evaluate_signal_trace(
        specification,
        definition,
        candles,
        htf_candles,
        extra_candles,
        reference_candles=references,
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
            reference_candles=references,
        )
    result, diagnostics = simulate_backtest_with_diagnostics(
        specification,
        definition,
        candles,
        htf_candles,
        extra_candles,
        additional_candles,
        additional_htf,
        additional_indicator,
        reference_candles=references,
        funding_rates=funding_rates,
    )
    return result, diagnostics, trace


async def _recorded_funding(
    specification: ResearchRunSpecification, source: FuturesRunSource | None
) -> Mapping[datetime, Decimal] | None:
    """The settled funding rates a series-bound perp run consumes, else None."""
    funding = specification.funding
    contract = specification.instrument_contract
    if funding is None or funding.series_fingerprint is None or contract is None:
        return None
    if source is None:
        return None
    rates, _missing = await load_funding_rates(
        source,
        contract.product_id,
        specification.evaluation.starts_at,
        specification.evaluation.ends_at,
    )
    return rates


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


def reference_instrument_candles(
    dataset_store: VerifiedCandleReader, specification: ResearchRunSpecification
) -> dict[str, tuple[Candle, ...]]:
    """Load each bound reference-instrument dataset by reference id (ADR 0096)."""
    return {
        item.reference_id: dataset_store.load_candles(item.dataset_fingerprint)
        for item in specification.reference_dataset_fingerprints
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
        clocks: dict[str, tuple[Candle, ...]] = {
            clock.timeframe: dataset_store.load_candles(clock.dataset_fingerprint)
            for clock in item.indicator_dataset_fingerprints
        }
        if clocks:
            loaded[item.product_id] = clocks
    return loaded
