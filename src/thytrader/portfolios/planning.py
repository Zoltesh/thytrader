"""Resolve a portfolio backtest request into a complete, deterministic plan (ADR 0088).

Resolution happens when the request is accepted, so rejections come back immediately
(HTTP 422) and the queued job is reproducible from its payload alone:

1. Every sleeve's strategy is snapshotted (it must currently validate) and must be quoted
   in the portfolio's quote currency.
2. Each sleeve binds request-supplied dataset fingerprints, or else the latest verified
   complete Coinbase dataset for every clock its strategy declares: decision, HTF filter,
   extra indicator clocks, and additional instruments (with their own HTF/extra clocks).
3. Each sleeve's usable window is resolved exactly as a single backtest would resolve it;
   the common window is their intersection, aligned to the least common multiple of the
   sleeves' bar durations so every sleeve evaluates the same bars. A supplied window is
   verified for every sleeve instead.
4. Each child submission carries ``capital = weight * capital_quote`` and the shared costs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import lcm
from typing import TYPE_CHECKING

from thytrader.backtest.submission import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    resolve_backtest_window,
)
from thytrader.evaluation.models import (
    AdditionalInstrumentDataset,
    IndicatorTimeframeDataset,
    ReferenceInstrumentDataset,
)
from thytrader.market_data.models import as_dataset_timeframe, parse_candle_interval
from thytrader.portfolios.allocation import sleeve_capital
from thytrader.portfolios.backtest import (
    PlannedSleeve,
    PortfolioBacktestPlan,
    PortfolioBacktestRequest,
    SleeveDatasetOverride,
)
from thytrader.portfolios.errors import PortfolioError
from thytrader.portfolios.models import sleeve_issues, utc_text
from thytrader.portfolios.rules import require_revision
from thytrader.strategies.library import StrategyInvalidError, StrategyNotFoundError
from thytrader.strategies.models import (
    covered_product_ids,
    lockstep_product_ids,
    reference_instruments,
    unbound_indicator_timeframes,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.portfolios.models import PortfolioAggregate, SleeveView
    from thytrader.strategies.library import StrategyStore
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import StrategySnapshot

_PROVIDER = "coinbase"


@dataclass(frozen=True, slots=True)
class SleeveProblem:
    """One reason a sleeve cannot join a portfolio backtest."""

    code: str
    message: str
    sleeve_id: UUID | None = None
    strategy_id: UUID | None = None
    strategy_name: str | None = None


class PortfolioBacktestRejectedError(PortfolioError):
    """The request cannot run as given; nothing was queued or published."""

    def __init__(self, message: str, problems: tuple[SleeveProblem, ...] = ()) -> None:
        """Keep each sleeve's problem beside the summary message."""
        super().__init__(message)
        self.problems = problems


@dataclass(frozen=True, slots=True)
class _Bindings:
    """Every dataset fingerprint one child submission binds."""

    dataset_fingerprint: str
    htf_dataset_fingerprint: str | None
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...]
    additional_instrument_datasets: tuple[AdditionalInstrumentDataset, ...]
    reference_dataset_fingerprints: tuple[ReferenceInstrumentDataset, ...] = ()


@dataclass(frozen=True, slots=True)
class _Child:
    """One sleeve's snapshot and undated child submission."""

    view: SleeveView
    snapshot: StrategySnapshot
    submission: BacktestSubmissionRequest


async def plan_portfolio_backtest(
    aggregate: PortfolioAggregate,
    request: PortfolioBacktestRequest,
    *,
    strategies: StrategyStore,
    datasets: DatasetStore,
) -> PortfolioBacktestPlan:
    """Snapshot every sleeve, bind datasets, and fix one common evaluation window."""
    portfolio = aggregate.portfolio
    if request.revision is not None:
        require_revision(portfolio, request.revision)
    if not aggregate.sleeves:
        raise PortfolioBacktestRejectedError(
            "Add at least one sleeve before running a portfolio backtest."
        )
    _require_known_overrides(aggregate, request.datasets)
    _require_sleeve_markets(aggregate)
    snapshots = await _snapshots(aggregate, strategies)
    return await asyncio.to_thread(_resolve_plan, aggregate, request, snapshots, datasets)


