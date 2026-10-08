"""Per-cycle portfolio supervision and portfolio limits in the closed-bar loop (ADR 0091)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tests.execution.decision_support import Catalog, candles, journaled_bar, strategy
from tests.operator_diagnostics.test_projection_observability import _order
from tests.portfolios.runtime_support import World, operator, portfolio, world
from thytrader.exchanges.models import ExchangeBalance
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import DecisionOutcome
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import PortfolioSleeveStart, create_deployment
from thytrader.execution_worker.portfolio_supervisor import supervise_portfolios
from thytrader.execution_worker.service import _run_cycle
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.portfolios.models import PortfolioLimits, SetWeightsRequest
from thytrader.portfolios.store import DisabledPortfolioStore
from thytrader.risk.gate import PortfolioRiskBook
from thytrader.risk.models import RiskReasonCode
from thytrader.risk.portfolio_scope import portfolio_risk_scope
from thytrader.strategies.models import strategy_fingerprint
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    LifecycleCommand,
    OrderStatus,
)

if TYPE_CHECKING:
    from thytrader.portfolios.models import PortfolioAggregate
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot

pytestmark = pytest.mark.anyio
_LOOP_PORTFOLIO = UUID("01978a3e-5f2c-7d10-b3a4-00000000f00a")


async def _supervise(state: World) -> dict[UUID, PortfolioRiskBook]:
    """Run one supervision pass like the worker's cycle does."""
    return await supervise_portfolios(
        store=state.execution,
        portfolios=state.portfolios,
        deployments=await state.execution.list_deployments(),
        now=datetime.now(UTC),
    )


async def _running(state: World, limits: PortfolioLimits) -> PortfolioAggregate:
    """A started paper portfolio (1,000 capital, sleeves at 50% and 30%)."""
    current, _ = await portfolio(state, limits=limits)
    await state.runtime.start(
        current.portfolio.portfolio_id, revision=current.portfolio.revision, context=operator()
    )
    return current


async def test_the_drawdown_stop_pauses_every_sleeve_and_latches() -> None:
    """Equity 25% below the run's peak trips the 20% stop: latched, paused, journaled."""
    state = world()
    current = await _running(state, PortfolioLimits(max_drawdown_fraction="0.2"))
    pid = current.portfolio.portfolio_id
    books = await state.tagged(pid)
    await _supervise(state)
    await state.mark_equity(books[0].id, pnl="-150")
    await state.mark_equity(books[1].id, pnl="-100")
    gate = await _supervise(state)
    assert gate[pid].breaker_reason is RiskReasonCode.PORTFOLIO_DRAWDOWN_STOP
    runtime = await state.portfolios.runtime_state(pid)
    assert runtime.breaker_reason == "PORTFOLIO_DRAWDOWN_STOP"
    assert runtime.last_equity == Decimal("750")
    paused = await state.tagged(pid)
    assert {book.status for book in paused} == {DeploymentStatus.PAUSED}
    assert {book.lifecycle_command for book in paused} == {LifecycleCommand.STOP_NEW_ENTRIES}
    assert all(str(book.mismatch_detail).startswith("PORTFOLIO_DRAWDOWN_STOP:") for book in paused)
    entry = (await state.portfolios.journal(pid, limit=1, offset=0)).entries[0]
    assert (entry.kind, entry.actor, entry.detail.reason_code) == (
        "breaker_tripped",
        "system",
        "PORTFOLIO_DRAWDOWN_STOP",
    )
    assert set(entry.detail.deployment_ids) == {book.id for book in books}


async def test_the_daily_loss_stop_latches_and_replays_after_a_back_door_resume() -> None:
    """A sleeve resumed through its bot page is paused again while the latch holds."""
    state = world()
    current = await _running(state, PortfolioLimits(daily_loss_quote="40"))
    pid = current.portfolio.portfolio_id
    books = await state.tagged(pid)
    await _supervise(state)
    await state.mark_equity(books[0].id, pnl="-45")
    await _supervise(state)
    assert (await state.portfolios.runtime_state(pid)).breaker_reason == "PORTFOLIO_DAILY_LOSS_STOP"
    first = (await state.execution.get_deployment(books[0].id)).deployment
    await state.execution.save_deployment(
        replace(first, status=DeploymentStatus.RUNNING, lifecycle_command=LifecycleCommand.NONE)
    )
    await _supervise(state)
    again = (await state.execution.get_deployment(books[0].id)).deployment
    assert again.status is DeploymentStatus.PAUSED
    assert str(again.mismatch_detail).startswith("PORTFOLIO_DAILY_LOSS_STOP:")
    trips = [
        entry
        for entry in (await state.portfolios.journal(pid, limit=20, offset=0)).entries
        if entry.kind == "breaker_tripped"
    ]
    assert len(trips) == 1


