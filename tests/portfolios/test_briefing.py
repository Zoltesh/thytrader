"""The one-call manager briefing (ADR 0091)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.portfolios.fixtures import DATA_START, FakeChildBacktests, write_dataset
from tests.portfolios.runtime_support import World, operator, portfolio, world
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.market_data.datasets import DatasetStore
from thytrader.portfolios.backtest import PortfolioBacktestRequest
from thytrader.portfolios.briefing import BRIEFING_CONTRACT, build_manager_briefing
from thytrader.portfolios.jobs import PortfolioBacktestRunner
from thytrader.portfolios.models import ManagerPermissions
from thytrader.portfolios.planning import plan_portfolio_backtest
from thytrader.portfolios.proposals import ProposalSubmitRequest
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.portfolios.models import PortfolioAggregate

pytestmark = pytest.mark.anyio

_COSTS = {"maker_fee_rate": "0.004", "taker_fee_rate": "0.006", "fixed_slippage_bps": "5"}


async def _store_backtest(
    state: World, current: PortfolioAggregate, root: Path, *, drawdown: str
) -> str:
    """Run one portfolio backtest and store a copy whose sleeves drew down ``drawdown``."""
    write_dataset(root, "BTC-USDC", "1h", start=DATA_START, count=300)
    write_dataset(root, "ETH-USDC", "1h", start=DATA_START, count=300, base=50)
    datasets = DatasetStore(root)
    plan = await plan_portfolio_backtest(
        current,
        PortfolioBacktestRequest.model_validate(_COSTS),
        strategies=state.strategies,
        datasets=datasets,
    )
    children = FakeChildBacktests(
        {item.strategy_fingerprint: item.timeframe for item in plan.sleeves}
    )
    runner = PortfolioBacktestRunner(
        store=state.portfolios, submitter=children, results=children, datasets=datasets
    )
    job = await state.portfolios.create_job(plan, context=operator())
    await state.portfolios.claim_next()
    await runner.run_job(job.job_id)
    finished = await state.portfolios.get_job(current.portfolio.portfolio_id, job.job_id)
    assert finished is not None
    result = await state.portfolios.load_result(
        current.portfolio.portfolio_id, str(finished.result_fingerprint)
    )
    stressed = result.model_copy(
        update={
            "sleeves": tuple(
                item.model_copy(update={"maximum_drawdown_fraction": drawdown})
                for item in result.sleeves
            )
        }
    )
    second = await state.portfolios.create_job(plan, context=operator())
    await state.portfolios.claim_next()
    stored = await state.portfolios.complete(second.job_id, stressed)
    return str(stored.result_fingerprint)


async def test_the_briefing_carries_everything_a_manager_reasons_from(tmp_path: Path) -> None:
    """Permissions and budget, performance, sleeves vs evidence, decisions, proposals, journal."""
    state = world()
    current, records = await portfolio(
        state, permissions=ManagerPermissions(may_rebalance=True, max_weight_change_per_week="0.1")
    )
    pid = current.portfolio.portfolio_id
    latest = await _store_backtest(state, current, tmp_path, drawdown="0.08")
    await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    books = await state.tagged(pid)
    await state.mark_equity(books[0].id, pnl="-60")
    bar = datetime(2026, 10, 2, 11, tzinfo=UTC)
    await state.decisions.upsert(
        BarDecision(
            deployment_id=books[0].id,
            strategy_id=records[0].strategy_id,
            product_id="BTC-USDC",
            timeframe="1h",
            mode=DeploymentMode.PAPER,
            bar_starts_at=bar,
            bar_closes_at=bar + timedelta(hours=1),
            evaluated_at=bar + timedelta(hours=1),
            outcome=DecisionOutcome.NO_SIGNAL,
            reason_code="CONDITIONS_NOT_MET",
            summary="No trade: EMA(21) 101 needs ≥ EMA(55) 102",
        )
    )
    request = ProposalSubmitRequest.model_validate(
        {
            "revision": current.portfolio.revision,
            "change": {
                "kind": "rebalance",
                "weights": [
                    {
                        "sleeve_id": str(current.sleeves[0].sleeve.sleeve_id),
                        "weight_fraction": "0.46",
                    },
                    {
                        "sleeve_id": str(current.sleeves[1].sleeve.sleeve_id),
                        "weight_fraction": "0.34",
                    },
                ],
            },
            "rationale": "Shift 4% to the steadier sleeve.",
        }
    )
    await state.proposals.submit(pid, request, channel="api")
    snapshot = await state.runtime.snapshot(pid)
    briefing = await build_manager_briefing(
        snapshot, portfolios=state.portfolios, decisions=state.decisions
    )
    assert briefing.contract == BRIEFING_CONTRACT
    assert (briefing.mode, briefing.state) == ("paper", "running")
    assert briefing.permissions.rebalance_auto_applies is True
    assert briefing.permissions.order_authority is False
    assert (
        briefing.permissions.weight_moved_this_week,
        briefing.permissions.weight_budget_remaining,
    ) == (
        "0.04",
        "0.06",
    )
    assert briefing.performance.equity == "940"
    assert briefing.performance.net_pnl == "-60"
    assert briefing.latest_backtest is not None
    assert briefing.latest_backtest.result_fingerprint == latest
    first = briefing.sleeves[0]
    assert first.deployment is not None
    assert first.deployment.net_pnl == "-60"
    assert first.deployment.drawdown_fraction == "0.12"
    assert first.backtest is not None
    assert first.backtest.maximum_drawdown_fraction == "0.08"
    assert first.drawdown_vs_backtest == "1.5"
    (decision,) = first.recent_decisions
    assert decision.ref == f"{books[0].id}/BTC-USDC@2026-10-02T11:00:00Z"
    assert decision.reason_code == "CONDITIONS_NOT_MET"
    assert briefing.pending_proposals == ()
    assert [item.status for item in briefing.recent_proposals] == ["applied"]
    kinds = [entry.kind for entry in briefing.journal]
    assert kinds[:3] == ["weights_changed", "proposal_submitted", "deployment_started"]
    assert any("never places orders" in line for line in briefing.disclosures)
    assert briefing.revision == (await state.portfolios.get(pid)).portfolio.revision
    assert Decimal(briefing.exposure.cap_quote) == Decimal("1000")
