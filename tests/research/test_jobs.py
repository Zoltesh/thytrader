"""Unit tests for durable async research jobs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from thytrader.backtest.submission import BacktestSubmissionRequest, BacktestSubmissionResult
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
        engine_contract_version="thytrader-bar-backtest-v1",
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
        record = await store.create_backtest(_backtest_request())
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
        record = await store.create_backtest(_backtest_request())
        stale = record.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(hours=1)})
        store._records[record.job_id] = stale
        expired = await store.expire_stale()
        polled = await store.get(record.job_id)
        assert expired == 1
        assert polled is not None
        assert polled.status is ResearchJobStatus.EXPIRED

    asyncio.run(_scenario())
