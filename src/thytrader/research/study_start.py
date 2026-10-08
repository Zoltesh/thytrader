"""Agent/browser study start body: strategies by id, snapshotted by the server (ADR 0082).

Callers name strategies by ``strategy_id``. The server snapshots each one's
current (valid) definition and substitutes the snapshot fingerprints, producing
the internal :class:`~thytrader.research.studies.ResearchStudyRequest` that is
planned, fingerprinted, and stored as the async job payload.

ADR 0089 lets a start omit what the server can derive exactly: dataset
fingerprints bind to the newest complete catalog dataset per clock, omitted
evaluation bounds become the common window every child backtest can evaluate,
and a cross-market study may name one base ``strategy_id`` plus
``markets[].product_id`` so the server derives per-market variants. The internal
request always carries exact fingerprints and bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from thytrader.backtest.submission import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    resolve_backtest_window,
)
from thytrader.evaluation.models import (
    DecimalInputText,
    IndicatorTimeframeDataset,
    ReferenceInstrumentDataset,
    reject_removed_engine_selection,
)
from thytrader.evaluation.stress import ExecutionStress
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN
from thytrader.research.dataset_binding import (
    BoundDataset,
    ClockBindings,
    DatasetResolver,
    bind_product_clocks,
    bind_reference_datasets,
)
from thytrader.research.market_variants import MarketVariantError, derive_market_variant
from thytrader.research.parameter_sweep import (
    ParameterAxis,
    SelectionMetric,
    derive_parameter_candidates,
)
from thytrader.research.studies import (
    STUDY_CONTRACT_VERSION,
    FoldMode,
    ResearchStudyRequest,
    StudyKind,
    StudyPlanningError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.strategies.library import StrategyStore
    from thytrader.strategies.snapshots import StrategySnapshot, StrategySnapshotStore

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_BOUND_FIELDS = frozenset(
    {
        "strategy_id",
        "markets",
        "candidate_strategy_ids",
        "dataset_fingerprint",
        "htf_dataset_fingerprint",
        "indicator_dataset_fingerprints",
        "reference_dataset_fingerprints",
        "evaluation_start",
        "evaluation_end",
    }
)


class _FrozenStartModel(BaseModel):
    """Reject unknown fields and prevent mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StudyMarketStart(_FrozenStartModel):
    """One cross-market leg: an existing strategy, or one product of the base strategy.

    Set exactly one of ``strategy_id`` (a strategy already authored for this market)
    or ``product_id`` (the server derives a variant of the request's top-level
    ``strategy_id`` for that product). Omitted datasets bind from the catalog. A
    derived variant keeps the base strategy's reference instruments (BTC stays BTC).
    """

    strategy_id: UUID | None = None
    product_id: str | None = Field(default=None, pattern=SPOT_PRODUCT_ID_PATTERN)
    dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = ()
    reference_dataset_fingerprints: tuple[ReferenceInstrumentDataset, ...] = ()


