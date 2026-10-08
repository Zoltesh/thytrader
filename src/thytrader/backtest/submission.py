"""Typed application boundary for immutable backtest submission.

The submitters, ``resolve_backtest_window``, and the execution fingerprint live here.
Request models and errors, binding checks, coverage checks, and window resolution
live in the ``submission_*`` sibling modules; names other modules import from here
are re-exported (``__all__``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.backtest.models import backtest_result_fingerprint
from thytrader.backtest.service import evaluate_and_publish_backtest
from thytrader.backtest.submission_bindings import (
    _require_additional_instrument_request,
    _require_htf_request,
    _require_indicator_dataset_request,
    _require_reference_dataset_request,
)
from thytrader.backtest.submission_coverage import _filled_window
from thytrader.backtest.submission_models import (
    BacktestAssumptions,
    BacktestStartRequest,
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
    _cost_assumptions,
    _validate_submission_assumptions,
)
from thytrader.backtest.submission_windows import _with_evaluation_window
from thytrader.execution.ids import uuid7
from thytrader.market_data.datasets import DatasetStoreError
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.research.models import (
    BACKTEST_ENGINE,
    SIMULATION_SEMANTICS,
    CapitalAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
    warmup_starts_at,
)
from thytrader.research.publication import (
    PublishedResearchRunSpecification,
    ResearchRunPublicationError,
)
from thytrader.strategies.snapshots import StrategyDatasetMismatchError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.market_data.products import SpotQuoteCurrency
    from thytrader.strategies.snapshots import StrategySnapshot

__all__ = [
    "SIMULATION_SEMANTICS",
    "BacktestAssumptions",
    "BacktestStartRequest",
    "BacktestSubmissionError",
    "BacktestSubmissionRejectedError",
    "BacktestSubmissionRequest",
    "BacktestSubmissionResult",
    "BacktestSubmitter",
    "DisabledBacktestSubmitter",
    "PostgresBacktestSubmitter",
    "_cost_assumptions",
    "_execution_fingerprint",
    "_with_evaluation_window",
    "resolve_backtest_window",
]


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
        self._strategy_store = PostgresStrategyStore(engine)
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
            _validate_submission_assumptions(
                request, quote_currency=strategy.definition.instrument.quote_currency
            )
            _require_htf_request(request, strategy.definition)
            _require_indicator_dataset_request(request, strategy.definition)
            _require_additional_instrument_request(request, strategy.definition)
            _require_reference_dataset_request(request, strategy.definition)
        except BacktestSubmissionRejectedError:
            raise
        except Exception as error:
            raise BacktestSubmissionError("Backtest submission is unavailable.") from error
        try:
            now = _utc_millisecond(datetime.now(UTC))
            await self._bind_submission_datasets(request, now)
            execution_fingerprint = _execution_fingerprint(
                request, strategy.definition.instrument.quote_currency
            )
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
        except StrategyDatasetMismatchError as error:
            raise BacktestSubmissionRejectedError(str(error)) from error
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
        for reference in request.reference_dataset_fingerprints:
            await self._strategy_store.bind_dataset(
                request.strategy_fingerprint,
                reference.dataset_fingerprint,
                dataset_store=self._dataset_store,
                bound_at=bound_at,
            )

    async def _publish_run(
        self,
        request: BacktestSubmissionRequest,
        strategy: StrategySnapshot,
        now: datetime,
        execution_fingerprint: str,
    ) -> PublishedResearchRunSpecification:
        """Build, verify, and idempotently publish one immutable run specification."""
        evaluation_start, evaluation_end = _filled_window(request)
        specification = ResearchRunSpecification(
            schema_version="1.0",
            run_id=uuid7(now),
            created_at=now,
            strategy_fingerprint=request.strategy_fingerprint,
            dataset_fingerprint=request.dataset_fingerprint,
            htf_dataset_fingerprint=request.htf_dataset_fingerprint,
            indicator_dataset_fingerprints=request.indicator_dataset_fingerprints,
            additional_instrument_datasets=request.additional_instrument_datasets,
            reference_dataset_fingerprints=request.reference_dataset_fingerprints,
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
                quote_currency=strategy.definition.instrument.quote_currency,
                initial_quote_balance=request.initial_quote_balance,
            ),
            costs=_cost_assumptions(request),
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


def resolve_backtest_window(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> tuple[datetime, datetime]:
    """Check one submission's bindings and window exactly as ``submit`` would, publishing nothing.

    Omitted dates resolve to the common coverage of every bound dataset (decision, HTF,
    extra clocks, extra products); supplied dates are verified against it. Rejections use
    :class:`BacktestSubmissionRejectedError` with the same messages as ``submit``. Portfolio
    backtests use this to intersect sleeve windows before any child run is published.
    """
    try:
        filled = _with_evaluation_window(request, strategy, dataset_store)
        _validate_submission_assumptions(
            filled, quote_currency=strategy.definition.instrument.quote_currency
        )
    except ValueError as error:
        if isinstance(error, BacktestSubmissionRejectedError):
            raise
        raise BacktestSubmissionRejectedError(str(error)) from error
    _require_htf_request(filled, strategy.definition)
    _require_indicator_dataset_request(filled, strategy.definition)
    _require_additional_instrument_request(filled, strategy.definition)
    _require_reference_dataset_request(filled, strategy.definition)
    return _filled_window(filled)


def _execution_fingerprint(
    request: BacktestSubmissionRequest,
    quote_currency: SpotQuoteCurrency,
) -> str:
    """Hash normalized simulation semantics so equivalent submissions are idempotent."""
    evaluation_start, evaluation_end = _filled_window(request)
    capital = CapitalAssumptions(
        quote_currency=quote_currency,
        initial_quote_balance=request.initial_quote_balance,
    )
    costs = _cost_assumptions(request)
    payload = {
        "capital": capital.model_dump(mode="json"),
        "costs": costs.model_dump(mode="json"),
        "dataset_fingerprint": request.dataset_fingerprint,
        "engine": BACKTEST_ENGINE,
        "evaluation_end": evaluation_end.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "random_seed": 0,
        "simulation_semantics": SIMULATION_SEMANTICS,
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
    if request.reference_dataset_fingerprints:
        payload["reference_dataset_fingerprints"] = [
            item.model_dump(mode="json") for item in request.reference_dataset_fingerprints
        ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{sha256(canonical.encode()).hexdigest()}"


def _utc_millisecond(value: datetime) -> datetime:
    """Normalize a server timestamp to the UUIDv7-representable UTC millisecond."""
    return value.astimezone(UTC).replace(microsecond=(value.microsecond // 1_000) * 1_000)
