"""Walk-forward, OOS holdout, and cross-market research study composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.backtest.models import (
    BacktestEngineContract,  # noqa: TC001 - Pydantic model field.
    BacktestSummary,  # noqa: TC001 - Pydantic model field.
)
from thytrader.backtest.submission import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
)
from thytrader.market_data.models import DatasetTimeframe, parse_candle_interval
from thytrader.research.models import EvaluationWindow, IndicatorTimeframeDataset

if TYPE_CHECKING:
    from thytrader.backtest.submission import BacktestSubmitter
    from thytrader.persistence.backtest_results import BacktestResultReader
    from thytrader.strategies.publication import PublishedStrategy, StrategyPublicationStore

STUDY_CONTRACT_VERSION = "thytrader-research-study-v1"
_FINGERPRINT_PREFIX = "sha256:"
_MAX_FOLDS = 24
_MAX_MARKETS = 8
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


class StudyKind(StrEnum):
    """Fail-closed research-study kinds for Phase 11."""

    OOS_HOLDOUT = "oos_holdout"
    WALK_FORWARD = "walk_forward"
    CROSS_MARKET = "cross_market"


class FoldMode(StrEnum):
    """Walk-forward IS window motion."""

    ROLLING = "rolling"
    ANCHORED = "anchored"


class WindowRole(StrEnum):
    """How one child backtest participates in the study."""

    IN_SAMPLE = "in_sample"
    OUT_OF_SAMPLE = "out_of_sample"
    FULL_WINDOW = "full_window"


class StudyPlanningError(ValueError):
    """Reject a study request that cannot form a valid window schedule."""


class ResearchStudyError(RuntimeError):
    """Report a redacted study failure without trading authority."""


class _FrozenStudyModel(BaseModel):
    """Reject unknown fields and prevent mutation of study documents."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketBinding(_FrozenStudyModel):
    """One published single-instrument strategy bound to a verified dataset."""

    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    dataset_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )


class ResearchStudyRequest(_FrozenStudyModel):
    """Typed study assumptions. Does not retune strategy parameters."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    kind: StudyKind
    evaluation_start: datetime
    evaluation_end: datetime
    initial_quote_balance: str
    maker_fee_rate: str
    taker_fee_rate: str
    fixed_slippage_bps: str
    engine_contract_version: BacktestEngineContract
    spread_bps: str | None = None
    strategy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    oos_fraction: str | None = None
    embargo_bars: int = Field(default=0, ge=0, le=10_000)
    in_sample_bars: int | None = Field(default=None, ge=1, le=100_000)
    out_of_sample_bars: int | None = Field(default=None, ge=1, le=100_000)
    step_bars: int | None = Field(default=None, ge=1, le=100_000)
    fold_mode: FoldMode = FoldMode.ROLLING
    markets: tuple[MarketBinding, ...] | None = None

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize study bounds with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @field_validator("oos_fraction")
    @classmethod
    def validate_oos_fraction_text(cls, value: str | None) -> str | None:
        """Reject non-decimal OOS fractions before kind-specific bounds."""
        if value is None:
            return value
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("oos_fraction must be a plain decimal string") from error
        if parsed <= 0 or parsed >= 1:
            raise ValueError("oos_fraction must be greater than 0 and less than 1")
        return value

    @model_validator(mode="after")
    def validate_kind_fields(self) -> Self:
        """Require kind-specific fields and reject combinations that mix study types."""
        EvaluationWindow(starts_at=self.evaluation_start, ends_at=self.evaluation_end)
        if self.kind is StudyKind.CROSS_MARKET:
            _require_cross_market(self)
            return self
        _require_single_market(self)
        if self.kind is StudyKind.OOS_HOLDOUT:
            _require_oos_holdout(self)
            return self
        _require_walk_forward(self)
        return self


class PlannedStudyWindow(_FrozenStudyModel):
    """One child evaluation window before submission."""

    label: str
    role: WindowRole
    fold_index: int = Field(ge=0)
    product_id: str
    timeframe: DatasetTimeframe
    strategy_fingerprint: str
    dataset_fingerprint: str
    htf_dataset_fingerprint: str | None = None
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    evaluation_start: datetime
    evaluation_end: datetime

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize planned bounds with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ResearchStudyPlan(_FrozenStudyModel):
    """Deterministic window schedule without simulation."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    kind: StudyKind
    request_fingerprint: str
    timeframe: DatasetTimeframe
    windows: tuple[PlannedStudyWindow, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()


class StudyWindowResult(_FrozenStudyModel):
    """One submitted child backtest identity plus its immutable summary."""

    label: str
    role: WindowRole
    fold_index: int = Field(ge=0)
    product_id: str
    run_fingerprint: str
    result_fingerprint: str
    evaluation_start: datetime
    evaluation_end: datetime
    summary: BacktestSummary

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize child bounds with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class StudyAggregate(_FrozenStudyModel):
    """Disclosed OOS statistics that are not a stitched equity curve."""

    window_count: int = Field(ge=1)
    oos_window_count: int = Field(ge=0)
    oos_trade_count: int = Field(ge=0)
    oos_winning_trade_count: int = Field(ge=0)
    oos_win_rate: str | None = None
    mean_oos_return_fraction: str | None = None
    mean_oos_drawdown_fraction: str | None = None
    mean_is_return_fraction: str | None = None
    is_oos_return_gap: str | None = None


class ResearchStudy(_FrozenStudyModel):
    """Canonical derived study after child backtests exist."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    study_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    kind: StudyKind
    engine_contract_version: BacktestEngineContract
    windows: tuple[StudyWindowResult, ...] = Field(min_length=1)
    aggregate: StudyAggregate
    warnings: tuple[str, ...] = ()


def request_fingerprint(request: ResearchStudyRequest) -> str:
    """Return the SHA-256 identity of the canonical study request."""
    payload = request.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical.encode()).hexdigest()}"


def study_fingerprint(study: ResearchStudy) -> str:
    """Return the SHA-256 identity of the derived study excluding its own fingerprint."""
    payload = study.model_dump(mode="json", exclude_none=True)
    payload.pop("study_fingerprint", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical.encode()).hexdigest()}"


def plan_study(
    request: ResearchStudyRequest,
    *,
    publications: dict[str, PublishedStrategy],
) -> ResearchStudyPlan:
    """Build the child window schedule from published strategy metadata."""
    warnings: list[str] = []
    if request.kind is StudyKind.CROSS_MARKET:
        windows, timeframe = _plan_cross_market(request, publications)
    else:
        windows, timeframe = _plan_single_market(request, publications, warnings)
    if not windows:
        raise StudyPlanningError("The evaluation window cannot form any study child windows.")
    return ResearchStudyPlan(
        kind=request.kind,
        request_fingerprint=request_fingerprint(request),
        timeframe=timeframe,
        windows=tuple(windows),
        warnings=tuple(warnings),
    )


def window_submission_request(
    request: ResearchStudyRequest,
    window: PlannedStudyWindow,
) -> BacktestSubmissionRequest:
    """Map one planned window onto the existing backtest submission contract."""
    return BacktestSubmissionRequest(
        strategy_fingerprint=window.strategy_fingerprint,
        dataset_fingerprint=window.dataset_fingerprint,
        htf_dataset_fingerprint=window.htf_dataset_fingerprint,
        indicator_dataset_fingerprints=window.indicator_dataset_fingerprints,
        evaluation_start=window.evaluation_start,
        evaluation_end=window.evaluation_end,
        initial_quote_balance=request.initial_quote_balance,
        maker_fee_rate=request.maker_fee_rate,
        taker_fee_rate=request.taker_fee_rate,
        fixed_slippage_bps=request.fixed_slippage_bps,
        engine_contract_version=request.engine_contract_version,
        spread_bps=request.spread_bps,
    )


def aggregate_windows(
    windows: tuple[StudyWindowResult, ...],
) -> StudyAggregate:
    """Summarize OOS (and optional IS) windows without stitching equity."""
    oos = tuple(item for item in windows if item.role is not WindowRole.IN_SAMPLE)
    scored = oos if oos else windows
    is_windows = tuple(item for item in windows if item.role is WindowRole.IN_SAMPLE)
    oos_trades = sum(item.summary.trade_count for item in scored)
    oos_wins = sum(item.summary.winning_trade_count for item in scored)
    mean_oos = _mean_decimal(tuple(item.summary.total_return_fraction for item in scored))
    mean_dd = _mean_decimal(tuple(item.summary.maximum_drawdown_fraction for item in scored))
    mean_is = _mean_decimal(tuple(item.summary.total_return_fraction for item in is_windows))
    gap = None
    if mean_is is not None and mean_oos is not None:
        gap = _canonical_decimal(Decimal(mean_is) - Decimal(mean_oos))
    win_rate = None
    if oos_trades:
        win_rate = _canonical_decimal(Decimal(oos_wins) / Decimal(oos_trades))
    return StudyAggregate(
        window_count=len(windows),
        oos_window_count=len(scored),
        oos_trade_count=oos_trades,
        oos_winning_trade_count=oos_wins,
        oos_win_rate=win_rate,
        mean_oos_return_fraction=mean_oos,
        mean_oos_drawdown_fraction=mean_dd,
        mean_is_return_fraction=mean_is,
        is_oos_return_gap=gap,
    )


@dataclass(frozen=True, slots=True)
class ResearchStudyService:
    """Plan and submit studies by composing existing backtest submissions."""

    publications: StrategyPublicationStore
    submitter: BacktestSubmitter
    results: BacktestResultReader

    async def plan(self, request: ResearchStudyRequest) -> ResearchStudyPlan:
        """Return the window schedule after loading published strategies."""
        published = await self._load_publications(request)
        return plan_study(request, publications=published)

    async def submit(self, request: ResearchStudyRequest) -> ResearchStudy:
        """Submit or reuse each child backtest and assemble the derived study."""
        plan = await self.plan(request)
        children: list[StudyWindowResult] = []
        try:
            for window in plan.windows:
                submission = window_submission_request(request, window)
                identities = await self.submitter.submit(submission)
                result = await self.results.load(identities.result_fingerprint)
                children.append(
                    StudyWindowResult(
                        label=window.label,
                        role=window.role,
                        fold_index=window.fold_index,
                        product_id=window.product_id,
                        run_fingerprint=identities.run_fingerprint,
                        result_fingerprint=identities.result_fingerprint,
                        evaluation_start=window.evaluation_start,
                        evaluation_end=window.evaluation_end,
                        summary=result.summary,
                    )
                )
        except StudyPlanningError:
            raise
        except BacktestSubmissionRejectedError:
            raise
        except Exception as error:
            raise ResearchStudyError("Research study submission is unavailable.") from error
        assembled = ResearchStudy(
            study_fingerprint="sha256:" + ("0" * 64),
            request_fingerprint=plan.request_fingerprint,
            kind=request.kind,
            engine_contract_version=request.engine_contract_version,
            windows=tuple(children),
            aggregate=aggregate_windows(tuple(children)),
            warnings=plan.warnings,
        )
        return assembled.model_copy(update={"study_fingerprint": study_fingerprint(assembled)})

    async def _load_publications(
        self, request: ResearchStudyRequest
    ) -> dict[str, PublishedStrategy]:
        """Load every published strategy named by the study request."""
        fingerprints = _strategy_fingerprints(request)
        loaded: dict[str, PublishedStrategy] = {}
        try:
            for fingerprint in fingerprints:
                loaded[fingerprint] = await self.publications.load(fingerprint)
        except Exception as error:
            raise ResearchStudyError("Research study submission is unavailable.") from error
        return loaded


def _strategy_fingerprints(request: ResearchStudyRequest) -> tuple[str, ...]:
    """Collect unique strategy fingerprints for publication loads."""
    if request.kind is StudyKind.CROSS_MARKET:
        if request.markets is None:
            raise StudyPlanningError("cross_market studies require markets.")
        return tuple(dict.fromkeys(item.strategy_fingerprint for item in request.markets))
    if request.strategy_fingerprint is None:
        raise StudyPlanningError("This study kind requires strategy_fingerprint.")
    return (request.strategy_fingerprint,)


def _require_single_market(request: ResearchStudyRequest) -> None:
    """Reject cross-market fields on single-instrument studies."""
    if request.markets is not None:
        raise ValueError("markets is only valid for cross_market studies")
    if request.strategy_fingerprint is None or request.dataset_fingerprint is None:
        raise ValueError("strategy_fingerprint and dataset_fingerprint are required")


def _require_oos_holdout(request: ResearchStudyRequest) -> None:
    """Require a fraction in (0, 1) and forbid walk-forward bar counts."""
    if request.in_sample_bars is not None or request.out_of_sample_bars is not None:
        raise ValueError("walk-forward bar counts are not valid for oos_holdout")
    if request.step_bars is not None:
        raise ValueError("step_bars is not valid for oos_holdout")
    if request.oos_fraction is None:
        raise ValueError("oos_fraction is required for oos_holdout")
    fraction = Decimal(request.oos_fraction)
    if fraction <= 0 or fraction >= 1:
        raise ValueError("oos_fraction must be greater than 0 and less than 1")


def _require_walk_forward(request: ResearchStudyRequest) -> None:
    """Require IS, OOS, and step bar counts."""
    if request.oos_fraction is not None:
        raise ValueError("oos_fraction is not valid for walk_forward")
    if (
        request.in_sample_bars is None
        or request.out_of_sample_bars is None
        or request.step_bars is None
    ):
        raise ValueError("walk_forward requires in_sample_bars, out_of_sample_bars, and step_bars")


def _require_cross_market(request: ResearchStudyRequest) -> None:
    """Require 2-8 unique product bindings and reject single-market fields."""
    if request.strategy_fingerprint is not None or request.dataset_fingerprint is not None:
        raise ValueError("cross_market studies name markets instead of a single strategy")
    if request.oos_fraction is not None or request.in_sample_bars is not None:
        raise ValueError("cross_market studies use the full evaluation window per market")
    if request.out_of_sample_bars is not None or request.step_bars is not None:
        raise ValueError("cross_market studies use the full evaluation window per market")
    if request.markets is None or len(request.markets) < 2 or len(request.markets) > _MAX_MARKETS:
        raise ValueError("cross_market requires between 2 and 8 markets")
    fingerprints = [item.strategy_fingerprint for item in request.markets]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("cross_market markets must use distinct strategy fingerprints")


def _plan_cross_market(
    request: ResearchStudyRequest,
    publications: dict[str, PublishedStrategy],
) -> tuple[list[PlannedStudyWindow], DatasetTimeframe]:
    """Emit one full-window child per distinct product."""
    if request.markets is None:
        raise StudyPlanningError("cross_market studies require markets.")
    windows: list[PlannedStudyWindow] = []
    products: list[str] = []
    timeframe: DatasetTimeframe | None = None
    for index, market in enumerate(request.markets):
        published = publications[market.strategy_fingerprint]
        product_id = published.definition.instrument.product_id
        clock = _decision_timeframe(published)
        if timeframe is None:
            timeframe = clock
        elif clock != timeframe:
            raise StudyPlanningError("cross_market markets must share one decision timeframe.")
        if product_id in products:
            raise StudyPlanningError("cross_market markets must use distinct products.")
        products.append(product_id)
        EvaluationWindow(starts_at=request.evaluation_start, ends_at=request.evaluation_end)
        windows.append(
            PlannedStudyWindow(
                label=product_id,
                role=WindowRole.FULL_WINDOW,
                fold_index=index,
                product_id=product_id,
                timeframe=clock,
                strategy_fingerprint=market.strategy_fingerprint,
                dataset_fingerprint=market.dataset_fingerprint,
                htf_dataset_fingerprint=market.htf_dataset_fingerprint,
                indicator_dataset_fingerprints=market.indicator_dataset_fingerprints,
                evaluation_start=request.evaluation_start,
                evaluation_end=request.evaluation_end,
            )
        )
    if timeframe is None:
        raise StudyPlanningError("cross_market studies require markets.")
    return windows, timeframe


def _plan_single_market(
    request: ResearchStudyRequest,
    publications: dict[str, PublishedStrategy],
    warnings: list[str],
) -> tuple[list[PlannedStudyWindow], DatasetTimeframe]:
    """Emit IS/OOS windows for holdout or walk-forward validation."""
    if request.strategy_fingerprint is None or request.dataset_fingerprint is None:
        raise StudyPlanningError("This study kind requires strategy and dataset fingerprints.")
    published = publications[request.strategy_fingerprint]
    timeframe = _decision_timeframe(published)
    duration = parse_candle_interval(timeframe).duration
    product_id = published.definition.instrument.product_id
    total_bars = _bar_count(request.evaluation_start, request.evaluation_end, duration)
    splits = (
        _oos_splits(request, total_bars, duration)
        if request.kind is StudyKind.OOS_HOLDOUT
        else _walk_forward_splits(request, total_bars, duration, warnings)
    )
    windows: list[PlannedStudyWindow] = []
    for fold_index, role, start, end in splits:
        label = f"{role.value}-{fold_index}"
        windows.append(
            PlannedStudyWindow(
                label=label,
                role=role,
                fold_index=fold_index,
                product_id=product_id,
                timeframe=timeframe,
                strategy_fingerprint=request.strategy_fingerprint,
                dataset_fingerprint=request.dataset_fingerprint,
                htf_dataset_fingerprint=request.htf_dataset_fingerprint,
                indicator_dataset_fingerprints=request.indicator_dataset_fingerprints,
                evaluation_start=start,
                evaluation_end=end,
            )
        )
    return windows, timeframe


def _oos_splits(
    request: ResearchStudyRequest,
    total_bars: int,
    duration: timedelta,
) -> tuple[tuple[int, WindowRole, datetime, datetime], ...]:
    """Split the evaluation range into one IS window and one OOS window."""
    if request.oos_fraction is None:
        raise StudyPlanningError("oos_fraction is required for oos_holdout.")
    fraction = Decimal(request.oos_fraction)
    oos_bars = int((Decimal(total_bars) * fraction).to_integral_value(rounding=ROUND_HALF_EVEN))
    oos_bars = min(max(oos_bars, 1), total_bars - 1)
    remaining = total_bars - oos_bars - request.embargo_bars
    if remaining < 1:
        raise StudyPlanningError(
            "oos_holdout needs at least one in-sample bar after embargo and OOS."
        )
    is_end = _add_bars(request.evaluation_start, remaining, duration)
    oos_start = _add_bars(is_end, request.embargo_bars, duration)
    return (
        (0, WindowRole.IN_SAMPLE, request.evaluation_start, is_end),
        (0, WindowRole.OUT_OF_SAMPLE, oos_start, request.evaluation_end),
    )


def _walk_forward_splits(
    request: ResearchStudyRequest,
    total_bars: int,
    duration: timedelta,
    warnings: list[str],
) -> tuple[tuple[int, WindowRole, datetime, datetime], ...]:
    """Emit sequential IS/OOS folds until the next OOS would pass evaluation_end."""
    if (
        request.in_sample_bars is None
        or request.out_of_sample_bars is None
        or request.step_bars is None
    ):
        raise StudyPlanningError("walk_forward requires bar counts.")
    if request.step_bars < request.out_of_sample_bars:
        warnings.append(
            "step_bars is smaller than out_of_sample_bars, so OOS windows overlap. "
            "Overlapping OOS metrics are not independent."
        )
    folds: list[tuple[int, WindowRole, datetime, datetime]] = []
    fold_index = 0
    while fold_index < _MAX_FOLDS:
        is_bars = request.in_sample_bars
        if request.fold_mode is FoldMode.ANCHORED:
            is_bars = request.in_sample_bars + fold_index * request.step_bars
        is_offset = 0 if request.fold_mode is FoldMode.ANCHORED else fold_index * request.step_bars
        is_start = _add_bars(request.evaluation_start, is_offset, duration)
        is_end = _add_bars(is_start, is_bars, duration)
        oos_start = _add_bars(is_end, request.embargo_bars, duration)
        oos_end = _add_bars(oos_start, request.out_of_sample_bars, duration)
        if oos_end > request.evaluation_end:
            break
        needed = is_offset + is_bars + request.embargo_bars + request.out_of_sample_bars
        if needed > total_bars:
            break
        folds.append((fold_index, WindowRole.IN_SAMPLE, is_start, is_end))
        folds.append((fold_index, WindowRole.OUT_OF_SAMPLE, oos_start, oos_end))
        fold_index += 1
    if not folds:
        raise StudyPlanningError(
            "The evaluation window is too short for one walk-forward fold "
            "(in-sample, embargo, and out-of-sample bars)."
        )
    return tuple(folds)


def _decision_timeframe(published: PublishedStrategy) -> DatasetTimeframe:
    """Read the published LTF clock without inventing unsupported intervals."""
    return published.definition.timeframe


def _bar_count(start: datetime, end: datetime, duration: timedelta) -> int:
    """Return the integer number of bars in a half-open window."""
    seconds = int((end - start).total_seconds())
    step = int(duration.total_seconds())
    if step <= 0 or seconds <= 0 or seconds % step != 0:
        raise StudyPlanningError("evaluation window must be a positive integer number of bars.")
    return seconds // step


def _add_bars(instant: datetime, bars: int, duration: timedelta) -> datetime:
    """Advance a UTC boundary by an exact bar count."""
    return instant + duration * bars


def _mean_decimal(values: tuple[str, ...]) -> str | None:
    """Equal-weight mean of canonical decimal strings, or None when empty."""
    if not values:
        return None
    total = sum((Decimal(item) for item in values), start=Decimal(0))
    return _canonical_decimal(total / Decimal(len(values)))


def _canonical_decimal(value: Decimal) -> str:
    """Render a finite Decimal as a plain string without scientific notation."""
    quantized = value.quantize(Decimal("0.000000000000000001"), rounding=ROUND_HALF_EVEN)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