class ResearchStudyStartRequest(_FrozenStartModel):
    """Study assumptions naming strategies by id; kind rules match ResearchStudyRequest."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    kind: StudyKind
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None
    initial_quote_balance: DecimalInputText
    maker_fee_rate: DecimalInputText
    taker_fee_rate: DecimalInputText
    fixed_slippage_bps: DecimalInputText
    spread_bps: DecimalInputText | None = None
    execution_stress: ExecutionStress | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    strategy_id: UUID | None = None
    dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = ()
    reference_dataset_fingerprints: tuple[ReferenceInstrumentDataset, ...] = ()
    oos_fraction: DecimalInputText | None = None
    embargo_bars: int = Field(default=0, ge=0, le=10_000)
    in_sample_bars: int | None = Field(default=None, ge=1, le=100_000)
    out_of_sample_bars: int | None = Field(default=None, ge=1, le=100_000)
    step_bars: int | None = Field(default=None, ge=1, le=100_000)
    fold_mode: FoldMode = FoldMode.ROLLING
    markets: tuple[StudyMarketStart, ...] | None = None
    candidate_strategy_ids: tuple[UUID, ...] = ()
    parameter_axes: tuple[ParameterAxis, ...] = ()
    selection_metric: SelectionMetric = SelectionMetric.TOTAL_RETURN_FRACTION

    @model_validator(mode="before")
    @classmethod
    def reject_engine_selection(cls, data: object) -> object:
        """Reject the retired engine selector with an explicit migration message."""
        return reject_removed_engine_selection(data)

    @model_validator(mode="after")
    def validate_start_shape(self) -> Self:
        """Require paired evaluation bounds and one consistent cross-market form."""
        if (self.evaluation_start is None) != (self.evaluation_end is None):
            raise ValueError("evaluation_start and evaluation_end must both be omitted or both set")
        _validate_market_forms(self)
        return self

    def strategy_ids(self) -> tuple[UUID, ...]:
        """Every strategy the study names, primary first, without duplicates."""
        ordered: dict[UUID, None] = {}
        if self.strategy_id is not None:
            ordered[self.strategy_id] = None
        for market in self.markets or ():
            if market.strategy_id is not None:
                ordered[market.strategy_id] = None
        for candidate in self.candidate_strategy_ids:
            ordered[candidate] = None
        return tuple(ordered)

    def primary_strategy_id(self) -> UUID:
        """The strategy the study is filed under (base strategy or first market)."""
        identities = self.strategy_ids()
        if not identities:
            raise StudyPlanningError("A study requires strategy_id or markets[].strategy_id.")
        return identities[0]


def _validate_market_forms(start: ResearchStudyStartRequest) -> None:
    """Accept per-market strategy ids, or one base strategy plus per-market product ids."""
    if start.markets is None:
        return
    if start.kind is not StudyKind.CROSS_MARKET:
        raise ValueError("markets is only valid for cross_market studies")
    if (
        start.dataset_fingerprint is not None
        or start.htf_dataset_fingerprint is not None
        or start.indicator_dataset_fingerprints
        or start.reference_dataset_fingerprints
    ):
        raise ValueError("cross_market studies bind datasets per market, under markets[]")
    for market in start.markets:
        if (market.strategy_id is None) == (market.product_id is None):
            raise ValueError("each market sets exactly one of strategy_id or product_id")
    by_product = [market.product_id for market in start.markets if market.product_id is not None]
    if by_product:
        if len(by_product) != len(start.markets) or start.strategy_id is None:
            raise ValueError(
                "markets[].product_id requires a top-level strategy_id and no markets[].strategy_id"
            )
        if len(set(by_product)) != len(by_product):
            raise ValueError("cross_market markets must use distinct product_id values")
    elif start.strategy_id is not None:
        raise ValueError(
            "a top-level strategy_id on cross_market requires markets[].product_id; "
            "per-market strategies use markets[].strategy_id only"
        )


@dataclass(frozen=True, slots=True)
class BoundStudyStart:
    """The exact internal study request plus every dataset the server bound for it."""

    request: ResearchStudyRequest
    bound_datasets: tuple[BoundDataset, ...]


@dataclass(frozen=True, slots=True)
class _Leg:
    """One strategy snapshot and the datasets its child backtests run on.

    ``references`` binds the strategy's read-only reference instruments (ADR 0096).
    """

    snapshot: StrategySnapshot
    clocks: ClockBindings
    references: tuple[ReferenceInstrumentDataset, ...] = ()


async def bind_study_start(
    start: ResearchStudyStartRequest,
    *,
    strategies: StrategyStore,
    publications: StrategySnapshotStore,
    resolver: DatasetResolver,
    datasets: DatasetStore | None,
) -> BoundStudyStart:
    """Snapshot, derive market variants, bind datasets, and fill bounds for one study start.

    Raises:
        StudyPlanningError: When no strategy is named, a market variant cannot be
            derived, or omitted bounds have no common evaluation window.
        DatasetsMissingError: When a required clock has no cataloged dataset.
        StrategyLibraryError: When a named strategy is missing or invalid.
        StrategySnapshotError: When a derived variant cannot be recorded.
    """
    start.primary_strategy_id()
    snapshots = {identity: await strategies.snapshot(identity) for identity in start.strategy_ids()}
    if start.kind is StudyKind.CROSS_MARKET:
        legs = await _bind_markets(start, snapshots, publications, resolver)
        resolver.require_complete()
        payload = _cross_market_payload(start, legs)
        children = legs
    else:
        base = snapshots[start.primary_strategy_id()]
        leg = _Leg(
            snapshot=base,
            clocks=bind_product_clocks(
                base.definition,
                base.definition.instrument.product_id,
                resolver,
                dataset_fingerprint=start.dataset_fingerprint,
                htf_dataset_fingerprint=start.htf_dataset_fingerprint,
                indicator_dataset_fingerprints=start.indicator_dataset_fingerprints,
            ),
            references=bind_reference_datasets(
                base.definition, resolver, explicit=start.reference_dataset_fingerprints
            ),
        )
        resolver.require_complete()
        payload = _single_market_payload(start, leg, snapshots)
        children = (leg, *_candidate_legs(start, leg, snapshots))
    evaluation_start, evaluation_end = _evaluation_window(start, children, datasets)
    payload["evaluation_start"] = evaluation_start
    payload["evaluation_end"] = evaluation_end
    return BoundStudyStart(
        request=ResearchStudyRequest.model_validate(payload),
        bound_datasets=resolver.bindings(),
    )


async def _bind_markets(
    start: ResearchStudyStartRequest,
    snapshots: dict[UUID, StrategySnapshot],
    publications: StrategySnapshotStore,
    resolver: DatasetResolver,
) -> tuple[_Leg, ...]:
    """Resolve each market to an exact snapshot and its datasets, in request order."""
    legs: list[_Leg] = []
    for market in start.markets or ():
        snapshot = await _market_snapshot(start, market, snapshots, publications)
        clocks = bind_product_clocks(
            snapshot.definition,
            snapshot.definition.instrument.product_id,
            resolver,
            dataset_fingerprint=market.dataset_fingerprint,
            htf_dataset_fingerprint=market.htf_dataset_fingerprint,
            indicator_dataset_fingerprints=market.indicator_dataset_fingerprints,
        )
        references = bind_reference_datasets(
            snapshot.definition, resolver, explicit=market.reference_dataset_fingerprints
        )
        legs.append(_Leg(snapshot=snapshot, clocks=clocks, references=references))
    return tuple(legs)


async def _market_snapshot(
    start: ResearchStudyStartRequest,
    market: StudyMarketStart,
    snapshots: dict[UUID, StrategySnapshot],
    publications: StrategySnapshotStore,
) -> StrategySnapshot:
    """Return the named strategy's snapshot, or record the base strategy's market variant."""
    if market.strategy_id is not None:
        return snapshots[market.strategy_id]
    if market.product_id is None or start.strategy_id is None:
        raise StudyPlanningError("markets[].product_id requires a top-level strategy_id.")
    base = snapshots[start.strategy_id]
    try:
        definition = derive_market_variant(base, market.product_id)
    except MarketVariantError as error:
        raise StudyPlanningError(str(error)) from error
    if definition == base.definition:
        return base
    return await publications.record_snapshot(definition)


def _candidate_legs(
    start: ResearchStudyStartRequest,
    base: _Leg,
    snapshots: dict[UUID, StrategySnapshot],
) -> tuple[_Leg, ...]:
    """Return sweep/WFO candidates on the base datasets, for the common-window check."""
    if start.parameter_axes:
        try:
            derived = derive_parameter_candidates(
                base.snapshot.definition,
                start.parameter_axes,
                base_fingerprint=base.snapshot.strategy_fingerprint,
            )
        except ValueError as error:
            raise StudyPlanningError(str(error)) from error
        return tuple(
            _Leg(snapshot=item, clocks=base.clocks, references=base.references) for item in derived
        )
    return tuple(
        _Leg(snapshot=snapshots[identity], clocks=base.clocks, references=base.references)
        for identity in start.candidate_strategy_ids
    )


def _common_payload(start: ResearchStudyStartRequest) -> dict[str, object]:
    """Copy every assumption the server does not bind or derive."""
    return start.model_dump(mode="python", exclude=set(_BOUND_FIELDS))


def _single_market_payload(
    start: ResearchStudyStartRequest,
    leg: _Leg,
    snapshots: dict[UUID, StrategySnapshot],
) -> dict[str, object]:
    """Build the internal single-market payload with exact fingerprints."""
    payload = _common_payload(start)
    payload["strategy_fingerprint"] = leg.snapshot.strategy_fingerprint
    payload["dataset_fingerprint"] = leg.clocks.dataset_fingerprint
    payload["htf_dataset_fingerprint"] = leg.clocks.htf_dataset_fingerprint
    payload["indicator_dataset_fingerprints"] = leg.clocks.indicator_dataset_fingerprints
    payload["reference_dataset_fingerprints"] = leg.references
    payload["candidate_strategy_fingerprints"] = tuple(
        snapshots[identity].strategy_fingerprint for identity in start.candidate_strategy_ids
    )
    payload["markets"] = None
    return payload


def _cross_market_payload(
    start: ResearchStudyStartRequest, legs: tuple[_Leg, ...]
) -> dict[str, object]:
    """Build the internal cross-market payload with one exact binding per market."""
    payload = _common_payload(start)
    payload["strategy_fingerprint"] = None
    payload["candidate_strategy_fingerprints"] = ()
    payload["markets"] = tuple(
        {
            "strategy_fingerprint": leg.snapshot.strategy_fingerprint,
            "dataset_fingerprint": leg.clocks.dataset_fingerprint,
            "htf_dataset_fingerprint": leg.clocks.htf_dataset_fingerprint,
            "indicator_dataset_fingerprints": leg.clocks.indicator_dataset_fingerprints,
            "reference_dataset_fingerprints": leg.references,
        }
        for leg in legs
    )
    return payload


def _evaluation_window(
    start: ResearchStudyStartRequest,
    children: Sequence[_Leg],
    datasets: DatasetStore | None,
) -> tuple[datetime, datetime]:
    """Return the requested bounds, or the window every child backtest can evaluate.

    Omitted bounds become the intersection of each child's default backtest window
    (dataset coverage after warmup, clipped to HTF and extra-clock coverage), the
    same rule a backtest with omitted bounds uses.
    """
    if start.evaluation_start is not None and start.evaluation_end is not None:
        return start.evaluation_start, start.evaluation_end
    if datasets is None:
        raise StudyPlanningError(
            "Omitted evaluation bounds need the dataset catalog; pass evaluation_start and "
            "evaluation_end."
        )
    windows = [_child_window(start, child, datasets) for child in children]
    starts_at = max(window[0] for window in windows)
    ends_at = min(window[1] for window in windows)
    if starts_at >= ends_at:
        raise StudyPlanningError(
            "The study's datasets have no common evaluation window after warmup; ingest "
            "overlapping history or pass evaluation_start and evaluation_end."
        )
    return starts_at, ends_at


def _child_window(
    start: ResearchStudyStartRequest, child: _Leg, datasets: DatasetStore
) -> tuple[datetime, datetime]:
    """Return one child's default backtest window or a planning rejection."""
    if child.clocks.dataset_fingerprint is None:
        raise StudyPlanningError("A study child has no bound dataset.")
    request = BacktestSubmissionRequest(
        strategy_fingerprint=child.snapshot.strategy_fingerprint,
        dataset_fingerprint=child.clocks.dataset_fingerprint,
        htf_dataset_fingerprint=child.clocks.htf_dataset_fingerprint,
        indicator_dataset_fingerprints=child.clocks.indicator_dataset_fingerprints,
        reference_dataset_fingerprints=child.references,
        initial_quote_balance=start.initial_quote_balance,
        maker_fee_rate=start.maker_fee_rate,
        taker_fee_rate=start.taker_fee_rate,
        fixed_slippage_bps=start.fixed_slippage_bps,
        spread_bps=start.spread_bps,
        execution_stress=start.execution_stress,
    )
    try:
        return resolve_backtest_window(request, child.snapshot, datasets)
    except BacktestSubmissionRejectedError as error:
        raise StudyPlanningError(str(error)) from error
