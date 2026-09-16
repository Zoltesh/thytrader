"""Typed application boundary for immutable backtest submission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import secrets
from typing import TYPE_CHECKING, Literal, Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from thytrader.backtest.models import backtest_result_fingerprint
from thytrader.backtest.service import evaluate_and_publish_backtest
from thytrader.market_data.datasets import DatasetStoreError
from thytrader.market_data.models import parse_candle_interval
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.research.models import (
    AdditionalInstrumentDataset,
    BarExecutionAssumptions,
    BrokerAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    IndicatorTimeframeDataset,
    ResearchRunSpecification,
    WarmupWindow,
    warmup_starts_at,
)
from thytrader.research.multi_timeframe import closed_bar_required_coverage, htf_required_coverage
from thytrader.research.publication import (
    PublishedResearchRunSpecification,
    ResearchRunPublicationError,
    dataset_evaluation_bounds,
    evaluation_window_suggestion,
)
from thytrader.strategies.models import (
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    lockstep_product_ids,
    unbound_indicator_timeframes,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.strategies.models import IndicatorDefinition, StrategyDefinition
    from thytrader.strategies.publication import PublishedStrategy


class BacktestSubmissionRequest(BaseModel):
    """Browser-supplied immutable simulation assumptions with no execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = ()
    additional_instrument_datasets: tuple[AdditionalInstrumentDataset, ...] = ()
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None
    initial_quote_balance: str
    maker_fee_rate: str
    taker_fee_rate: str
    fixed_slippage_bps: str
    engine_contract_version: Literal[
        "thytrader-bar-backtest-v1",
        "thytrader-bar-backtest-v2",
        "thytrader-bar-backtest-v3",
        "thytrader-bar-backtest-v4",
    ] = "thytrader-bar-backtest-v1"
    spread_bps: str | None = None

    @model_validator(mode="after")
    def validate_engine_broker_contract(self) -> Self:
        """Reject assumptions that cannot form one valid immutable research run."""
        _validate_submission_assumptions(self)
        return self


@dataclass(frozen=True, slots=True)
class BacktestSubmissionResult:
    """Immutable run and result identities returned after deterministic publication."""

    run_fingerprint: str
    result_fingerprint: str


class BacktestSubmissionError(RuntimeError):
    """Report a redacted submission failure without granting trading authority."""


class BacktestSubmissionRejectedError(ValueError):
    """Report a caller-input rejection (dataset/window mismatch) before any I/O."""


@runtime_checkable
class BacktestSubmitter(Protocol):
    """Submit one immutable research run and publish its deterministic result."""

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Return immutable evidence identities for one submitted research simulation."""
        ...


class DisabledBacktestSubmitter:
    """Fail closed until durable submission dependencies are configured."""

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Refuse submission when the authoritative service is unavailable."""
        del request
        raise BacktestSubmissionError("Backtest submission is unavailable.")