async def test_limits_within_bounds_leave_the_sleeves_running() -> None:
    """A loss inside both stops records equity and changes nothing else."""
    state = world()
    current = await _running(
        state, PortfolioLimits(daily_loss_quote="100", max_drawdown_fraction="0.2")
    )
    pid = current.portfolio.portfolio_id
    books = await state.tagged(pid)
    await _supervise(state)
    await state.mark_equity(books[0].id, pnl="-30")
    gate = await _supervise(state)
    assert gate[pid].breaker_reason is None
    runtime = await state.portfolios.runtime_state(pid)
    assert (runtime.last_equity, runtime.day_open_equity) == (Decimal("970"), Decimal("1000"))
    assert {book.status for book in await state.tagged(pid)} == {DeploymentStatus.RUNNING}


async def _unresolve_equity(state: World, deployment_id: UUID) -> None:
    """A FILLED order with no published fills, and the null equity refresh_performance stamps."""
    deployment = (await state.execution.get_deployment(deployment_id)).deployment
    await _order(
        state.execution,
        deployment,
        product="BTC-USDC",
        status=OrderStatus.FILLED,
        filled="1",
        created_at=deployment.created_at + timedelta(seconds=1),
    )
    current = (await state.execution.get_deployment(deployment_id)).deployment
    await state.execution.save_deployment(replace(current, performance_equity=None))


async def test_an_unresolved_profitable_sleeve_does_not_trip_a_false_drawdown() -> None:
    """Unknown equity is not zero PnL: losing sight of a +50 gain must not read as a loss."""
    state = world()
    current = await _running(state, PortfolioLimits(max_drawdown_fraction="0.03"))
    pid = current.portfolio.portfolio_id
    books = await state.tagged(pid)
    await state.mark_equity(books[0].id, pnl="50")
    await _supervise(state)
    recorded = await state.portfolios.runtime_state(pid)
    assert recorded.high_water_mark_equity == Decimal("1050")
    await _unresolve_equity(state, books[0].id)
    gate = await _supervise(state)
    assert gate[pid].breaker_reason is None
    held = await state.portfolios.runtime_state(pid)
    assert (held.last_equity, held.high_water_mark_equity, held.day_open_equity) == (
        recorded.last_equity,
        recorded.high_water_mark_equity,
        recorded.day_open_equity,
    )
    assert {book.status for book in await state.tagged(pid)} == {DeploymentStatus.RUNNING}


async def test_an_unresolved_losing_sleeve_does_not_raise_the_high_water_mark() -> None:
    """Hiding a loss must not lift the peak and arm a false drawdown once the sleeve resolves."""
    state = world()
    current = await _running(state, PortfolioLimits(max_drawdown_fraction="0.2"))
    pid = current.portfolio.portfolio_id
    books = await state.tagged(pid)
    await _supervise(state)
    await state.mark_equity(books[0].id, pnl="-60")
    await state.mark_equity(books[1].id, pnl="40")
    await _supervise(state)
    await _unresolve_equity(state, books[0].id)
    await _supervise(state)
    held = await state.portfolios.runtime_state(pid)
    assert (held.last_equity, held.high_water_mark_equity) == (Decimal("980"), Decimal("1000"))


async def test_a_latched_breaker_still_pauses_sleeves_while_equity_is_unresolved() -> None:
    """Holding the baselines never releases an existing latch."""
    state = world()
    current = await _running(state, PortfolioLimits(daily_loss_quote="40"))
    pid = current.portfolio.portfolio_id
    books = await state.tagged(pid)
    await _supervise(state)
    await state.mark_equity(books[0].id, pnl="-45")
    await _supervise(state)
    await _unresolve_equity(state, books[0].id)
    first = (await state.execution.get_deployment(books[1].id)).deployment
    await state.execution.save_deployment(
        replace(first, status=DeploymentStatus.RUNNING, lifecycle_command=LifecycleCommand.NONE)
    )
    gate = await _supervise(state)
    assert gate[pid].breaker_reason is RiskReasonCode.PORTFOLIO_DAILY_LOSS_STOP
    again = (await state.execution.get_deployment(books[1].id)).deployment
    assert again.status is DeploymentStatus.PAUSED


async def test_supervision_keeps_each_sleeve_allocation_at_weight_times_capital() -> None:
    """A rebalance on a running portfolio binds each sleeve's allocated capital next cycle."""
    state = world()
    current = await _running(state, PortfolioLimits())
    pid = current.portfolio.portfolio_id
    request = SetWeightsRequest.model_validate(
        {
            "revision": current.portfolio.revision,
            "weights": [
                {"sleeve_id": str(current.sleeves[0].sleeve.sleeve_id), "weight_fraction": "0.3"},
                {"sleeve_id": str(current.sleeves[1].sleeve.sleeve_id), "weight_fraction": "0.5"},
            ],
        }
    )
    await state.portfolios.set_weights(pid, request, context=operator())
    await _supervise(state)
    assert [book.allocated_capital for book in await state.tagged(pid)] == [
        Decimal("300"),
        Decimal("500"),
    ]


