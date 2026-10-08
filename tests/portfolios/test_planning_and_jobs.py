"""Portfolio backtest planning (snapshots, datasets, common window) and the job runner."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tests.portfolios.fixtures import (
    FakeChildBacktests,
    datasets_for_two_sleeves,
    strategy,
    write_dataset,
)
from thytrader.backtest.submission import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
    resolve_backtest_window,
)
from thytrader.market_data.datasets import DatasetStore
from thytrader.portfolios.backtest import PortfolioBacktestRequest
from thytrader.portfolios.errors import PortfolioRevisionConflictError
from thytrader.portfolios.jobs import PortfolioBacktestRunner
from thytrader.portfolios.models import MutationContext, PortfolioCreateRequest, SleeveAddRequest
from thytrader.portfolios.planning import PortfolioBacktestRejectedError, plan_portfolio_backtest
from thytrader.portfolios.store import InMemoryPortfolioStore
from thytrader.research.jobs import ResearchJobStatus
from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.portfolios.models import PortfolioAggregate

_CONTEXT = MutationContext(
    actor="operator", channel="api", occurred_at=datetime(2026, 10, 2, 12, tzinfo=UTC)
)
_COSTS = {"maker_fee_rate": "0.004", "taker_fee_rate": "0.006", "fixed_slippage_bps": "5"}


def _portfolio(
    strategies: InMemoryStrategyStore, store: InMemoryPortfolioStore
) -> PortfolioAggregate:
    """A 1000 USDC portfolio with BTC 1h (50%) and ETH 4h (30%) sleeves."""
    btc = strategy(strategies, "BTC-USDC", "1h")
    eth = strategy(strategies, "ETH-USDC", "4h")

    async def build() -> PortfolioAggregate:
        current = await store.create(
            PortfolioCreateRequest(
                name="Core", mode="paper", capital_quote="1000", cash_reserve_fraction="0.2"
            ),
            context=_CONTEXT,
        )
        for record, weight in ((btc, "0.5"), (eth, "0.3")):
            current = await store.add_sleeve(
                current.portfolio.portfolio_id,
                SleeveAddRequest(
                    revision=current.portfolio.revision,
                    strategy_id=record.strategy_id,
                    weight_fraction=weight,
                ),
                context=_CONTEXT,
            )
        return current

    return asyncio.run(build())


def test_plan_binds_latest_datasets_and_aligns_the_common_window(tmp_path: Path) -> None:
    """The window is the intersection of usable coverage, aligned to the 4h clock."""
    strategies = InMemoryStrategyStore()
    store = InMemoryPortfolioStore(strategies=strategies)
    current = _portfolio(strategies, store)
    btc_dataset, eth_dataset = datasets_for_two_sleeves(tmp_path)
    datasets = DatasetStore(tmp_path)
    plan = asyncio.run(
        plan_portfolio_backtest(
            current,
            PortfolioBacktestRequest.model_validate({**_COSTS, "revision": 3}),
            strategies=strategies,
            datasets=datasets,
        )
    )
    assert [sleeve.submission.dataset_fingerprint for sleeve in plan.sleeves] == [
        btc_dataset,
        eth_dataset,
    ]
    assert [sleeve.capital_quote for sleeve in plan.sleeves] == ["500", "300"]
    windows = []
    for sleeve in plan.sleeves:
        undated = sleeve.submission.model_copy(
            update={"evaluation_start": None, "evaluation_end": None}
        )
        snapshot = asyncio.run(strategies.snapshot(sleeve.strategy_id))
        windows.append(resolve_backtest_window(undated, snapshot, datasets))
    start = max(window[0] for window in windows)
    end = min(window[1] for window in windows)
    assert plan.evaluation_start >= start
    assert plan.evaluation_end <= end
    assert plan.evaluation_start - start < timedelta(hours=4)
    assert end - plan.evaluation_end < timedelta(hours=4)
    assert int(plan.evaluation_start.timestamp()) % 14_400 == 0
    assert int(plan.evaluation_end.timestamp()) % 14_400 == 0
    assert all(
        (sleeve.submission.evaluation_start, sleeve.submission.evaluation_end)
        == (plan.evaluation_start, plan.evaluation_end)
        for sleeve in plan.sleeves
    )


def test_plan_rejects_missing_datasets_stale_revisions_and_bad_windows(tmp_path: Path) -> None:
    """Missing clocks name the sleeve; a stale revision conflicts; an unfit window is refused."""
    strategies = InMemoryStrategyStore()
    store = InMemoryPortfolioStore(strategies=strategies)
    current = _portfolio(strategies, store)
    write_dataset(tmp_path, "BTC-USDC", "1h", start=datetime(2026, 7, 1, tzinfo=UTC), count=400)
    datasets = DatasetStore(tmp_path)
    request = PortfolioBacktestRequest.model_validate(_COSTS)
    with pytest.raises(PortfolioBacktestRejectedError) as raised:
        asyncio.run(
            plan_portfolio_backtest(current, request, strategies=strategies, datasets=datasets)
        )
    problems = raised.value.problems
    assert [(problem.code, problem.strategy_name) for problem in problems] == [
        ("dataset_missing", current.sleeves[1].strategy.name)
    ]
    assert "ETH-USDC 4h (decision clock)" in problems[0].message
    with pytest.raises(PortfolioRevisionConflictError):
        asyncio.run(
            plan_portfolio_backtest(
                current,
                PortfolioBacktestRequest.model_validate({**_COSTS, "revision": 1}),
                strategies=strategies,
                datasets=datasets,
            )
        )
    datasets_for_two_sleeves(tmp_path)
    early = PortfolioBacktestRequest.model_validate(
        {
            **_COSTS,
            "evaluation_start": "2026-07-01T00:00:00Z",
            "evaluation_end": "2026-07-02T00:00:00Z",
        }
    )
    with pytest.raises(PortfolioBacktestRejectedError) as unfit:
        asyncio.run(
            plan_portfolio_backtest(current, early, strategies=strategies, datasets=datasets)
        )
    assert unfit.value.problems[0].code == "window_rejected"


def test_plan_needs_sleeves_and_rejects_the_retired_engine_selector(tmp_path: Path) -> None:
    """An empty portfolio cannot be backtested; engine selection stays rejected."""
    strategies = InMemoryStrategyStore()
    store = InMemoryPortfolioStore(strategies=strategies)
    empty = asyncio.run(
        store.create(
            PortfolioCreateRequest(name="Empty", mode="paper", capital_quote="100"),
            context=_CONTEXT,
        )
    )
    with pytest.raises(PortfolioBacktestRejectedError, match="at least one sleeve"):
        asyncio.run(
            plan_portfolio_backtest(
                empty,
                PortfolioBacktestRequest.model_validate(_COSTS),
                strategies=strategies,
                datasets=DatasetStore(tmp_path),
            )
        )
    with pytest.raises(ValueError, match="one backtest model"):
        PortfolioBacktestRequest.model_validate({**_COSTS, "engine_contract_version": "v1"})


def test_runner_runs_every_sleeve_combines_and_journals(tmp_path: Path) -> None:
    """A queued job runs each child, stores the result, and appends backtest_run."""
    strategies = InMemoryStrategyStore()
    store = InMemoryPortfolioStore(strategies=strategies)
    current = _portfolio(strategies, store)
    datasets_for_two_sleeves(tmp_path)
    datasets = DatasetStore(tmp_path)

    async def scenario() -> None:
        plan = await plan_portfolio_backtest(
            current,
            PortfolioBacktestRequest.model_validate(_COSTS),
            strategies=strategies,
            datasets=datasets,
        )
        children = FakeChildBacktests(
            {sleeve.strategy_fingerprint: sleeve.timeframe for sleeve in plan.sleeves}
        )
        runner = PortfolioBacktestRunner(
            store=store, submitter=children, results=children, datasets=datasets
        )
        job = await store.create_job(plan, context=_CONTEXT)
        claimed = await store.claim_next()
        assert claimed == job.job_id
        await runner.run_job(job.job_id)
        portfolio_id = current.portfolio.portfolio_id
        finished = await store.get_job(portfolio_id, job.job_id)
        assert finished is not None
        assert finished.status is ResearchJobStatus.COMPLETED
        assert finished.progress_current == finished.progress_total == 3
        assert [request.initial_quote_balance for request in children.requests] == ["500", "300"]
        result = await store.load_result(portfolio_id, str(finished.result_fingerprint))
        assert result.summary.total_return_fraction == "0"
        assert result.summary.idle_capital_fraction == "1"
        assert [leg.product_id for leg in result.basket.legs] == ["BTC-USDC", "ETH-USDC"]
        listing = await store.list_results(portfolio_id, limit=5, offset=0)
        assert [row.result_fingerprint for row in listing] == [finished.result_fingerprint]
        journal = await store.journal(portfolio_id, limit=1, offset=0)
        entry = journal.entries[0]
        assert entry.kind == "backtest_run"
        assert entry.detail.result_fingerprint == finished.result_fingerprint
        assert entry.summary.startswith("Portfolio backtest: 0.0% net, 0.0% max drawdown")

    asyncio.run(scenario())


def test_runner_records_child_rejections_as_failed_jobs(tmp_path: Path) -> None:
    """A rejected child backtest fails the job with its message."""
    strategies = InMemoryStrategyStore()
    store = InMemoryPortfolioStore(strategies=strategies)
    current = _portfolio(strategies, store)
    datasets_for_two_sleeves(tmp_path)
    datasets = DatasetStore(tmp_path)

    class Rejecting(FakeChildBacktests):
        """Refuse every child."""

        async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
            """Reject like a dataset mismatch would."""
            del request
            raise BacktestSubmissionRejectedError("The selected dataset was not found.")

    async def scenario() -> None:
        plan = await plan_portfolio_backtest(
            current,
            PortfolioBacktestRequest.model_validate(_COSTS),
            strategies=strategies,
            datasets=datasets,
        )
        children = Rejecting({})
        runner = PortfolioBacktestRunner(
            store=store, submitter=children, results=children, datasets=datasets
        )
        job = await store.create_job(plan, context=_CONTEXT)
        await store.claim_next()
        await runner.run_job(job.job_id)
        failed = await store.get_job(current.portfolio.portfolio_id, job.job_id)
        assert failed is not None
        assert failed.status is ResearchJobStatus.FAILED
        assert failed.error_message == "The selected dataset was not found."

    asyncio.run(scenario())
