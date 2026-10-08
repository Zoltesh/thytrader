"""End-to-end research worker pool coverage against live PostgreSQL (ADR 0092).

Each test runs the real ``python -m thytrader.research_worker`` supervisor as a
subprocess (spawned worker processes, real simulations on generated datasets) next to
an API app in this process. Needs ``THYTRADER_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
import signal
import statistics
import time
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from tests.portfolios.fixtures import DATA_START, write_dataset
from tests.research_worker.support import (
    TEST_DATABASE_URL,
    cloned_database,
    execute,
    migrated_template,
    research_worker_pool,
    rows,
    scalar,
    wait_until,
    worker_log,
)
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_backtest_submitter import PostgresBacktestSubmitter
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_portfolios import PostgresPortfolioStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.portfolios.backtest import PortfolioBacktestRequest
from thytrader.portfolios.jobs import PortfolioBacktestRunner
from thytrader.portfolios.models import MutationContext, PortfolioCreateRequest, SleeveAddRequest
from thytrader.portfolios.planning import plan_portfolio_backtest
from thytrader.research.jobs import ResearchExecutionMode
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from uuid import UUID

    from thytrader.strategies.models import StrategyDefinition

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)

_HEAVY_BARS = 4000
_LIGHT_BARS = 600
_TERMINAL = {"completed", "failed", "cancelled", "expired"}
_CONTEXT = MutationContext(
    actor="operator", channel="api", occurred_at=datetime(2026, 10, 2, 12, tzinfo=UTC)
)
# Decoded HTTP JSON is a dynamic boundary in these tests (same convention as test_portfolios_api).
type Json = dict[str, Any]


class Datasets:
    """Generated verified datasets shared by every test in the module."""

    def __init__(self, root: Path) -> None:
        """Write the heavy and light BTC datasets plus an ETH 4h dataset."""
        self.root = root
        self.heavy = write_dataset(root, "BTC-USDC", "1h", start=DATA_START, count=_HEAVY_BARS)
        self.eth = write_dataset(
            root, "ETH-USDC", "4h", start=DATA_START + timedelta(days=1), count=120, base=50
        )


@pytest.fixture(scope="module")
def template() -> Iterator[str]:
    """Migrate one template database for the module."""
    with migrated_template() as name:
        yield name


@pytest.fixture(scope="module")
def datasets(tmp_path_factory: pytest.TempPathFactory) -> Datasets:
    """Write the module's datasets once."""
    return Datasets(tmp_path_factory.mktemp("research-datasets"))


@pytest.fixture(scope="module")
def btc_definition() -> StrategyDefinition:
    """One BTC-USDC 1h definition reused across databases (identical fingerprints)."""
    return create_template_strategy(product_id="BTC-USDC", timeframe="1h")


@pytest.fixture
def database(template: str) -> Iterator[str]:
    """Clone a fresh database for one test."""
    with cloned_database(template) as url:
        yield url


def _seed(url: str, definition: StrategyDefinition) -> UUID:
    """Create ``definition`` as a strategy in database ``url``."""

    async def create() -> UUID:
        engine = create_engine(SecretStr(url))
        try:
            record = await create_strategy_from_definition(
                PostgresStrategyStore(engine), definition
            )
            return record.strategy_id
        finally:
            await dispose(engine)

    return asyncio.run(create())


@contextmanager
def _api(
    url: str,
    root: Path,
    *,
    mode: ResearchExecutionMode | None = None,
    sync_wait: float = 90.0,
) -> Iterator[TestClient]:
    """Run one API app against ``url`` (research worker mode unless ``mode`` says otherwise)."""
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(url),
        market_data_dataset_root=root,
        research_sync_wait_seconds=sync_wait,
        research_job_lease_seconds=3,
    )
    with TestClient(create_app(settings, research_execution=mode)) as client:
        yield client


def _backtest(strategy_id: UUID, dataset: str, bars: int, slippage: int = 5) -> Json:
    """Return one backtest start over the last ``bars - 241`` hours of a dataset."""
    return {
        "strategy_id": str(strategy_id),
        "dataset_fingerprint": dataset,
        "evaluation_start": (DATA_START + timedelta(days=10)).isoformat(),
        "evaluation_end": (DATA_START + timedelta(hours=bars - 1)).isoformat(),
        "initial_quote_balance": "10000",
        "maker_fee_rate": "0.001",
        "taker_fee_rate": "0.002",
        "fixed_slippage_bps": str(slippage),
    }