def _require_known_overrides(
    aggregate: PortfolioAggregate, overrides: tuple[SleeveDatasetOverride, ...]
) -> None:
    """Refuse dataset overrides for strategies that are not sleeves."""
    known = {view.sleeve.strategy_id for view in aggregate.sleeves}
    unknown = [item.strategy_id for item in overrides if item.strategy_id not in known]
    if unknown:
        raise PortfolioBacktestRejectedError(
            "datasets name strategies that are not sleeves of this portfolio: "
            + ", ".join(str(item) for item in unknown)
            + "."
        )


def _require_sleeve_markets(aggregate: PortfolioAggregate) -> None:
    """Refuse sleeves whose strategy market is unknown or in another quote currency."""
    quote = aggregate.portfolio.quote_currency
    problems: list[SleeveProblem] = []
    for view in aggregate.sleeves:
        issues = sleeve_issues(view, quote)
        if "product_unknown" in issues:
            problems.append(
                _problem(view, "product_unknown", "The strategy has no readable market.")
            )
        elif "quote_currency_mismatch" in issues:
            problems.append(
                _problem(
                    view,
                    "quote_currency_mismatch",
                    f"The strategy now trades in {view.strategy.quote_currency}; the portfolio "
                    f"holds {quote}.",
                )
            )
    if problems:
        raise PortfolioBacktestRejectedError(
            "Some sleeves cannot run in this portfolio.", tuple(problems)
        )


async def _snapshots(
    aggregate: PortfolioAggregate, strategies: StrategyStore
) -> tuple[StrategySnapshot, ...]:
    """Snapshot every sleeve's current rules; collect invalid or missing strategies."""
    snapshots: list[StrategySnapshot] = []
    problems: list[SleeveProblem] = []
    for view in aggregate.sleeves:
        try:
            snapshots.append(await strategies.snapshot(view.sleeve.strategy_id))
        except StrategyInvalidError as error:
            problems.append(_problem(view, "strategy_invalid", str(error)))
        except StrategyNotFoundError:
            problems.append(_problem(view, "strategy_not_found", "The strategy was deleted."))
    if problems:
        raise PortfolioBacktestRejectedError(
            "Every sleeve's strategy must currently validate.", tuple(problems)
        )
    return tuple(snapshots)


def _resolve_plan(
    aggregate: PortfolioAggregate,
    request: PortfolioBacktestRequest,
    snapshots: tuple[StrategySnapshot, ...],
    datasets: DatasetStore,
) -> PortfolioBacktestPlan:
    """Bind datasets, fix the window, and verify every dated child submission."""
    children = _children(aggregate, request, snapshots, datasets)
    if request.evaluation_start is not None and request.evaluation_end is not None:
        start, end = request.evaluation_start, request.evaluation_end
    else:
        start, end = _common_window(children, datasets)
    planned = tuple(_planned_sleeve(child, start, end, datasets) for child in children)
    portfolio = aggregate.portfolio
    return PortfolioBacktestPlan(
        portfolio_id=portfolio.portfolio_id,
        portfolio_revision=portfolio.revision,
        portfolio_name=portfolio.name,
        mode=portfolio.mode,
        quote_currency=portfolio.quote_currency,
        capital_quote=portfolio.capital_quote,
        cash_reserve_fraction=portfolio.cash_reserve_fraction,
        evaluation_start=start,
        evaluation_end=end,
        costs=request.costs(),
        sleeves=planned,
    )


