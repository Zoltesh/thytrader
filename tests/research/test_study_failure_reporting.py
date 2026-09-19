"""Regression tests: study failures must surface real causes, phases, and detail.

Operator QA (2026-09-18) showed parameter-sweep studies failing with the
constant ``Research study submission is unavailable.`` on the CLI and in async
job records, hiding the child backtest rejection reason and the phase that
failed. These tests pin the corrected contracts: real error text, a structured
``failed_phase`` on service errors, and ``failed_phase``/``failed_detail`` on
durable job records.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from thytrader.backtest.submission import BacktestSubmissionRejectedError
from thytrader.research.jobs import (
    InMemoryResearchJobStore,
    ResearchJobStatus,
    run_study_job,
)
from thytrader.research.parameter_sweep import ParameterAxis
from thytrader.research.studies import (
    ResearchStudyError,
    ResearchStudyRequest,
    ResearchStudyService,
    StudyKind,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyPublicationError,
)

if TYPE_CHECKING:
    from thytrader.backtest.submission import BacktestSubmitter
    from thytrader.persistence.backtest_results import BacktestResultReader
    from thytrader.strategies.publication import StrategyPublicationStore

_REFERENCE = Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"


def _published() -> PublishedStrategy:
    """Load the golden reference as a published strategy."""
    definition = StrategyDefinition.model_validate_json(_REFERENCE.read_text(encoding="utf-8"))
    return PublishedStrategy(
        strategy_fingerprint=strategy_fingerprint(definition), definition=definition
    )


def _holdout_request() -> ResearchStudyRequest:
    """Return a valid OOS holdout request without parameter axes."""
    return ResearchStudyRequest(
        kind=StudyKind.OOS_HOLDOUT,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 11, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint="sha256:" + "a" * 64,
        dataset_fingerprint="sha256:" + "b" * 64,
        oos_fraction="0.3",
    )


def _sweep_request() -> ResearchStudyRequest:
    """Return a valid two-candidate parameter-sweep request."""
    published = _published()
    return ResearchStudyRequest(
        kind=StudyKind.PARAMETER_SWEEP,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 11, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint=published.strategy_fingerprint,
        dataset_fingerprint="sha256:" + "b" * 64,
        parameter_axes=(
            ParameterAxis(indicator_id="ema_fast", parameter="period", values=("12", "26")),
        ),
    )


class _RejectionSubmitter:
    """Reject the first child submission with a caller-facing rejection."""

    def __init__(self, message: str) -> None:
        """Store the rejection text raised on submit."""
        self.message = message

    async def submit(self, request: object) -> object:
        """Refuse the child window with the stored rejection."""
        del request
        raise BacktestSubmissionRejectedError(self.message)


class _UnexpectedSubmitter:
    """Fail the first child submission with a non-domain runtime error."""

    async def submit(self, request: object) -> object:
        """Crash mid-submission like a storage or transport failure would."""
        del request
        raise RuntimeError("child runner crashed on window 2")


class _StaticPublications:
    """Return one fixed publication for every fingerprint load."""

    def __init__(self, published: PublishedStrategy) -> None:
        """Store the stand-in publication."""
        self._published = published

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return the fixed publication regardless of the requested fingerprint."""
        del strategy_fingerprint_value
        return self._published

    async def publish(self, definition: object) -> PublishedStrategy:
        """Refuse publication; these tests never expect a write."""
        del definition
        raise AssertionError("unexpected publish")

    async def publish_draft(
        self, definition: object, *, expected_revision: int
    ) -> PublishedStrategy:
        """Refuse draft publication; these tests never expect a write."""
        del definition, expected_revision
        raise AssertionError("unexpected publish_draft")


