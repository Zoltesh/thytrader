"""Unit tests for durable async research jobs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
)
from thytrader.research.jobs import (
    InMemoryResearchJobStore,
    ResearchJobStatus,
    run_backtest_job,
)
from thytrader.research.studies import (
    ResearchStudyPlan,
    StudyKind,
    plan_fingerprint,
    summarize_research_study_plan,
)

_STRATEGY_ID = UUID("01985cf0-7b60-7000-8000-00000000abcd")


def _backtest_request() -> BacktestSubmissionRequest:
    """Return one minimal backtest submission."""
    return BacktestSubmissionRequest(
        strategy_fingerprint="sha256:" + "a" * 64,
        dataset_fingerprint="sha256:" + "b" * 64,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 2, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
    )


class _ImmediateSubmitter:
    """Return fixed fingerprints for async backtest job tests."""

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Ignore the request and return one completed submission."""
        del request
        return BacktestSubmissionResult(
            run_fingerprint="sha256:" + "c" * 64,
            result_fingerprint="sha256:" + "d" * 64,
        )


def test_in_memory_job_store_runs_backtest_to_completion() -> None:
    """Queued backtests complete with immutable fingerprints."""
    store = InMemoryResearchJobStore()

    async def _scenario() -> None:
        record = await store.create_backtest(_backtest_request(), strategy_id=_STRATEGY_ID)
        assert record.progress_current == 0
        assert record.progress_total >= 1
        await run_backtest_job(store, _ImmediateSubmitter(), record.job_id, _backtest_request())
        polled = await store.get(record.job_id)
        assert polled is not None
        assert polled.status is ResearchJobStatus.COMPLETED
        assert polled.result_fingerprint == "sha256:" + "d" * 64

    asyncio.run(_scenario())


def test_plan_fingerprint_ignores_request_bounds_when_windows_match() -> None:
    """Equivalent effective windows share one plan fingerprint."""
    window = {
        "label": "fold-0-oos",
        "role": "out_of_sample",
        "fold_index": 0,
        "product_id": "DOGE-USD",
        "timeframe": "4h",
        "strategy_fingerprint": "sha256:" + "a" * 64,
        "dataset_fingerprint": "sha256:" + "b" * 64,
        "evaluation_start": "2026-01-01T00:00:00Z",
        "evaluation_end": "2026-01-11T00:00:00Z",
    }
    first = ResearchStudyPlan.model_validate(
        {
            "kind": "walk_forward_optimization",
            "request_fingerprint": "sha256:" + "1" * 64,
            "plan_fingerprint": "sha256:" + ("0" * 64),
            "timeframe": "4h",
            "windows": [window],
        }
    )
    second = first.model_copy(
        update={
            "request_fingerprint": "sha256:" + "2" * 64,
            "plan_fingerprint": plan_fingerprint(first),
        }
    )
    second = second.model_copy(update={"plan_fingerprint": plan_fingerprint(second)})
    assert plan_fingerprint(first) == plan_fingerprint(second)


def test_summarize_research_study_plan_omits_windows() -> None:
    """Compact planner output reports counts without child windows."""
    plan = ResearchStudyPlan.model_validate(
        {
            "kind": "walk_forward_optimization",
            "request_fingerprint": "sha256:" + "1" * 64,
            "plan_fingerprint": "sha256:" + "2" * 64,
            "timeframe": "4h",
            "windows": [
                {
                    "label": "fold-0-is",
                    "role": "in_sample",
                    "fold_index": 0,
                    "product_id": "DOGE-USD",
                    "timeframe": "4h",
                    "strategy_fingerprint": "sha256:" + "a" * 64,
                    "dataset_fingerprint": "sha256:" + "b" * 64,
                    "evaluation_start": "2026-01-01T00:00:00Z",
                    "evaluation_end": "2026-01-05T00:00:00Z",
                },
                {
                    "label": "fold-0-oos",
                    "role": "out_of_sample",
                    "fold_index": 0,
                    "product_id": "DOGE-USD",
                    "timeframe": "4h",
                    "strategy_fingerprint": "sha256:" + "a" * 64,
                    "dataset_fingerprint": "sha256:" + "b" * 64,
                    "evaluation_start": "2026-01-05T00:00:00Z",
                    "evaluation_end": "2026-01-11T00:00:00Z",
                },
            ],
        }
    )
    summary = summarize_research_study_plan(plan)
    assert summary.kind is StudyKind.WALK_FORWARD_OPTIMIZATION
    assert summary.window_count == 2
    assert summary.fold_count == 1
    assert "windows" not in summary.model_dump(mode="json")


def test_in_memory_job_store_expires_stale_jobs() -> None:
    """Overdue queued jobs transition to expired."""
    store = InMemoryResearchJobStore()

    async def _scenario() -> None:
        record = await store.create_backtest(_backtest_request(), strategy_id=_STRATEGY_ID)
        stale = record.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(hours=1)})
        store._records[record.job_id] = stale
        expired = await store.expire_stale()
        polled = await store.get(record.job_id)
        assert expired == 1
        assert polled is not None
        assert polled.status is ResearchJobStatus.EXPIRED

    asyncio.run(_scenario())


class _FailingSubmitter:
    """Raise one configured submission failure."""

    def __init__(self, error: Exception) -> None:
        """Remember the failure to raise."""
        self._error = error

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Refuse the request with the configured failure."""
        del request
        raise self._error


def test_claim_marks_the_job_running_and_counts_the_attempt() -> None:
    """Claiming is the start of an attempt: running, attempts + 1, oldest first."""
    store = InMemoryResearchJobStore()

    async def _scenario() -> None:
        first = await store.create_backtest(_backtest_request(), strategy_id=_STRATEGY_ID)
        await store.create_backtest(_backtest_request(), strategy_id=_STRATEGY_ID)
        claimed = await store.claim_next()
        assert claimed is not None
        assert claimed[0] == first.job_id
        record = await store.get(first.job_id)
        assert record is not None
        assert record.status is ResearchJobStatus.RUNNING
        assert record.attempts == 1

    asyncio.run(_scenario())


def test_backtest_failures_record_the_code_the_sync_route_answers_with() -> None:
    """A rejected window is caller input; any other failure is research_unavailable."""
    store = InMemoryResearchJobStore()

    async def _scenario() -> None:
        for error, code in (
            (BacktestSubmissionRejectedError("bad window"), "backtest_window_rejected"),
            (BacktestSubmissionError("down"), "research_unavailable"),
            (RuntimeError("bug"), "research_unavailable"),
        ):
            record = await store.create_backtest(_backtest_request(), strategy_id=_STRATEGY_ID)
            await run_backtest_job(
                store, _FailingSubmitter(error), record.job_id, _backtest_request()
            )
            failed = await store.get(record.job_id)
            assert failed is not None
            assert failed.status is ResearchJobStatus.FAILED
            assert failed.error_code is not None
            assert failed.error_code.value == code

    asyncio.run(_scenario())