def _children(
    aggregate: PortfolioAggregate,
    request: PortfolioBacktestRequest,
    snapshots: tuple[StrategySnapshot, ...],
    datasets: DatasetStore,
) -> tuple[_Child, ...]:
    """Build every sleeve's undated child submission, collecting binding problems."""
    overrides = {item.strategy_id: item for item in request.datasets}
    latest: Mapping[tuple[str, str], str] | None = None
    portfolio = aggregate.portfolio
    children: list[_Child] = []
    problems: list[SleeveProblem] = []
    for view, snapshot in zip(aggregate.sleeves, snapshots, strict=True):
        definition = snapshot.definition
        if definition.instrument.quote_currency != portfolio.quote_currency:
            problems.append(
                _problem(view, "quote_currency_mismatch", "The strategy's quote currency changed.")
            )
            continue
        override = overrides.get(view.sleeve.strategy_id)
        if override is None and latest is None:
            latest = _latest_datasets(datasets)
        try:
            bindings = (
                _override_bindings(override)
                if override is not None
                else _latest_bindings(definition, latest or {})
            )
        except _MissingDatasetsError as missing:
            problems.append(_problem(view, "dataset_missing", str(missing)))
            continue
        submission = BacktestSubmissionRequest(
            strategy_fingerprint=snapshot.strategy_fingerprint,
            dataset_fingerprint=bindings.dataset_fingerprint,
            htf_dataset_fingerprint=bindings.htf_dataset_fingerprint,
            indicator_dataset_fingerprints=bindings.indicator_dataset_fingerprints,
            additional_instrument_datasets=bindings.additional_instrument_datasets,
            reference_dataset_fingerprints=bindings.reference_dataset_fingerprints,
            evaluation_start=request.evaluation_start,
            evaluation_end=request.evaluation_end,
            initial_quote_balance=sleeve_capital(
                portfolio.capital_quote, view.sleeve.weight_fraction
            ),
            maker_fee_rate=request.maker_fee_rate,
            taker_fee_rate=request.taker_fee_rate,
            fixed_slippage_bps=request.fixed_slippage_bps,
            spread_bps=request.spread_bps,
        )
        children.append(_Child(view=view, snapshot=snapshot, submission=submission))
    if problems:
        raise PortfolioBacktestRejectedError(
            "Some sleeves have no usable datasets.", tuple(problems)
        )
    return tuple(children)


class _MissingDatasetsError(ValueError):
    """No verified dataset exists for one or more clocks a strategy declares."""


def _latest_datasets(datasets: DatasetStore) -> dict[tuple[str, str], str]:
    """Index the newest verified complete Coinbase dataset per (product, timeframe)."""
    return {
        (manifest.product_id, manifest.timeframe): manifest.content_fingerprint
        for manifest in datasets.list_latest_verified()
        if manifest.provider == _PROVIDER and manifest.complete
    }


def _override_bindings(override: SleeveDatasetOverride) -> _Bindings:
    """Use the caller's exact fingerprints (verified later like any backtest request)."""
    return _Bindings(
        dataset_fingerprint=override.dataset_fingerprint,
        htf_dataset_fingerprint=override.htf_dataset_fingerprint,
        indicator_dataset_fingerprints=override.indicator_dataset_fingerprints,
        additional_instrument_datasets=override.additional_instrument_datasets,
        reference_dataset_fingerprints=override.reference_dataset_fingerprints,
    )


def _latest_bindings(
    definition: StrategyDefinition, latest: Mapping[tuple[str, str], str]
) -> _Bindings:
    """Bind the latest dataset for every clock and product the strategy declares."""
    missing: list[str] = []

    def pick(product_id: str, timeframe: str, role: str) -> str:
        """Return one fingerprint or record what is missing."""
        fingerprint = latest.get((product_id, timeframe))
        if fingerprint is None:
            missing.append(f"{product_id} {timeframe} ({role})")
            return ""
        return fingerprint

    primary = definition.instrument.product_id
    clocks = unbound_indicator_timeframes(definition)
    htf = definition.htf_filter
    decision = pick(primary, definition.timeframe, "decision clock")
    htf_fingerprint = pick(primary, htf.timeframe, "HTF filter") if htf is not None else None
    indicator = tuple((clock, pick(primary, clock, "indicator clock")) for clock in clocks)
    additional = tuple(
        (
            product_id,
            pick(product_id, definition.timeframe, "additional instrument"),
            pick(product_id, htf.timeframe, "additional instrument HTF")
            if htf is not None
            else None,
            tuple(
                (clock, pick(product_id, clock, "additional instrument clock")) for clock in clocks
            ),
        )
        for product_id in lockstep_product_ids(definition)
        if product_id != primary
    )
    references = tuple(
        (
            reference,
            pick(reference.product_id, reference.timeframe, f"reference instrument {reference.id}"),
        )
        for reference in reference_instruments(definition)
    )
    if missing:
        raise _MissingDatasetsError(
            "No verified complete dataset for " + ", ".join(missing) + ". Ingest it with "
            "thytrader-data, or pass exact fingerprints in datasets."
        )
    return _Bindings(
        dataset_fingerprint=decision,
        htf_dataset_fingerprint=htf_fingerprint,
        indicator_dataset_fingerprints=_clock_bindings(indicator),
        additional_instrument_datasets=tuple(
            AdditionalInstrumentDataset(
                product_id=product_id,
                dataset_fingerprint=fingerprint,
                htf_dataset_fingerprint=htf_value,
                indicator_dataset_fingerprints=_clock_bindings(extra_clocks),
            )
            for product_id, fingerprint, htf_value, extra_clocks in additional
        ),
        reference_dataset_fingerprints=tuple(
            ReferenceInstrumentDataset(
                reference_id=reference.id,
                product_id=reference.product_id,
                timeframe=reference.timeframe,
                dataset_fingerprint=fingerprint,
            )
            for reference, fingerprint in references
        ),
    )