class _DerivedPublishFails:
    """Base publication loads; derived fingerprint load misses, publish crashes."""

    def __init__(self, published: PublishedStrategy, base_fingerprint: str) -> None:
        """Store the base publication and its fingerprint."""
        self._published = published
        self._base = base_fingerprint

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Serve the base publication; report derived fingerprints as missing."""
        if strategy_fingerprint_value == self._base:
            return self._published
        raise StrategyPublicationError("Published strategy was not found.")

    async def publish(self, definition: object) -> PublishedStrategy:
        """Crash the publication write like a storage failure would."""
        del definition
        raise RuntimeError("storage write failed")

    async def publish_draft(
        self, definition: object, *, expected_revision: int
    ) -> PublishedStrategy:
        """Unused by the service submit path."""
        del definition, expected_revision
        raise AssertionError("unexpected publish_draft")


class _ResultsUnavailable:
    """Result reader stand-in that must never be reached in failure tests."""

    async def load(self, result_fingerprint: str) -> object:
        """Refuse result loads; failing submissions never read results."""
        del result_fingerprint
        raise AssertionError("unexpected result load")


def _service(
    publications: object,
    submitter: object,
) -> ResearchStudyService:
    """Build the study service with failing-test doubles."""
    return ResearchStudyService(
        publications=cast("StrategyPublicationStore", publications),
        submitter=cast("BacktestSubmitter", submitter),
        results=cast("BacktestResultReader", _ResultsUnavailable()),
    )


def test_child_rejection_propagates_real_reason_with_phase() -> None:
    """A rejected child window must keep its real reason and name the phase."""
    service = _service(
        _StaticPublications(_published()),
        _RejectionSubmitter("HTTP 422: The evaluation window does not fit the selected dataset."),
    )
    with pytest.raises(BacktestSubmissionRejectedError) as raised:
        asyncio.run(service.submit(_holdout_request()))
    assert "evaluation window does not fit" in str(raised.value)


def test_unexpected_child_failure_names_cause_and_phase() -> None:
    """An unexpected child failure must expose the cause, not a constant string."""
    service = _service(_StaticPublications(_published()), _UnexpectedSubmitter())
    with pytest.raises(ResearchStudyError) as raised:
        asyncio.run(service.submit(_holdout_request()))
    assert "child runner crashed on window 2" in str(raised.value)
    assert raised.value.failed_phase == "submit_children"
    assert isinstance(raised.value.__cause__, RuntimeError)


def test_derived_publish_failure_names_cause_and_phase() -> None:
    """A crash while publishing derived variants must name the publish phase."""
    published = _published()
    request = _sweep_request()
    service = _service(
        _DerivedPublishFails(published, request.strategy_fingerprint or ""),
        _UnexpectedSubmitter(),
    )
    with pytest.raises(ResearchStudyError) as raised:
        asyncio.run(service.submit(request))
    assert "storage write failed" in str(raised.value)
    assert raised.value.failed_phase == "publish_derived"
    assert isinstance(raised.value.__cause__, RuntimeError)


class _FailingStudyService:
    """Study-service double whose submission fails in a declared phase."""

    def __init__(
        self,
        error: Exception,
        *,
        failed_phase: str | None = None,
    ) -> None:
        """Store the error to raise and its optional structured phase."""
        self._error = error
        self._failed_phase = failed_phase

    async def submit_with_progress(
        self,
        request: ResearchStudyRequest,
        *,
        on_progress: object = None,
        cancel_check: object = None,
    ) -> object:
        """Refuse the study with the configured failure."""
        del request, on_progress, cancel_check
        error = self._error
        if isinstance(error, ResearchStudyError) and self._failed_phase is not None:
            error.failed_phase = self._failed_phase
        raise error


def test_run_study_job_records_phase_from_service_error() -> None:
    """A failed study job must keep the service phase and the real message."""
    request = _holdout_request()
    store = InMemoryResearchJobStore()
    job_id = asyncio.run(store.create_study(request)).job_id
    service = _FailingStudyService(
        ResearchStudyError("Child backtest failed: HTTP 422: window does not fit dataset."),
        failed_phase="submit_children",
    )
    asyncio.run(run_study_job(store, cast("ResearchStudyService", service), job_id, request))
    record = asyncio.run(store.get(job_id))
    assert record is not None
    assert record.status is ResearchJobStatus.FAILED
    assert record.failed_phase == "submit_children"
    assert record.error_message is not None
    assert "window does not fit" in record.error_message


def test_run_study_job_unexpected_failure_keeps_detail() -> None:
    """An unexpected job failure must keep the redacted message plus detail."""
    request = _holdout_request()
    store = InMemoryResearchJobStore()
    job_id = asyncio.run(store.create_study(request)).job_id
    service = _FailingStudyService(RuntimeError("queue backend exploded"))
    asyncio.run(run_study_job(store, cast("ResearchStudyService", service), job_id, request))
    record = asyncio.run(store.get(job_id))
    assert record is not None
    assert record.status is ResearchJobStatus.FAILED
    assert record.failed_phase == "unknown"
    assert record.failed_detail == "queue backend exploded"
    assert record.error_message == "Research study submission is unavailable."


def test_run_study_job_rejected_child_records_phase_and_reason() -> None:
    """A rejected child window must fail the job with its real 422 reason."""
    request = _holdout_request()
    store = InMemoryResearchJobStore()
    job_id = asyncio.run(store.create_study(request)).job_id
    service = _FailingStudyService(
        BacktestSubmissionRejectedError(
            "HTTP 422: The evaluation window does not fit the selected dataset."
        )
    )
    asyncio.run(run_study_job(store, cast("ResearchStudyService", service), job_id, request))
    record = asyncio.run(store.get(job_id))
    assert record is not None
    assert record.status is ResearchJobStatus.FAILED
    assert record.failed_phase == "submit_children"
    assert record.error_message is not None
    assert "evaluation window does not fit" in record.error_message
    assert record.failed_detail == record.error_message
