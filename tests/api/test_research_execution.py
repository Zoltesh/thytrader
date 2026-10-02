"""The API queues research and waits; it never runs it (ADR 0092)."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.api.research_execution import sync_failure, wait_for_job
from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
)
from thytrader.config import Settings
from thytrader.research.jobs import (
    InMemoryResearchJobStore,
    ResearchExecutionMode,
    ResearchJobErrorCode,
    ResearchJobRecord,
    ResearchJobStatus,
)
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from uuid import UUID

_DATASET = "sha256:" + "d" * 64


class _Submitter:
    """Count submissions and answer with fixed fingerprints or a configured failure."""

    def __init__(self, failure: Exception | None = None) -> None:
        """Remember the failure to raise, if any."""
        self.calls = 0
        self._failure = failure

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Record the call; raise or return one completed submission."""
        del request
        self.calls += 1
        if self._failure is not None:
            raise self._failure
        return BacktestSubmissionResult(
            run_fingerprint="sha256:" + "c" * 64, result_fingerprint="sha256:" + "e" * 64
        )


def _client(
    submitter: _Submitter,
    *,
    mode: ResearchExecutionMode | None,
    sync_wait: float = 25.0,
) -> tuple[TestClient, UUID]:
    """Build one app with an in-memory strategy and return it with the strategy id."""
    strategies = InMemoryStrategyStore()
    record = asyncio.run(create_strategy_from_definition(strategies, create_template_strategy()))
    app = create_app(
        Settings(_env_file=None, research_sync_wait_seconds=sync_wait),
        strategy_store=strategies,
        backtest_submitter=submitter,
        research_execution=mode,
    )
    return TestClient(app), record.strategy_id


def _body(strategy_id: UUID) -> dict[str, object]:
    """Return one valid backtest start."""
    return {
        "strategy_id": str(strategy_id),
        "dataset_fingerprint": _DATASET,
        "evaluation_start": "2026-08-01T00:00:00Z",
        "evaluation_end": "2026-08-02T00:00:00Z",
        "initial_quote_balance": "10000",
        "maker_fee_rate": "0.001",
        "taker_fee_rate": "0.002",
        "fixed_slippage_bps": "1",
    }


def test_without_a_queue_research_submissions_fail_closed() -> None:
    """No PostgreSQL and no harness: nobody could run the job, so refuse instead of queuing."""
    submitter = _Submitter()
    client, strategy_id = _client(submitter, mode=None)
    with client:
        sync = client.post("/api/v1/backtests", json=_body(strategy_id))
        queued = client.post("/api/v1/backtests?async=true", json=_body(strategy_id))
        jobs = client.get(f"/api/v1/research/jobs?strategy_id={strategy_id}")
    assert sync.status_code == 503
    assert sync.json()["detail"] == "Backtest submission is unavailable."
    assert queued.status_code == 503
    assert jobs.json()["returned"] == 0
    assert submitter.calls == 0


def test_worker_mode_api_queues_and_waits_but_never_runs_research() -> None:
    """With a worker pool the API only queues; a sync submit returns 202 at its bound."""
    submitter = _Submitter()
    client, strategy_id = _client(
        submitter, mode=ResearchExecutionMode.RESEARCH_WORKER, sync_wait=0.3
    )
    with client:
        started = time.monotonic()
        response = client.post("/api/v1/backtests", json=_body(strategy_id))
        waited = time.monotonic() - started
        job = client.get(f"/api/v1/research/jobs/{response.json()['job_id']}").json()
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["sync_wait_seconds"] == 0.3
    assert body["status"] == "queued"
    assert waited >= 0.3
    assert job["status"] == "queued"
    assert submitter.calls == 0


def test_long_polls_do_not_hold_the_event_loop() -> None:
    """Four sync submits waiting on the worker leave cheap endpoints instant."""
    client, strategy_id = _client(
        _Submitter(), mode=ResearchExecutionMode.RESEARCH_WORKER, sync_wait=1.5
    )
    with client, ThreadPoolExecutor(max_workers=4) as pool:
        waits = [
            pool.submit(client.post, "/api/v1/backtests", json=_body(strategy_id)) for _ in range(4)
        ]
        time.sleep(0.2)
        latencies = []
        for _ in range(5):
            started = time.perf_counter()
            assert client.get("/health/live").status_code == 200
            latencies.append(time.perf_counter() - started)
        statuses = [future.result().status_code for future in waits]
    assert statuses == [202] * 4
    assert max(latencies) < 0.25, latencies