def _clock_bindings(pairs: tuple[tuple[str, str], ...]) -> tuple[IndicatorTimeframeDataset, ...]:
    """Build extra-clock bindings in the strategy's declared clock order."""
    return tuple(
        IndicatorTimeframeDataset(
            timeframe=as_dataset_timeframe(parse_candle_interval(clock)),
            dataset_fingerprint=fingerprint,
        )
        for clock, fingerprint in pairs
    )


def _common_window(
    children: tuple[_Child, ...], datasets: DatasetStore
) -> tuple[datetime, datetime]:
    """Intersect every sleeve's usable window and align it to the common bar clock."""
    windows: list[tuple[_Child, datetime, datetime]] = []
    problems: list[SleeveProblem] = []
    for child in children:
        try:
            start, end = resolve_backtest_window(child.submission, child.snapshot, datasets)
        except BacktestSubmissionRejectedError as rejected:
            problems.append(_problem(child.view, "window_rejected", str(rejected)))
            continue
        windows.append((child, start, end))
    if problems:
        raise PortfolioBacktestRejectedError(
            "Some sleeves' datasets cannot support a backtest.", tuple(problems)
        )
    clock = lcm(*(_bar_seconds(child.snapshot.definition.timeframe) for child in children))
    start = _align(max(item[1] for item in windows), clock, up=True)
    end = _align(min(item[2] for item in windows), clock, up=False)
    if start >= end:
        raise PortfolioBacktestRejectedError(
            "The sleeves' datasets share no common evaluation window. Ingest overlapping "
            "history or remove a sleeve.",
            tuple(
                _problem(
                    child.view,
                    "no_common_window",
                    f"Usable window {utc_text(child_start)} → {utc_text(child_end)}.",
                )
                for child, child_start, child_end in windows
            ),
        )
    return start, end


def _bar_seconds(timeframe: str) -> int:
    """Return one clock's bar length in whole seconds."""
    return int(parse_candle_interval(timeframe).duration.total_seconds())


def _align(instant: datetime, seconds: int, *, up: bool) -> datetime:
    """Align a UTC instant to a multiple of ``seconds`` since the Unix epoch."""
    offset = int(instant.timestamp()) % seconds
    if offset == 0:
        return instant
    if up:
        return instant + timedelta(seconds=seconds - offset)
    return instant - timedelta(seconds=offset)


def _planned_sleeve(
    child: _Child, start: datetime, end: datetime, datasets: DatasetStore
) -> PlannedSleeve:
    """Date one child submission and verify it like a single backtest would."""
    dated = child.submission.model_copy(update={"evaluation_start": start, "evaluation_end": end})
    try:
        resolve_backtest_window(dated, child.snapshot, datasets)
    except BacktestSubmissionRejectedError as rejected:
        raise PortfolioBacktestRejectedError(
            "The evaluation window does not fit every sleeve.",
            (_problem(child.view, "window_rejected", str(rejected)),),
        ) from rejected
    definition = child.snapshot.definition
    view = child.view
    return PlannedSleeve(
        sleeve_id=view.sleeve.sleeve_id,
        strategy_id=view.sleeve.strategy_id,
        strategy_name=definition.name,
        strategy_fingerprint=child.snapshot.strategy_fingerprint,
        product_id=definition.instrument.product_id,
        covered_product_ids=covered_product_ids(definition),
        timeframe=definition.timeframe,
        weight_fraction=view.sleeve.weight_fraction,
        capital_quote=dated.initial_quote_balance,
        submission=dated,
    )


def _problem(view: SleeveView, code: str, message: str) -> SleeveProblem:
    """Attach sleeve identity to one problem."""
    return SleeveProblem(
        code=code,
        message=message,
        sleeve_id=view.sleeve.sleeve_id,
        strategy_id=view.sleeve.strategy_id,
        strategy_name=view.strategy.name,
    )