class PostgresBacktestSubmitter:
    """Publish/reuse immutable sources and invoke the existing authoritative simulation service."""

    def __init__(self, engine: AsyncEngine, dataset_store: DatasetStore) -> None:
        """Use one application-managed engine and immutable dataset root."""
        self._dataset_store = dataset_store
        self._strategy_store = PostgresStrategyPublicationStore(engine)
        self._run_store = PostgresResearchRunStore(engine)
        self._result_store = PostgresBacktestResultStore(
            engine,
            research_run_store=self._run_store,
            dataset_store=dataset_store,
        )

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Create/reuse exact research inputs, simulate, and return immutable identities."""
        try:
            _validate_submission_assumptions(request)
            strategy = await self._strategy_store.load(request.strategy_fingerprint)
            request = _with_evaluation_window(request, strategy, self._dataset_store)
            _validate_submission_assumptions(request)
            _require_htf_request(request, strategy.definition)
            _require_indicator_dataset_request(request, strategy.definition)
            _require_additional_instrument_request(request, strategy.definition)
        except BacktestSubmissionRejectedError:
            raise
        except Exception as error:
            raise BacktestSubmissionError("Backtest submission is unavailable.") from error
        try:
            now = _utc_millisecond(datetime.now(UTC))
            await self._bind_submission_datasets(request, now)
            execution_fingerprint = _execution_fingerprint(request)
            published_run = await self._run_store.load_by_execution_fingerprint(
                execution_fingerprint,
                dataset_store=self._dataset_store,
            )
            if published_run is None:
                published_run = await self._publish_run(
                    request, strategy, now, execution_fingerprint
                )
            result = await evaluate_and_publish_backtest(
                published_run.run_fingerprint,
                run_store=self._run_store,
                strategy_store=self._strategy_store,
                dataset_store=self._dataset_store,
                result_store=self._result_store,
            )
        except BacktestSubmissionRejectedError:
            raise
        except Exception as error:
            raise BacktestSubmissionError("Backtest submission is unavailable.") from error
        return BacktestSubmissionResult(
            run_fingerprint=published_run.run_fingerprint,
            result_fingerprint=backtest_result_fingerprint(result),
        )

    async def _bind_submission_datasets(
        self, request: BacktestSubmissionRequest, bound_at: datetime
    ) -> None:
        """Bind primary, HTF, extra-TF, and additional-instrument datasets to the strategy."""
        await self._strategy_store.bind_dataset(
            request.strategy_fingerprint,
            request.dataset_fingerprint,
            dataset_store=self._dataset_store,
            bound_at=bound_at,
        )
        if request.htf_dataset_fingerprint is not None:
            await self._strategy_store.bind_dataset(
                request.strategy_fingerprint,
                request.htf_dataset_fingerprint,
                dataset_store=self._dataset_store,
                bound_at=bound_at,
            )
        for binding in request.indicator_dataset_fingerprints:
            await self._strategy_store.bind_dataset(
                request.strategy_fingerprint,
                binding.dataset_fingerprint,
                dataset_store=self._dataset_store,
                bound_at=bound_at,
            )
        for extra in request.additional_instrument_datasets:
            await self._strategy_store.bind_dataset(
                request.strategy_fingerprint,
                extra.dataset_fingerprint,
                dataset_store=self._dataset_store,
                bound_at=bound_at,
            )
            if extra.htf_dataset_fingerprint is not None:
                await self._strategy_store.bind_dataset(
                    request.strategy_fingerprint,
                    extra.htf_dataset_fingerprint,
                    dataset_store=self._dataset_store,
                    bound_at=bound_at,
                )
            for clock in extra.indicator_dataset_fingerprints:
                await self._strategy_store.bind_dataset(
                    request.strategy_fingerprint,
                    clock.dataset_fingerprint,
                    dataset_store=self._dataset_store,
                    bound_at=bound_at,
                )

    async def _publish_run(
        self,
        request: BacktestSubmissionRequest,
        strategy: PublishedStrategy,
        now: datetime,
        execution_fingerprint: str,
    ) -> PublishedResearchRunSpecification:
        """Build, verify, and idempotently publish one immutable run specification."""
        evaluation_start, evaluation_end = _filled_window(request)
        specification = ResearchRunSpecification(
            schema_version="1.0",
            run_id=_uuid7(now),
            created_at=now,
            strategy_fingerprint=request.strategy_fingerprint,
            dataset_fingerprint=request.dataset_fingerprint,
            htf_dataset_fingerprint=request.htf_dataset_fingerprint,
            indicator_dataset_fingerprints=request.indicator_dataset_fingerprints,
            additional_instrument_datasets=request.additional_instrument_datasets,
            evaluation=EvaluationWindow(
                starts_at=evaluation_start,
                ends_at=evaluation_end,
            ),
            warmup=WarmupWindow(
                bars=strategy.definition.data_requirements.warmup_bars,
                starts_at=warmup_starts_at(
                    evaluation_start,
                    strategy.definition.data_requirements.warmup_bars,
                    strategy.definition.timeframe,
                ),
            ),
            capital=CapitalAssumptions(
                quote_currency="USD",
                initial_quote_balance=request.initial_quote_balance,
            ),
            costs=CostAssumptions(
                maker_fee_rate=request.maker_fee_rate,
                taker_fee_rate=request.taker_fee_rate,
                fixed_slippage_bps=request.fixed_slippage_bps,
            ),
            broker=_broker_from_request(request),
            bar_execution=_bar_execution_from_request(request),
            engine_contract_version=request.engine_contract_version,
            random_seed=0,
        )
        try:
            return await self._run_store.publish(
                specification,
                dataset_store=self._dataset_store,
                execution_fingerprint=execution_fingerprint,
            )
        except DatasetStoreError as error:
            # A missing/unusable dataset artifact is caller input, not an outage.
            raise BacktestSubmissionRejectedError(
                "The selected dataset was not found or is not a verified complete artifact."
            ) from error
        except ResearchRunPublicationError as error:
            message = str(error)
            dataset_problem = (
                "dataset" in message
                or "warmup" in message
                or "coverage" in message
                or "evaluation window" in message
            )
            if dataset_problem:
                raise BacktestSubmissionRejectedError(message) from error
            raise BacktestSubmissionError("Backtest submission is unavailable.") from error


def _with_evaluation_window(
    request: BacktestSubmissionRequest,
    strategy: PublishedStrategy,
    dataset_store: DatasetStore,
) -> BacktestSubmissionRequest:
    """Fill omitted dates from the dataset, or reject supplied dates with a suggestion."""
    try:
        manifest = dataset_store.load_manifest(request.dataset_fingerprint)
        dataset_starts_at = _manifest_instant(manifest.starts_at)
        dataset_ends_at = _manifest_instant(manifest.ends_at)
        suggested_start, suggested_end = dataset_evaluation_bounds(
            dataset_starts_at=dataset_starts_at,
            dataset_ends_at=dataset_ends_at,
            warmup_bars=strategy.definition.data_requirements.warmup_bars,
            timeframe=strategy.definition.timeframe,
        )
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(
            "The selected dataset was not found or is not a verified complete artifact."
        ) from error
    except (ResearchRunPublicationError, ValueError) as error:
        raise BacktestSubmissionRejectedError(str(error)) from error
    if request.evaluation_start is None and request.evaluation_end is None:
        filled = request.model_copy(
            update={"evaluation_start": suggested_start, "evaluation_end": suggested_end}
        )
        _require_htf_window(filled, strategy, dataset_store)
        _require_indicator_timeframe_window(filled, strategy, dataset_store)
        _require_additional_instrument_window(filled, strategy, dataset_store)
        return filled
    if request.evaluation_start is None or request.evaluation_end is None:
        raise BacktestSubmissionRejectedError(
            "evaluation_start and evaluation_end must both be omitted or both be set."
        )
    try:
        EvaluationWindow(starts_at=request.evaluation_start, ends_at=request.evaluation_end)
    except ValueError as error:
        raise BacktestSubmissionRejectedError(str(error)) from error
    required_fill_end = (
        request.evaluation_end + parse_candle_interval(strategy.definition.timeframe).duration
    )
    warmup_start = warmup_starts_at(
        request.evaluation_start,
        strategy.definition.data_requirements.warmup_bars,
        strategy.definition.timeframe,
    )
    if dataset_starts_at > warmup_start or dataset_ends_at < required_fill_end:
        raise BacktestSubmissionRejectedError(
            evaluation_window_suggestion(
                dataset_starts_at=dataset_starts_at,
                dataset_ends_at=dataset_ends_at,
                warmup_bars=strategy.definition.data_requirements.warmup_bars,
                timeframe=strategy.definition.timeframe,
            )
        )
    _require_htf_window(request, strategy, dataset_store)
    _require_indicator_timeframe_window(request, strategy, dataset_store)
    _require_additional_instrument_window(request, strategy, dataset_store)
    return request


def _manifest_instant(value: str) -> datetime:
    """Parse one dataset coverage timestamp as timezone-aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Dataset coverage timestamps are invalid.")
    return parsed.astimezone(UTC)


