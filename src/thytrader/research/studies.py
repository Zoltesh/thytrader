"""Submit walk-forward, OOS, cross-market, sweep, and WFO research studies.

``ResearchStudyService`` loads published strategies, submits or reuses each planned
child backtest, and persists the assembled study. This module is also the public
facade of the study contract: ``__all__`` re-exports the documents
(:mod:`~thytrader.research.study_models`), planning, identities, assembly, and
summaries so callers keep one import path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING

from thytrader.backtest.submission import BacktestSubmissionRejectedError
from thytrader.evaluation.publication import explain_evaluation_window_rejection
from thytrader.market_data.datasets import DatasetStoreError
from thytrader.research.catalog import (
    ResearchStudyCatalog,
    StudyCatalogUnavailableError,
)
from thytrader.research.study_assembly import (
    _annotate_selections,
    _derived_stitched_equity,
    _stitch_warnings,
    aggregate_windows,
)
from thytrader.research.study_identity import (
    canonical_study_json,
    plan_fingerprint,
    request_fingerprint,
    study_fingerprint,
)
from thytrader.research.study_models import (
    ASYNC_STUDY_BUDGET,
    STUDY_CONTRACT_VERSION,
    SYNC_STUDY_BUDGET,
    FoldMode,
    MarketBinding,
    PlannedStudyWindow,
    ResearchStudy,
    ResearchStudyPlan,
    ResearchStudyPlanSummary,
    ResearchStudyRequest,
    ResearchStudySummary,
    StudyAggregate,
    StudyBudget,
    StudyCandidateAggregate,
    StudyFailedPhase,
    StudyKind,
    StudyWindowPnl,
    StudyWindowResult,
    WindowRole,
)
from thytrader.research.study_planning import (
    StudyBudgetError,
    StudyPlanningError,
    _merge_derived_candidates,
    plan_study,
    study_candidate_count,
    window_submission_request,
)
from thytrader.research.study_summaries import (
    study_candidate_aggregates,
    study_candidate_fingerprints,
    study_catalog_summary,
    summarize_research_study,
    summarize_research_study_plan,
)
from thytrader.strategies.snapshots import StrategySnapshotError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from thytrader.backtest.models import BacktestResult
    from thytrader.backtest.results import BacktestResultReader
    from thytrader.backtest.submission import BacktestSubmitter
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import (
        StrategySnapshot,
        StrategySnapshotReader,
        StrategySnapshotStore,
    )

__all__ = [
    "ASYNC_STUDY_BUDGET",
    "STUDY_CONTRACT_VERSION",
    "SYNC_STUDY_BUDGET",
    "FoldMode",
    "MarketBinding",
    "PlannedStudyWindow",
    "ResearchStudy",
    "ResearchStudyError",
    "ResearchStudyPlan",
    "ResearchStudyPlanSummary",
    "ResearchStudyRequest",
    "ResearchStudyService",
    "ResearchStudySummary",
    "StudyAggregate",
    "StudyBudget",
    "StudyBudgetError",
    "StudyCandidateAggregate",
    "StudyFailedPhase",
    "StudyKind",
    "StudyPlanningError",
    "StudyWindowPnl",
    "StudyWindowResult",
    "WindowRole",
    "aggregate_windows",
    "canonical_study_json",
    "load_candidate_definitions",
    "plan_fingerprint",
    "plan_study",
    "request_fingerprint",
    "study_candidate_aggregates",
    "study_candidate_count",
    "study_candidate_fingerprints",
    "study_catalog_summary",
    "study_fingerprint",
    "summarize_research_study",
    "summarize_research_study_plan",
    "window_submission_request",
]

_logger = logging.getLogger(__name__)


class ResearchStudyError(RuntimeError):
    """Report a redacted study failure without trading authority.

    ``failed_phase`` names the submission stage that failed so operators and
    agents can decide what to inspect or retry. It stays ``None`` on plain
    construction for backward compatibility.
    """

    def __init__(self, message: str, *, failed_phase: str | None = None) -> None:
        """Store the message and the optional structured failure phase."""
        super().__init__(message)
        self.failed_phase = failed_phase


async def load_candidate_definitions(
    publications: StrategySnapshotReader, study: ResearchStudy
) -> dict[str, StrategyDefinition]:
    """Load every candidate snapshot a study names, skipping ones that cannot be read.

    Axis values are explanatory, so an unreadable snapshot leaves that candidate's
    ``axis_values`` empty instead of failing the summary.
    """
    definitions: dict[str, StrategyDefinition] = {}
    for fingerprint in study_candidate_fingerprints(study):
        try:
            definitions[fingerprint] = (await publications.load(fingerprint)).definition
        except Exception as error:  # noqa: BLE001 - advisory enrichment only.
            _logger.warning(
                "study_candidate_snapshot_unavailable error_class=%s", type(error).__name__
            )
    return definitions


@dataclass(frozen=True, slots=True)
class ResearchStudyService:
    """Plan and submit studies by composing existing backtest submissions."""

    publications: StrategySnapshotStore
    submitter: BacktestSubmitter
    results: BacktestResultReader
    catalog: ResearchStudyCatalog | None = None
    datasets: DatasetStore | None = None

    async def plan(
        self, request: ResearchStudyRequest, *, budget: StudyBudget = ASYNC_STUDY_BUDGET
    ) -> ResearchStudyPlan:
        """Return the window schedule after loading published strategies."""
        published = await self._load_publications(request)
        published = _merge_derived_candidates(request, published)
        plan = plan_study(request, publications=published, budget=budget)
        self._reject_windows_outside_datasets(plan, published)
        return plan

    async def submit(
        self, request: ResearchStudyRequest, *, budget: StudyBudget = SYNC_STUDY_BUDGET
    ) -> ResearchStudy:
        """Submit or reuse each child backtest inside this call (small synchronous budget)."""
        return await self.submit_with_progress(request, budget=budget)

    async def submit_with_progress(
        self,
        request: ResearchStudyRequest,
        *,
        on_progress: Callable[[int, int], Awaitable[object]] | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
        budget: StudyBudget = ASYNC_STUDY_BUDGET,
    ) -> ResearchStudy:
        """Submit or reuse each child backtest and assemble the derived study.

        The research worker runs async jobs here with the larger async budget.
        """
        published = await self._publish_derived_candidates(
            request, await self._load_publications(request)
        )
        plan = plan_study(request, publications=published, budget=budget)
        self._reject_windows_outside_datasets(plan, published)
        existing = await self._load_existing_plan(plan.plan_fingerprint)
        if existing is not None:
            return existing
        children, loaded_results = await self._submit_windows(
            request,
            plan,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        windows = _annotate_selections(request, children)
        stitched = _derived_stitched_equity(request, windows, loaded_results)
        selected_only = request.kind is StudyKind.WALK_FORWARD_OPTIMIZATION
        assembled = ResearchStudy(
            study_fingerprint="sha256:" + ("0" * 64),
            request_fingerprint=plan.request_fingerprint,
            kind=request.kind,
            windows=windows,
            aggregate=aggregate_windows(windows, selected_only=selected_only),
            warnings=_stitch_warnings(plan.warnings, stitched),
            selection_metric=(
                request.selection_metric
                if request.kind in {StudyKind.PARAMETER_SWEEP, StudyKind.WALK_FORWARD_OPTIMIZATION}
                else None
            ),
            stitched_oos_equity=stitched,
        )
        study = assembled.model_copy(update={"study_fingerprint": study_fingerprint(assembled)})
        await self._persist_catalog(study, plan)
        return study

    async def _load_existing_plan(self, plan_fingerprint_value: str) -> ResearchStudy | None:
        """Return a persisted study when the effective plan already exists."""
        if self.catalog is None:
            return None
        try:
            canonical = await self.catalog.find_by_plan_fingerprint(plan_fingerprint_value)
        except StudyCatalogUnavailableError as error:
            raise ResearchStudyError(
                "Research study catalog is unavailable.",
                failed_phase=StudyFailedPhase.PERSIST_STUDY.value,
            ) from error
        if canonical is None:
            return None
        return ResearchStudy.model_validate_json(canonical)

    async def _persist_catalog(self, study: ResearchStudy, plan: ResearchStudyPlan) -> None:
        """Store the assembled study when a catalog is configured."""
        if self.catalog is None:
            return
        try:
            await self.catalog.persist(
                study_catalog_summary(study, plan),
                canonical_study_json(study),
            )
        except StudyCatalogUnavailableError as error:
            raise ResearchStudyError(
                "Research study catalog is unavailable.",
                failed_phase=StudyFailedPhase.PERSIST_STUDY.value,
            ) from error

    async def _submit_windows(
        self,
        request: ResearchStudyRequest,
        plan: ResearchStudyPlan,
        *,
        on_progress: Callable[[int, int], Awaitable[object]] | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[tuple[StudyWindowResult, ...], dict[str, BacktestResult]]:
        """Submit every planned child and keep result documents for stitching."""
        children: list[StudyWindowResult] = []
        loaded_results: dict[str, BacktestResult] = {}
        total = len(plan.windows)
        try:
            for index, window in enumerate(plan.windows, start=1):
                if cancel_check is not None and await cancel_check():
                    _raise_cancelled_study()
                if on_progress is not None:
                    await on_progress(index - 1, total)
                submission = window_submission_request(request, window)
                identities = await self.submitter.submit(submission)
                result = await self.results.load(identities.result_fingerprint)
                loaded_results[identities.result_fingerprint] = result
                children.append(
                    StudyWindowResult(
                        label=window.label,
                        role=window.role,
                        fold_index=window.fold_index,
                        product_id=window.product_id,
                        run_fingerprint=identities.run_fingerprint,
                        result_fingerprint=identities.result_fingerprint,
                        strategy_fingerprint=window.strategy_fingerprint,
                        evaluation_start=window.evaluation_start,
                        evaluation_end=window.evaluation_end,
                        summary=result.summary,
                    )
                )
            if on_progress is not None:
                await on_progress(total, total)
        except StudyPlanningError:
            raise
        except BacktestSubmissionRejectedError:
            raise
        except ResearchStudyError:
            raise
        except Exception as error:
            raise ResearchStudyError(
                f"Child window submission failed: {error}",
                failed_phase=StudyFailedPhase.SUBMIT_CHILDREN.value,
            ) from error
        return tuple(children), loaded_results

    async def _publish_derived_candidates(
        self,
        request: ResearchStudyRequest,
        publications: dict[str, StrategySnapshot],
    ) -> dict[str, StrategySnapshot]:
        """Persist missing axis-derived fingerprints, then return the merged map."""
        merged = _merge_derived_candidates(request, publications)
        if not request.parameter_axes:
            return merged
        published = dict(publications)
        try:
            for fingerprint, candidate in merged.items():
                if fingerprint in publications:
                    continue
                loaded = await self._load_or_publish_derived(fingerprint, candidate)
                published[loaded.strategy_fingerprint] = loaded
        except StudyPlanningError:
            raise
        except StrategySnapshotError as error:
            if "was not found" in str(error):
                raise StudyPlanningError(str(error)) from error
            raise ResearchStudyError(
                f"Derived candidate publication failed: {error}",
                failed_phase=StudyFailedPhase.PUBLISH_DERIVED.value,
            ) from error
        except Exception as error:
            raise ResearchStudyError(
                f"Derived candidate publication failed: {error}",
                failed_phase=StudyFailedPhase.PUBLISH_DERIVED.value,
            ) from error
        return published

    async def _load_or_publish_derived(
        self,
        fingerprint: str,
        candidate: StrategySnapshot,
    ) -> StrategySnapshot:
        """Reuse a stored derived fingerprint or persist it for the first time."""
        try:
            return await self.publications.load(fingerprint)
        except StrategySnapshotError as error:
            if "was not found" not in str(error):
                raise
            return await self.publications.record_snapshot(candidate.definition)

    def _reject_windows_outside_datasets(
        self,
        plan: ResearchStudyPlan,
        publications: dict[str, StrategySnapshot],
    ) -> None:
        """Reject planned windows with the same dataset bound check as child backtests."""
        if self.datasets is None:
            return
        seen: dict[str, tuple[datetime, datetime]] = {}
        for window in plan.windows:
            bounds = seen.get(window.dataset_fingerprint)
            if bounds is None:
                try:
                    manifest = self.datasets.load_manifest(window.dataset_fingerprint)
                except DatasetStoreError as error:
                    raise StudyPlanningError(
                        "The selected dataset was not found or is not a verified complete artifact."
                    ) from error
                try:
                    bounds = (
                        _coverage_instant(manifest.starts_at),
                        _coverage_instant(manifest.ends_at),
                    )
                except ValueError as error:
                    raise StudyPlanningError("Dataset coverage timestamps are invalid.") from error
                seen[window.dataset_fingerprint] = bounds
            published = publications[window.strategy_fingerprint]
            rejection = explain_evaluation_window_rejection(
                dataset_starts_at=bounds[0],
                dataset_ends_at=bounds[1],
                evaluation_start=window.evaluation_start,
                evaluation_end=window.evaluation_end,
                warmup_bars=published.definition.data_requirements.warmup_bars,
                timeframe=published.definition.timeframe,
            )
            if rejection is not None:
                raise StudyPlanningError(rejection)

    async def _load_publications(
        self, request: ResearchStudyRequest
    ) -> dict[str, StrategySnapshot]:
        """Load every published strategy named by the study request."""
        fingerprints = _strategy_fingerprints(request)
        loaded: dict[str, StrategySnapshot] = {}
        try:
            for fingerprint in fingerprints:
                loaded[fingerprint] = await self.publications.load(fingerprint)
        except StrategySnapshotError as error:
            if "was not found" in str(error):
                raise StudyPlanningError("Published strategy was not found.") from error
            raise ResearchStudyError(
                f"Published strategy load failed: {error}",
                failed_phase=StudyFailedPhase.PUBLISH_DERIVED.value,
            ) from error
        except StudyPlanningError:
            raise
        except Exception as error:
            raise ResearchStudyError(
                f"Published strategy load failed: {error}",
                failed_phase=StudyFailedPhase.UNKNOWN.value,
            ) from error
        return loaded


def _strategy_fingerprints(request: ResearchStudyRequest) -> tuple[str, ...]:
    """Collect unique strategy fingerprints for publication loads."""
    if request.kind is StudyKind.CROSS_MARKET:
        if request.markets is None:
            raise StudyPlanningError("cross_market studies require markets.")
        return tuple(dict.fromkeys(item.strategy_fingerprint for item in request.markets))
    if request.strategy_fingerprint is None:
        raise StudyPlanningError("This study kind requires strategy_fingerprint.")
    fingerprints = [request.strategy_fingerprint]
    fingerprints.extend(request.candidate_strategy_fingerprints)
    return tuple(dict.fromkeys(fingerprints))


def _coverage_instant(value: str) -> datetime:
    """Parse one dataset coverage timestamp as timezone-aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Dataset coverage timestamps are invalid.")
    return parsed.astimezone(UTC)


def _raise_cancelled_study() -> None:
    """Abort study submission when a durable job cancellation was requested."""
    raise ResearchStudyError("Research job was cancelled.")