async def test_an_unreadable_portfolio_store_yields_no_books() -> None:
    """Supervision degrades to no books; the gate then fails closed for tagged sleeves."""
    state = world()
    await _running(state, PortfolioLimits())
    books = await supervise_portfolios(
        store=state.execution,
        portfolios=DisabledPortfolioStore(),
        deployments=await state.execution.list_deployments(),
        now=datetime.now(UTC),
    )
    assert books == {}


async def _tagged_paper_book(
    portfolio_id: UUID,
) -> tuple[InMemoryExecutionStore, StrategyDefinition, DeploymentSnapshot]:
    """One running paper sleeve book of a 10,000-capital portfolio on the RSI strategy."""
    definition = strategy()
    store = InMemoryExecutionStore()
    created = await create_deployment(
        store=store,
        publication_store=Catalog(definition),
        strategy_fingerprint=strategy_fingerprint(definition),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
        portfolio_sleeve=PortfolioSleeveStart(
            portfolio_id=portfolio_id, allocated_capital=Decimal("10000")
        ),
    )
    return store, definition, await store.get_deployment(created.id)


@pytest.mark.parametrize(
    ("book", "reason"),
    [
        (
            PortfolioRiskBook(
                portfolio_id=_LOOP_PORTFOLIO,
                capital=Decimal("10000"),
                max_total_exposure_fraction=Decimal("0.0001"),
                max_per_asset_fraction=Decimal("1"),
            ),
            "PORTFOLIO_TOTAL_EXPOSURE_LIMIT",
        ),
        (None, "PORTFOLIO_LIMITS_UNAVAILABLE"),
    ],
)
async def test_the_loop_blocks_a_sleeve_entry_with_the_portfolio_reason_code(
    book: PortfolioRiskBook | None, reason: str
) -> None:
    """A matched entry refused by the portfolio gate lands in the decision timeline."""
    store, definition, snapshot = await _tagged_paper_book(_LOOP_PORTFOLIO)
    journal = InMemoryDecisionJournalStore()
    with portfolio_risk_scope(book):
        after, decision = await journaled_bar(
            snapshot,
            definition=definition,
            window=candles(60),
            store=store,
            journal=journal,
        )
    assert decision.outcome is DecisionOutcome.ENTRY_BLOCKED
    assert decision.reason_code == reason
    assert decision.risk is not None
    assert (decision.risk.decision, decision.risk.reason_code) == ("deny", reason)
    assert after.orders == ()


async def test_the_worker_cycle_supervises_portfolios_before_processing_books() -> None:
    """One real worker cycle syncs sleeve allocations from the portfolio's weights."""
    state = world()
    current = await _running(state, PortfolioLimits())
    pid = current.portfolio.portfolio_id
    request = SetWeightsRequest.model_validate(
        {
            "revision": current.portfolio.revision,
            "weights": [
                {"sleeve_id": str(current.sleeves[0].sleeve.sleeve_id), "weight_fraction": "0.2"},
                {"sleeve_id": str(current.sleeves[1].sleeve.sleeve_id), "weight_fraction": "0.6"},
            ],
        }
    )
    await state.portfolios.set_weights(pid, request, context=operator())
    await _run_cycle(
        store=state.execution,
        publication_store=state.strategies,
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=None,
        quote_reader=None,
        risk_store=state.risk,
        portfolio_store=state.portfolios,
    )
    assert [book.allocated_capital for book in await state.tagged(pid)] == [
        Decimal("200"),
        Decimal("600"),
    ]
    runtime = await state.portfolios.runtime_state(pid)
    assert runtime.last_equity == Decimal("1000")


class _QuoteBalances:
    """A fake venue quote reader: 5,000 USDC available."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return one quote balance."""
        return (
            ExchangeBalance(
                currency="USDC", name="USD Coin", available=Decimal("5000"), hold=Decimal(0)
            ),
        )


async def test_a_live_portfolio_runs_and_stops_through_worker_cycles_with_a_fake_broker() -> None:
    """Live sleeves keep weight x capital as allocation through venue refresh and flatten."""
    state = world()
    await state.publish_policy()
    current, _ = await portfolio(state, mode="live")
    pid = current.portfolio.portfolio_id
    await state.runtime.start(
        pid, revision=current.portfolio.revision, context=operator(), live_acknowledged=True
    )

    async def cycle() -> None:
        await _run_cycle(
            store=state.execution,
            publication_store=state.strategies,
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=PaperBroker(),
            quote_reader=_QuoteBalances(),
            risk_store=state.risk,
            portfolio_store=state.portfolios,
        )

    await cycle()
    books = await state.tagged(pid)
    assert {book.status for book in books} == {DeploymentStatus.RUNNING}
    assert [book.allocated_capital for book in books] == [Decimal("500"), Decimal("300")]
    assert all(book.mismatch_detail != "Live broker is unavailable." for book in books)
    await state.runtime.stop(pid, context=operator(), flatten=True)
    await cycle()
    stopped = await state.tagged(pid)
    assert {book.status for book in stopped} == {DeploymentStatus.STOPPED}
    assert {book.lifecycle_command for book in stopped} == {LifecycleCommand.FLATTEN}
    assert (await state.runtime.snapshot(pid)).books.state == "stopped"