def _validate_submission_assumptions(request: BacktestSubmissionRequest) -> None:
    """Revalidate every untrusted simulation assumption before source or persistence I/O."""
    _require_valid_broker_inputs(request)
    if request.evaluation_start is None and request.evaluation_end is None:
        pass
    elif request.evaluation_start is None or request.evaluation_end is None:
        raise ValueError("evaluation_start and evaluation_end must both be omitted or both be set.")
    else:
        EvaluationWindow(starts_at=request.evaluation_start, ends_at=request.evaluation_end)
    CapitalAssumptions(
        quote_currency="USD",
        initial_quote_balance=request.initial_quote_balance,
    )
    CostAssumptions(
        maker_fee_rate=request.maker_fee_rate,
        taker_fee_rate=request.taker_fee_rate,
        fixed_slippage_bps=request.fixed_slippage_bps,
    )
    _broker_from_request(request)


def _broker_from_request(request: BacktestSubmissionRequest) -> BrokerAssumptions | None:
    """Resolve contract-specific broker inputs, mirroring the CLI contract exactly."""
    if request.engine_contract_version == "thytrader-bar-backtest-v1":
        return None
    if request.engine_contract_version in (
        "thytrader-bar-backtest-v3",
        "thytrader-bar-backtest-v4",
    ):
        return BrokerAssumptions(
            price_model="post_only_limit",
            spread_bps="0",
            fill_policy="resting_limit",
            trigger_evaluation="bar_extreme",
            equity_marking="last_close",
        )
    if request.spread_bps is None:
        message = "spread_bps is required for the thytrader-bar-backtest-v2 contract"
        raise ValueError(message)
    return BrokerAssumptions(
        price_model="constant_spread_bps",
        spread_bps=request.spread_bps,
        fill_policy="full",
        trigger_evaluation="bid_side",
        equity_marking="bid_close",
    )