def _walk_forward(strategy_id: UUID, dataset: str, *, folds_end_hours: int, step: int) -> Json:
    """Return one walk-forward study over the heavy dataset."""
    return {
        "kind": "walk_forward",
        "strategy_id": str(strategy_id),
        "dataset_fingerprint": dataset,
        "evaluation_start": (DATA_START + timedelta(days=10)).isoformat(),
        "evaluation_end": (DATA_START + timedelta(hours=folds_end_hours)).isoformat(),
        "in_sample_bars": 300,
        "out_of_sample_bars": 100,
        "step_bars": step,
        "initial_quote_balance": "10000",
        "maker_fee_rate": "0.001",
        "taker_fee_rate": "0.002",
        "fixed_slippage_bps": "5",
    }


def _submit_async(client: TestClient, body: Json, path: str = "/api/v1/backtests") -> str:
    """Queue one job with ``?async=true`` and return its id."""
    accepted = client.post(f"{path}?async=true", json=body)
    assert accepted.status_code == 202, accepted.text
    return str(accepted.json()["job_id"])


def _job(client: TestClient, job_id: str) -> Json:
    """Read one research job through the API."""
    response = client.get(f"/api/v1/research/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _finished(client: TestClient, job_id: str, *, timeout: float, workdir: Path) -> Json:
    """Wait until one job is terminal and return it."""

    def probe() -> Json | None:
        record = _job(client, job_id)
        return record if record["status"] in _TERMINAL else None

    try:
        return wait_until(probe, timeout=timeout, interval=0.2, message=f"job {job_id}")
    except AssertionError as error:
        raise AssertionError(f"{error}\n{worker_log(workdir)[-4000:]}") from None


def test_pool_runs_jobs_in_parallel_but_never_beyond_the_worker_count(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """Four heavy backtests on two workers: two run at once, never three, all complete."""
    strategy_id = _seed(database, btc_definition)
    with _api(database, datasets.root) as client:
        ids = [
            _submit_async(client, _backtest(strategy_id, datasets.heavy, _HEAVY_BARS, slip))
            for slip in range(5, 9)
        ]
        peak = 0
        owners: set[str] = set()
        with research_worker_pool(database, datasets.root, tmp_path, workers=2):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                running = rows(
                    database, "SELECT lease_owner FROM research_jobs WHERE status = 'running'"
                )
                peak = max(peak, len(running))
                owners |= {str(row[0]) for row in running}
                statuses = {_job(client, job_id)["status"] for job_id in ids}
                if statuses <= _TERMINAL:
                    break
                time.sleep(0.05)
        records = [_job(client, job_id) for job_id in ids]
    assert [record["status"] for record in records] == ["completed"] * 4, worker_log(tmp_path)
    assert peak == 2
    assert len({owner.rsplit("/", 1)[0] for owner in owners}) == 2


def test_killed_worker_job_is_requeued_and_completes_on_the_next_attempt(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """SIGKILL a worker mid-job: its replacement re-queues and finishes the job."""
    strategy_id = _seed(database, btc_definition)
    with _api(database, datasets.root) as client:
        job_id = _submit_async(client, _backtest(strategy_id, datasets.heavy, _HEAVY_BARS))
        with research_worker_pool(database, datasets.root, tmp_path, workers=1):
            pid = wait_until(
                lambda: scalar(
                    database,
                    "SELECT pid FROM research_workers WHERE state = 'running' AND job_id = :job",
                    {"job": job_id},
                ),
                timeout=60,
                message="the worker to start the job",
            )
            os.kill(int(str(pid)), signal.SIGKILL)
            record = _finished(client, job_id, timeout=120, workdir=tmp_path)
    assert record["status"] == "completed", record
    assert record["attempts"] == 2
    assert "research_worker_crashed" in worker_log(tmp_path)


def test_dead_containers_job_is_requeued_after_its_lease_expires(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """A running row held by a worker that no longer exists runs again once its lease lapses."""
    strategy_id = _seed(database, btc_definition)
    with _api(database, datasets.root) as client:
        job_id = _submit_async(client, _backtest(strategy_id, datasets.heavy, _LIGHT_BARS))
        assert (
            execute(
                database,
                "UPDATE research_jobs SET status = 'running', attempts = 1, "
                "lease_owner = 'gone-host/1/0/dead', "
                "lease_expires_at = now() + interval '2 seconds' WHERE job_id = :job",
                {"job": job_id},
            )
            == 1
        )
        with research_worker_pool(database, datasets.root, tmp_path, workers=1):
            record = _finished(client, job_id, timeout=90, workdir=tmp_path)
    assert record["status"] == "completed", record
    assert record["attempts"] == 2


def test_cancel_stops_queued_and_running_jobs(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """A cancelled queued job never runs; a running study stops at its next window."""
    strategy_id = _seed(database, btc_definition)
    with _api(database, datasets.root) as client:
        queued = _submit_async(client, _backtest(strategy_id, datasets.heavy, _LIGHT_BARS))
        cancelled = client.post(f"/api/v1/research/jobs/{queued}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        study = _submit_async(
            client,
            _walk_forward(strategy_id, datasets.heavy, folds_end_hours=_HEAVY_BARS - 1, step=100),
            path="/api/v1/research/studies",
        )
        with research_worker_pool(database, datasets.root, tmp_path, workers=1):
            wait_until(
                lambda: (
                    True
                    if _job(client, study)["status"] == "running"
                    and int(str(_job(client, study)["progress_current"])) >= 1
                    else None
                ),
                timeout=90,
                message="the study to make progress",
            )
            assert client.post(f"/api/v1/research/jobs/{study}/cancel").status_code == 200
            record = _finished(client, study, timeout=120, workdir=tmp_path)
            never_ran = _job(client, queued)
    assert record["status"] == "cancelled", record
    assert int(str(record["progress_current"])) < int(str(record["progress_total"]))
    assert never_ran["status"] == "cancelled"
    assert never_ran["attempts"] == 0


def test_worker_results_are_bit_identical_to_the_in_process_harness(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """The same sync backtest and study answer byte-identical bodies on either path."""
    strategy_id = _seed(database, btc_definition)
    backtest = _backtest(strategy_id, datasets.heavy, _LIGHT_BARS)
    study = _walk_forward(strategy_id, datasets.heavy, folds_end_hours=_LIGHT_BARS + 400, step=200)
    with _api(database, datasets.root, mode=ResearchExecutionMode.IN_PROCESS) as harness:
        harness_backtest = harness.post("/api/v1/backtests", json=backtest)
        harness_study = harness.post("/api/v1/research/studies", json=study)
    assert harness_backtest.status_code == 201, harness_backtest.text
    assert harness_study.status_code == 201, harness_study.text
    study_fingerprint = harness_study.json()["study_fingerprint"]
    # Drop the persisted study so the worker recomputes it instead of reusing the plan.
    assert (
        execute(
            database,
            "DELETE FROM published_research_studies WHERE study_fingerprint = :fp",
            {"fp": study_fingerprint},
        )
        == 1
    )
    with (
        _api(database, datasets.root) as client,
        research_worker_pool(database, datasets.root, tmp_path, workers=1),
    ):
        worker_backtest = client.post("/api/v1/backtests", json=backtest)
        worker_study = client.post("/api/v1/research/studies", json=study)
    assert worker_backtest.status_code == 201, worker_log(tmp_path)[-3000:]
    assert worker_study.status_code == 201, worker_log(tmp_path)[-3000:]
    assert worker_backtest.content == harness_backtest.content
    assert worker_study.content == harness_study.content
    executed = scalar(database, "SELECT sum(jobs_completed) FROM research_workers")
    assert int(str(executed)) == 2
    diagnostics = rows(
        database,
        "SELECT diagnostics_json FROM published_backtest_results WHERE result_fingerprint = :fp",
        {"fp": worker_backtest.json()["result_fingerprint"]},
    )
    assert diagnostics and diagnostics[0][0] is not None


def test_worker_writes_the_same_diagnostics_as_the_harness(
    template: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """Separate databases: the worker stores the same diagnostics_json the harness does."""
    stored: list[object] = []
    for mode in (ResearchExecutionMode.IN_PROCESS, None):
        with cloned_database(template) as url:
            strategy_id = _seed(url, btc_definition)
            body = _backtest(strategy_id, datasets.heavy, _LIGHT_BARS)
            with _api(url, datasets.root, mode=mode) as client:
                if mode is None:
                    with research_worker_pool(url, datasets.root, tmp_path / "pool", workers=1):
                        response = client.post("/api/v1/backtests", json=body)
                else:
                    response = client.post("/api/v1/backtests", json=body)
            assert response.status_code == 201, response.text
            stored.append(
                scalar(
                    url,
                    "SELECT diagnostics_json FROM published_backtest_results "
                    "WHERE result_fingerprint = :fp",
                    {"fp": response.json()["result_fingerprint"]},
                )
            )
    assert stored[0] is not None
    assert stored[0] == stored[1]


def test_portfolio_backtest_in_the_worker_matches_the_in_process_runner(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """Two jobs of one plan, one per path, store the same portfolio result fingerprint."""
    eth_definition = create_template_strategy(product_id="ETH-USDC", timeframe="4h")

    async def plan_two_jobs() -> tuple[UUID, UUID, UUID]:
        engine = create_engine(SecretStr(database))
        store = PostgresPortfolioStore(engine)
        strategies = PostgresStrategyStore(engine)
        try:
            btc = await create_strategy_from_definition(strategies, btc_definition)
            eth = await create_strategy_from_definition(strategies, eth_definition)
            created = await store.create(
                PortfolioCreateRequest(
                    name="Pool", mode="paper", capital_quote="1000", cash_reserve_fraction="0.2"
                ),
                context=_CONTEXT,
            )
            pid = created.portfolio.portfolio_id
            current = await store.add_sleeve(
                pid,
                SleeveAddRequest(revision=1, strategy_id=btc.strategy_id, weight_fraction="0.5"),
                context=_CONTEXT,
            )
            current = await store.add_sleeve(
                pid,
                SleeveAddRequest(revision=2, strategy_id=eth.strategy_id, weight_fraction="0.3"),
                context=_CONTEXT,
            )
            plan = await plan_portfolio_backtest(
                current,
                PortfolioBacktestRequest(
                    maker_fee_rate="0.004", taker_fee_rate="0.006", fixed_slippage_bps="5"
                ),
                strategies=strategies,
                datasets=DatasetStore(datasets.root),
            )
            # Keep queue admission current independently of historical simulation timestamps.
            job_context = replace(_CONTEXT, occurred_at=datetime.now(UTC))
            first = await store.create_job(plan, context=job_context)
            assert await store.claim_next() == first.job_id
            second = await store.create_job(plan, context=job_context)
            dataset_store = DatasetStore(datasets.root)
            await PortfolioBacktestRunner(
                store=store,
                submitter=PostgresBacktestSubmitter(engine, dataset_store),
                results=PostgresBacktestResultStore(
                    engine,
                    research_run_store=PostgresResearchRunStore(engine),
                    dataset_store=dataset_store,
                ),
                datasets=dataset_store,
            ).run_job(first.job_id)
            return pid, first.job_id, second.job_id
        finally:
            await dispose(engine)

    _pid, first, second = asyncio.run(plan_two_jobs())
    with research_worker_pool(database, datasets.root, tmp_path, workers=1):
        wait_until(
            lambda: scalar(
                database,
                "SELECT status FROM portfolio_backtest_jobs WHERE job_id = :job AND "
                "status IN ('completed', 'failed')",
                {"job": str(second)},
            ),
            timeout=120,
            message="the worker to finish the portfolio backtest",
        )
    results = {
        str(row[0]): row[1]
        for row in rows(
            database,
            "SELECT job_id::text, result_fingerprint FROM portfolio_backtest_jobs "
            "WHERE status = 'completed'",
        )
    }
    assert results[str(first)] is not None
    assert results[str(second)] == results[str(first)], worker_log(tmp_path)[-3000:]


def test_api_stays_responsive_while_research_runs(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """Regression for the 46 s round trip: cheap calls stay fast and the API stays idle.

    With research inline, the API process burned at least a core for the whole batch.
    Now the batch runs in worker processes: health answers in milliseconds and the API
    process uses a small fraction of the wall time.
    """
    strategy_id = _seed(database, btc_definition)
    with _api(database, datasets.root) as client:
        ids = [
            _submit_async(client, _backtest(strategy_id, datasets.heavy, _HEAVY_BARS, slip))
            for slip in range(5, 9)
        ]
        with research_worker_pool(database, datasets.root, tmp_path, workers=2):
            wait_until(
                lambda: True if _job(client, ids[0])["status"] == "running" else None,
                timeout=60,
                message="research to start",
            )
            latencies: list[float] = []
            cpu_start = time.process_time()
            wall_start = time.monotonic()
            while any(_job(client, job_id)["status"] not in _TERMINAL for job_id in ids):
                started = time.perf_counter()
                assert client.get("/health/live").status_code == 200
                latencies.append(time.perf_counter() - started)
                time.sleep(0.2)
                assert time.monotonic() - wall_start < 180, worker_log(tmp_path)[-3000:]
            cpu = time.process_time() - cpu_start
            wall = time.monotonic() - wall_start
        statuses = [_job(client, job_id)["status"] for job_id in ids]
    assert statuses == ["completed"] * 4
    assert len(latencies) >= 5
    assert max(latencies) < 1.0, latencies
    assert statistics.median(latencies) < 0.1, latencies
    assert cpu < 0.5 * wall, (cpu, wall)


def test_operator_health_reports_workers_queue_depth_and_rss(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """Health shows configured and live workers, per-worker RSS, and queue depth."""
    strategy_id = _seed(database, btc_definition)
    with _api(database, datasets.root) as client:
        before = client.get("/api/v1/operator/health").json()
        assert before["payload"]["research_workers"]["configured_workers"] is None
        missing = next(item for item in before["components"] if item["name"] == "research_worker")
        assert missing["reason_code"] == "RESEARCH_WORKER_MISSING"
        for slip in range(5, 8):
            _submit_async(client, _backtest(strategy_id, datasets.heavy, _HEAVY_BARS, slip))
        queued = client.get("/api/v1/operator/health").json()["payload"]["research_workers"]
        assert queued["queue"]["queued"] == 3
        assert queued["queue"]["oldest_queued_age_seconds"] is not None
        with research_worker_pool(database, datasets.root, tmp_path, workers=2):

            def live() -> Json | None:
                report = client.get("/api/v1/operator/health").json()
                block = report["payload"]["research_workers"]
                return report if block["live_workers"] == 2 and block["queue"]["running"] else None

            report = wait_until(live, timeout=90, message="two live workers")
    block = report["payload"]["research_workers"]
    component = next(item for item in report["components"] if item["name"] == "research_worker")
    assert component["reason_code"] == "READY", component
    assert block["configured_workers"] == 2
    assert {worker["slot"] for worker in block["workers"]} == {0, 1}
    assert all(int(worker["rss_bytes"]) > 20 * 2**20 for worker in block["workers"])
    assert block["queue"]["queued"] + block["queue"]["running"] <= 3


def test_worker_recycles_its_process_after_max_jobs(
    database: str, datasets: Datasets, btc_definition: StrategyDefinition, tmp_path: Path
) -> None:
    """With max_jobs=1 every job runs in a fresh process; both still complete."""
    strategy_id = _seed(database, btc_definition)
    with _api(database, datasets.root) as client:
        ids = [
            _submit_async(client, _backtest(strategy_id, datasets.heavy, _LIGHT_BARS, slip))
            for slip in (5, 6)
        ]
        with research_worker_pool(database, datasets.root, tmp_path, workers=1, max_jobs=1):
            for job_id in ids:
                _finished(client, job_id, timeout=90, workdir=tmp_path)
            wait_until(
                lambda: (
                    True if worker_log(tmp_path).count("research_worker_recycled") >= 2 else None
                ),
                timeout=30,
                message="two planned recycles",
            )
        statuses = [_job(client, job_id)["status"] for job_id in ids]
    log = worker_log(tmp_path)
    assert statuses == ["completed", "completed"]
    assert log.count("research_worker_recycling") >= 2
    assert "spawned=3" in log