def test_sync_submit_maps_worker_failures_to_the_inline_statuses() -> None:
    """A rejected window is still 422 with its message; an outage is still a plain 503."""
    rejected = _Submitter(BacktestSubmissionRejectedError("The window does not fit the dataset."))
    client, strategy_id = _client(rejected, mode=ResearchExecutionMode.IN_PROCESS)
    with client:
        refused = client.post("/api/v1/backtests", json=_body(strategy_id))
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == {
        "code": "backtest_window_rejected",
        "message": "The window does not fit the dataset.",
    }
    broken = _Submitter(BacktestSubmissionError("boom"))
    client, strategy_id = _client(broken, mode=ResearchExecutionMode.IN_PROCESS)
    with client:
        failed = client.post("/api/v1/backtests", json=_body(strategy_id))
        listed = client.get(f"/api/v1/research/jobs?strategy_id={strategy_id}").json()
    assert failed.status_code == 503
    assert failed.json()["detail"] == "Backtest submission is unavailable."
    assert listed["jobs"][0]["error_code"] == "research_unavailable"


def test_sync_submit_completes_with_the_inline_body() -> None:
    """A finished job answers 201 with the same fields the inline submit returned."""
    submitter = _Submitter()
    client, strategy_id = _client(submitter, mode=ResearchExecutionMode.IN_PROCESS)
    with client:
        response = client.post("/api/v1/backtests", json=_body(strategy_id))
    assert response.status_code == 201, response.text
    assert set(response.json()) == {
        "run_fingerprint",
        "result_fingerprint",
        "strategy_id",
        "strategy_fingerprint",
        "bound_datasets",
    }
    assert response.json()["result_fingerprint"] == "sha256:" + "e" * 64
    assert submitter.calls == 1


def _record(status: ResearchJobStatus, code: ResearchJobErrorCode | None) -> ResearchJobRecord:
    """Return one job record in ``status`` with ``code``."""
    now = datetime.now(UTC)
    return ResearchJobRecord(
        job_id=uuid4(),
        kind="backtest",
        status=status,
        created_at=now,
        updated_at=now,
        expires_at=now,
        error_message="why",
        error_code=code,
    )


def test_sync_failure_codes_cover_cancel_and_lost_workers() -> None:
    """Cancelled is 409; a lost worker or an expired job is the route's 503."""
    cancelled = sync_failure(_record(ResearchJobStatus.CANCELLED, None), unavailable_detail="x")
    assert cancelled.status_code == 409
    lost = sync_failure(
        _record(ResearchJobStatus.FAILED, ResearchJobErrorCode.RESEARCH_WORKER_LOST),
        unavailable_detail="x",
    )
    assert (lost.status_code, lost.detail) == (503, "x")
    budget = sync_failure(
        _record(ResearchJobStatus.FAILED, ResearchJobErrorCode.STUDY_BUDGET_EXCEEDED),
        unavailable_detail="x",
    )
    assert budget.status_code == 422
    assert budget.detail == {"code": "study_budget_exceeded", "message": "why"}


def test_wait_for_job_returns_terminal_records_early_and_stops_at_the_bound() -> None:
    """The long-poll returns as soon as the job is final and never waits past its bound."""
    store = InMemoryResearchJobStore()
    request = BacktestSubmissionRequest(
        strategy_fingerprint="sha256:" + "a" * 64,
        dataset_fingerprint=_DATASET,
        evaluation_start=datetime(2026, 8, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 8, 2, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="1",
    )

    async def scenario() -> tuple[float, ResearchJobRecord | None, ResearchJobRecord | None]:
        record = await store.create_backtest(request, strategy_id=uuid4())
        started = time.monotonic()
        pending = await wait_for_job(store, record.job_id, timeout_seconds=0.2)
        bounded = time.monotonic() - started
        await store.mark_cancelled(record.job_id)
        done = await wait_for_job(store, record.job_id, timeout_seconds=30)
        return bounded, pending, done

    bounded, pending, done = asyncio.run(scenario())
    assert 0.2 <= bounded < 1.0
    assert pending is not None and pending.status is ResearchJobStatus.QUEUED
    assert done is not None and done.status is ResearchJobStatus.CANCELLED