def _bar_execution_from_request(request: BacktestSubmissionRequest) -> BarExecutionAssumptions:
    """Bind fill timing to the selected engine contract."""
    if request.engine_contract_version in (
        "thytrader-bar-backtest-v3",
        "thytrader-bar-backtest-v4",
    ):
        return BarExecutionAssumptions(
            signal_timing="completed_candle_close",
            fill_timing="resting_maker_limit",
            limit_at="completed_close",
        )
    return BarExecutionAssumptions(
        signal_timing="completed_candle_close",
        fill_timing="next_candle_open",
    )


def _require_valid_broker_inputs(request: BacktestSubmissionRequest) -> None:
    """Reject mismatched engine and spread combinations before any publication."""
    if request.engine_contract_version == "thytrader-bar-backtest-v2":
        if request.spread_bps is None:
            raise ValueError("spread_bps is required for the thytrader-bar-backtest-v2 contract")
        return
    if request.spread_bps is not None:
        raise ValueError("spread_bps requires the thytrader-bar-backtest-v2 contract")


def _execution_fingerprint(request: BacktestSubmissionRequest) -> str:
    """Hash normalized simulation semantics so equivalent submissions are idempotent."""
    evaluation_start, evaluation_end = _filled_window(request)
    capital = CapitalAssumptions(
        quote_currency="USD",
        initial_quote_balance=request.initial_quote_balance,
    )
    costs = CostAssumptions(
        maker_fee_rate=request.maker_fee_rate,
        taker_fee_rate=request.taker_fee_rate,
        fixed_slippage_bps=request.fixed_slippage_bps,
    )
    broker = _broker_from_request(request)
    payload = {
        "bar_execution": _bar_execution_from_request(request).model_dump(
            mode="json", exclude_none=True
        ),
        "broker": None if broker is None else broker.model_dump(mode="json"),
        "capital": capital.model_dump(mode="json"),
        "costs": costs.model_dump(mode="json"),
        "dataset_fingerprint": request.dataset_fingerprint,
        "engine_contract_version": request.engine_contract_version,
        "evaluation_end": evaluation_end.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "random_seed": 0,
        "strategy_fingerprint": request.strategy_fingerprint,
    }
    if request.htf_dataset_fingerprint is not None:
        payload["htf_dataset_fingerprint"] = request.htf_dataset_fingerprint
    if request.indicator_dataset_fingerprints:
        payload["indicator_dataset_fingerprints"] = [
            item.model_dump(mode="json") for item in request.indicator_dataset_fingerprints
        ]
    if request.additional_instrument_datasets:
        payload["additional_instrument_datasets"] = [
            item.model_dump(mode="json") for item in request.additional_instrument_datasets
        ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{sha256(canonical.encode()).hexdigest()}"


def _filled_window(request: BacktestSubmissionRequest) -> tuple[datetime, datetime]:
    """Return evaluation bounds after optional dataset fill."""
    if request.evaluation_start is None or request.evaluation_end is None:
        raise BacktestSubmissionError("Backtest submission is unavailable.")
    return request.evaluation_start, request.evaluation_end


def _require_htf_request(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Reject HTF fingerprints that do not match the published definition."""
    has_filter = definition.htf_filter is not None
    has_fingerprint = request.htf_dataset_fingerprint is not None
    if has_filter and not has_fingerprint:
        raise BacktestSubmissionRejectedError(
            "Multi-timeframe strategies require htf_dataset_fingerprint."
        )
    if not has_filter and has_fingerprint:
        raise BacktestSubmissionRejectedError(
            "htf_dataset_fingerprint is only valid when the strategy declares htf_filter."
        )
    if has_fingerprint and request.htf_dataset_fingerprint == request.dataset_fingerprint:
        raise BacktestSubmissionRejectedError(
            "htf_dataset_fingerprint must differ from dataset_fingerprint."
        )


def _require_indicator_dataset_request(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Reject extra-TF fingerprints that do not match the published definition."""
    required = unbound_indicator_timeframes(definition)
    declared = tuple(item.timeframe for item in request.indicator_dataset_fingerprints)
    if declared != required:
        raise BacktestSubmissionRejectedError(
            "indicator_dataset_fingerprints must match the strategy extra indicator timeframes."
        )
    reserved = {request.dataset_fingerprint}
    if request.htf_dataset_fingerprint is not None:
        reserved.add(request.htf_dataset_fingerprint)
    fingerprints = [item.dataset_fingerprint for item in request.indicator_dataset_fingerprints]
    if any(fingerprint in reserved for fingerprint in fingerprints):
        raise BacktestSubmissionRejectedError(
            "indicator_dataset_fingerprints must differ from dataset_fingerprint and "
            "htf_dataset_fingerprint."
        )


def _require_htf_window(
    request: BacktestSubmissionRequest,
    strategy: PublishedStrategy,
    dataset_store: DatasetStore,
) -> None:
    """Confirm the HTF dataset covers last-completed HTF bars for the LTF window."""
    definition = strategy.definition
    htf_filter = definition.htf_filter
    if htf_filter is None:
        if request.htf_dataset_fingerprint is not None:
            raise BacktestSubmissionRejectedError(
                "htf_dataset_fingerprint is only valid when the strategy declares htf_filter."
            )
        return
    if request.htf_dataset_fingerprint is None:
        raise BacktestSubmissionRejectedError(
            "Multi-timeframe strategies require htf_dataset_fingerprint."
        )
    try:
        htf_manifest = dataset_store.load_manifest(request.htf_dataset_fingerprint)
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(
            "The selected HTF dataset was not found or is not a verified complete artifact."
        ) from error
    if htf_manifest.product_id != definition.instrument.product_id:
        raise BacktestSubmissionRejectedError(
            "HTF dataset product_id must match the published strategy instrument."
        )
    if htf_manifest.timeframe != htf_filter.timeframe:
        raise BacktestSubmissionRejectedError(
            "HTF dataset timeframe must match strategy htf_filter.timeframe."
        )
    if not htf_manifest.complete:
        raise BacktestSubmissionRejectedError("HTF dataset status must be complete.")
    evaluation_start, evaluation_end = _filled_window(request)
    required_start, required_end = htf_required_coverage(
        evaluation_starts_at=evaluation_start,
        evaluation_ends_at=evaluation_end,
        htf_filter=htf_filter,
    )
    htf_starts_at = _manifest_instant(htf_manifest.starts_at)
    htf_ends_at = _manifest_instant(htf_manifest.ends_at)
    if htf_starts_at > required_start or htf_ends_at < required_end:
        raise BacktestSubmissionRejectedError(
            "Requested evaluation window is not fully covered by the HTF dataset."
        )


def _require_indicator_timeframe_window(
    request: BacktestSubmissionRequest,
    strategy: PublishedStrategy,
    dataset_store: DatasetStore,
) -> None:
    """Confirm extra-TF datasets cover last-completed bars for the LTF window."""
    definition = strategy.definition
    required = unbound_indicator_timeframes(definition)
    declared = tuple(item.timeframe for item in request.indicator_dataset_fingerprints)
    if declared != required:
        raise BacktestSubmissionRejectedError(
            "indicator_dataset_fingerprints must match the strategy extra indicator timeframes."
        )
    groups = dict(extra_indicator_timeframe_groups(definition))
    evaluation_start, evaluation_end = _filled_window(request)
    for binding in request.indicator_dataset_fingerprints:
        try:
            manifest = dataset_store.load_manifest(binding.dataset_fingerprint)
        except DatasetStoreError as error:
            raise BacktestSubmissionRejectedError(
                "The selected indicator-timeframe dataset was not found or is not a "
                "verified complete artifact."
            ) from error
        if manifest.product_id != definition.instrument.product_id:
            raise BacktestSubmissionRejectedError(
                "Indicator-timeframe dataset product_id must match the published strategy."
            )
        if manifest.timeframe != binding.timeframe:
            raise BacktestSubmissionRejectedError(
                "Indicator-timeframe dataset must match the declared indicator timeframe."
            )
        if not manifest.complete:
            raise BacktestSubmissionRejectedError(
                "Indicator-timeframe dataset status must be complete."
            )
        required_start, required_end = closed_bar_required_coverage(
            evaluation_starts_at=evaluation_start,
            evaluation_ends_at=evaluation_end,
            timeframe=binding.timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(groups[binding.timeframe]),
        )
        starts_at = _manifest_instant(manifest.starts_at)
        ends_at = _manifest_instant(manifest.ends_at)
        if starts_at > required_start or ends_at < required_end:
            raise BacktestSubmissionRejectedError(
                "Requested evaluation window is not fully covered by the indicator-timeframe "
                "dataset."
            )


def _extra_lockstep_product_ids(definition: StrategyDefinition) -> tuple[str, ...]:
    """Return extra covered product ids in lexicographic lockstep order."""
    return tuple(
        product_id
        for product_id in lockstep_product_ids(definition)
        if product_id != definition.instrument.product_id
    )


def _require_additional_product_order(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Require extra dataset bindings to list the extra covered products in lockstep order."""
    declared = tuple(item.product_id for item in request.additional_instrument_datasets)
    if declared != _extra_lockstep_product_ids(definition):
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets must match extra covered products in product_id order."
        )


def _require_additional_htf_fingerprint(
    binding: AdditionalInstrumentDataset, *, has_filter: bool
) -> None:
    """Require extra-product HTF fingerprints exactly when the document declares htf_filter."""
    if has_filter and binding.htf_dataset_fingerprint is None:
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets require htf_dataset_fingerprint when the "
            "strategy declares htf_filter."
        )
    if not has_filter and binding.htf_dataset_fingerprint is not None:
        raise BacktestSubmissionRejectedError(
            "additional-instrument HTF fingerprints are only valid with htf_filter."
        )


def _additional_request_fingerprints(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> list[str]:
    """Collect extra-product dataset identities and reject HTF or extra-TF mismatches."""
    required_clocks = unbound_indicator_timeframes(definition)
    has_filter = definition.htf_filter is not None
    extra_fingerprints: list[str] = []
    for binding in request.additional_instrument_datasets:
        extra_fingerprints.append(binding.dataset_fingerprint)
        _require_additional_htf_fingerprint(binding, has_filter=has_filter)
        if binding.htf_dataset_fingerprint is not None:
            extra_fingerprints.append(binding.htf_dataset_fingerprint)
        clocks = tuple(item.timeframe for item in binding.indicator_dataset_fingerprints)
        if clocks != required_clocks:
            raise BacktestSubmissionRejectedError(
                "additional-instrument extra-TF fingerprints must match the strategy clocks."
            )
        extra_fingerprints.extend(
            item.dataset_fingerprint for item in binding.indicator_dataset_fingerprints
        )
    return extra_fingerprints


def _require_additional_instrument_request(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Reject extra product bindings that do not match the published document."""
    if not definition.additional_instruments and not request.additional_instrument_datasets:
        return
    _require_additional_product_order(request, definition)
    extra_fingerprints = _additional_request_fingerprints(request, definition)
    reserved = {request.dataset_fingerprint}
    if request.htf_dataset_fingerprint is not None:
        reserved.add(request.htf_dataset_fingerprint)
    reserved.update(item.dataset_fingerprint for item in request.indicator_dataset_fingerprints)
    if any(fingerprint in reserved for fingerprint in extra_fingerprints):
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets must differ from primary LTF/HTF/extra-TF identities."
        )
    if len(extra_fingerprints) != len(set(extra_fingerprints)):
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets identities must be unique."
        )


def _require_additional_instrument_window(
    request: BacktestSubmissionRequest,
    strategy: PublishedStrategy,
    dataset_store: DatasetStore,
) -> None:
    """Confirm extra product datasets cover the same evaluation window as the primary."""
    definition = strategy.definition
    if not definition.additional_instruments and not request.additional_instrument_datasets:
        return
    _require_additional_product_order(request, definition)
    required_clocks = unbound_indicator_timeframes(definition)
    groups = dict(extra_indicator_timeframe_groups(definition))
    evaluation_start, evaluation_end = _filled_window(request)
    interval = parse_candle_interval(definition.timeframe)
    warmup_start = warmup_starts_at(
        evaluation_start,
        definition.data_requirements.warmup_bars,
        definition.timeframe,
    )
    required_fill_end = evaluation_end + interval.duration
    for binding in request.additional_instrument_datasets:
        _require_additional_ltf_window(
            binding,
            definition=definition,
            dataset_store=dataset_store,
            warmup_start=warmup_start,
            required_fill_end=required_fill_end,
        )
        _require_additional_htf_window(
            binding,
            definition=definition,
            dataset_store=dataset_store,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
        )
        _require_additional_extra_tf_window(
            binding,
            dataset_store=dataset_store,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            required_clocks=required_clocks,
            groups=groups,
        )


def _require_additional_ltf_window(
    binding: AdditionalInstrumentDataset,
    *,
    definition: StrategyDefinition,
    dataset_store: DatasetStore,
    warmup_start: datetime,
    required_fill_end: datetime,
) -> None:
    """Confirm one extra product's decision-clock dataset covers the evaluation window."""
    try:
        manifest = dataset_store.load_manifest(binding.dataset_fingerprint)
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(
            "The selected additional-instrument dataset was not found or is not a "
            "verified complete artifact."
        ) from error
    if (
        manifest.product_id != binding.product_id
        or manifest.timeframe != definition.timeframe
        or not manifest.complete
    ):
        raise BacktestSubmissionRejectedError(
            "Additional-instrument dataset must be a complete artifact for that product "
            "and decision clock."
        )
    starts_at = _manifest_instant(manifest.starts_at)
    ends_at = _manifest_instant(manifest.ends_at)
    if starts_at > warmup_start or ends_at < required_fill_end:
        raise BacktestSubmissionRejectedError(
            "Requested evaluation window is not fully covered by an additional-instrument dataset."
        )


def _require_additional_htf_window(
    binding: AdditionalInstrumentDataset,
    *,
    definition: StrategyDefinition,
    dataset_store: DatasetStore,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> None:
    """Confirm one extra product's HTF dataset when the strategy declares a filter."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return
    if binding.htf_dataset_fingerprint is None:
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets require htf_dataset_fingerprint when the "
            "strategy declares htf_filter."
        )
    try:
        htf_manifest = dataset_store.load_manifest(binding.htf_dataset_fingerprint)
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(
            "The selected additional-instrument HTF dataset was not found or is not a "
            "verified complete artifact."
        ) from error
    if (
        htf_manifest.product_id != binding.product_id
        or htf_manifest.timeframe != htf_filter.timeframe
        or not htf_manifest.complete
    ):
        raise BacktestSubmissionRejectedError(
            "Additional-instrument HTF dataset must match that product and HTF clock."
        )
    required_start, required_end = htf_required_coverage(
        evaluation_starts_at=evaluation_start,
        evaluation_ends_at=evaluation_end,
        htf_filter=htf_filter,
    )
    htf_starts_at = _manifest_instant(htf_manifest.starts_at)
    htf_ends_at = _manifest_instant(htf_manifest.ends_at)
    if htf_starts_at > required_start or htf_ends_at < required_end:
        raise BacktestSubmissionRejectedError(
            "Requested evaluation window is not fully covered by an additional-instrument "
            "HTF dataset."
        )


def _require_additional_extra_tf_window(
    binding: AdditionalInstrumentDataset,
    *,
    dataset_store: DatasetStore,
    evaluation_start: datetime,
    evaluation_end: datetime,
    required_clocks: tuple[str, ...],
    groups: Mapping[str, tuple[IndicatorDefinition, ...]],
) -> None:
    """Confirm extra-TF datasets for one extra product match the published clocks."""
    clocks = tuple(item.timeframe for item in binding.indicator_dataset_fingerprints)
    if clocks != required_clocks:
        raise BacktestSubmissionRejectedError(
            "additional-instrument extra-TF fingerprints must match the strategy clocks."
        )
    for clock in binding.indicator_dataset_fingerprints:
        try:
            clock_manifest = dataset_store.load_manifest(clock.dataset_fingerprint)
        except DatasetStoreError as error:
            raise BacktestSubmissionRejectedError(
                "The selected additional-instrument extra-TF dataset was not found or is "
                "not a verified complete artifact."
            ) from error
        if (
            clock_manifest.product_id != binding.product_id
            or clock_manifest.timeframe != clock.timeframe
            or not clock_manifest.complete
        ):
            raise BacktestSubmissionRejectedError(
                "Additional-instrument extra-TF dataset must match that product and clock."
            )
        required_start, required_end = closed_bar_required_coverage(
            evaluation_starts_at=evaluation_start,
            evaluation_ends_at=evaluation_end,
            timeframe=clock.timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(groups[clock.timeframe]),
        )
        clock_start = _manifest_instant(clock_manifest.starts_at)
        clock_end = _manifest_instant(clock_manifest.ends_at)
        if clock_start > required_start or clock_end < required_end:
            raise BacktestSubmissionRejectedError(
                "Requested evaluation window is not fully covered by an additional-instrument "
                "extra-TF dataset."
            )


def _utc_millisecond(value: datetime) -> datetime:
    """Normalize a server timestamp to the UUIDv7-representable UTC millisecond."""
    return value.astimezone(UTC).replace(microsecond=(value.microsecond // 1_000) * 1_000)


def _uuid7(created_at: datetime) -> UUID:
    """Create one UUIDv7 whose encoded timestamp matches a UTC millisecond."""
    milliseconds = int(created_at.timestamp() * 1_000)
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)
