"""Zero-based live PnL, capital-normalized drawdown, and durable worker latches."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.execution.test_loop import _always_entry_strategy, _candles, _product
from thytrader.execution.capital import apply_venue_quote, refresh_performance
from thytrader.execution.ledger import ledger_from_snapshot
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.execution.paper import PaperBroker
from thytrader.execution.performance import current_drawdown, performance_capital
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_runtime_breakers
from thytrader.risk.models import RiskReasonCode, compiled_default_risk_policy
from thytrader.strategies.models import strategy_fingerprint

_NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


def _live_snapshot() -> DeploymentSnapshot:
    """A real-shaped 50-quote entry with its recorded half-quote fee and zero ledger origin."""
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=uuid4(),
        product_id="BTC-USD",
        timeframe="1h",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        phase=RuntimePhase.OPEN,
        cash=Decimal("-50.5"),
        allocated_capital=Decimal("100"),
        venue_available_quote=Decimal("1000"),
        initial_equity=Decimal("0"),
        baseline_equity=Decimal("0"),
        high_water_mark_equity=Decimal("0"),
        utc_day_open_equity=Decimal("0"),
        utc_day_open_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
    )
    order = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="entry",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        price=Decimal("50"),
        product_id="BTC-USD",
        status=OrderStatus.FILLED,
        created_at=_NOW,
        updated_at=_NOW,
    )
    fill = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=order.id,
        venue_fill_id="entry-fill",
        price=Decimal("50"),
        quantity=Decimal("1"),
        fee=Decimal("0.5"),
        filled_at=_NOW,
        economics_applied_at=_NOW,
    )
    position = Position(
        deployment_id=deployment.id,
        product_id="BTC-USD",
        quantity=Decimal("1"),
        entry_price=Decimal("50"),
        stop_price=Decimal("40"),
        target_price=Decimal("80"),
        entered_bar=_NOW,
        updated_at=_NOW,
    )
    return DeploymentSnapshot(deployment, orders=(order,), fills=(fill,), position=position)


def test_live_loss_reports_return_and_drawdown_without_changing_fill_accounting() -> None:
    """Losses before the first profit count against the allocation, including recorded fees."""
    snapshot = _live_snapshot()
    ledger = ledger_from_snapshot(snapshot, mark_price=Decimal("45"))
    assert ledger.cash == Decimal("-50.5")
    assert ledger.starting_cash == Decimal("0")
    assert ledger.equity == ledger.total_net_pnl == Decimal("-5.5")
    assert ledger.total_fees == Decimal("0.5")
    assert ledger.total_return_fraction == Decimal("-0.055")
    assert ledger.maximum_drawdown_fraction == Decimal("0.055")
    updated = refresh_performance(snapshot, mark_price=Decimal("45"), now=_NOW)
    assert updated.performance_capital_quote == Decimal("100")
    assert updated.performance_maximum_drawdown_fraction == Decimal("0.055")
    assert updated.initial_equity == updated.baseline_equity == Decimal("0")
    assert updated.high_water_mark_equity == Decimal("0")
    assert updated.cash == snapshot.deployment.cash


def test_recovery_and_rebalance_preserve_the_budget_and_worst_observed_drawdown() -> None:
    """Recovered prices and increased allocations cannot erase loss evidence or alter returns."""
    snapshot = _live_snapshot()
    lost = refresh_performance(snapshot, mark_price=Decimal("45"), now=_NOW)
    gained = refresh_performance(
        replace(snapshot, deployment=replace(lost, allocated_capital=Decimal("200"))),
        mark_price=Decimal("60"),
        now=_NOW,
    )
    assert gained.performance_capital_quote == Decimal("100")
    assert gained.high_water_mark_equity == Decimal("9.5")
    ledger = ledger_from_snapshot(replace(snapshot, deployment=gained), mark_price=Decimal("60"))
    assert ledger.total_return_fraction == Decimal("0.095")
    assert ledger.maximum_drawdown_fraction == Decimal("0.055")
    later = refresh_performance(
        replace(snapshot, deployment=gained), mark_price=Decimal("50"), now=_NOW
    )
    expected = Decimal("10") / Decimal("109.5")
    assert later.performance_maximum_drawdown_fraction == expected
    assert current_drawdown(later, ledger_equity=Decimal("-0.5")) == expected


def test_missing_marks_keep_current_metrics_unknown_and_preserve_durable_loss() -> None:
    """A previously known maximum is retained, but cannot substitute for a missing close."""
    snapshot = _live_snapshot()
    lost = refresh_performance(snapshot, mark_price=Decimal("45"), now=_NOW)
    missing = refresh_performance(replace(snapshot, deployment=lost), now=_NOW)
    assert missing.performance_maximum_drawdown_fraction == Decimal("0.055")
    ledger = ledger_from_snapshot(replace(snapshot, deployment=missing))
    assert not ledger.mark_complete
    assert ledger.total_net_pnl is None
    assert ledger.total_return_fraction is None
    assert ledger.maximum_drawdown_fraction is None


def test_unallocated_balance_is_pinned_before_entries_and_not_refreshed_with_venue_cash() -> None:
    """An unallocated book fixes its sizing budget at the first known venue observation."""
    book = replace(_live_snapshot().deployment, allocated_capital=None, venue_available_quote=None)
    assert performance_capital(book) is None
    first = apply_venue_quote(book, available=Decimal("100"), now=_NOW)
    next_poll = apply_venue_quote(first, available=Decimal("1000"), now=_NOW)
    assert next_poll.performance_capital_quote == Decimal("100")
    assert next_poll.venue_available_quote == Decimal("1000")
    assert next_poll.cash == book.cash


@pytest.mark.parametrize("capital", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_invalid_pinned_capital_remains_unknown(capital: Decimal) -> None:
    """Invalid pinned metadata must not fall back to a larger current allocation."""
    snapshot = _live_snapshot()
    snapshot = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment,
            performance_capital_quote=capital,
            performance_maximum_drawdown_fraction=Decimal("0.1"),
        ),
    )
    assert performance_capital(snapshot.deployment) is None
    ledger = ledger_from_snapshot(snapshot, mark_price=Decimal("45"))
    assert ledger.total_return_fraction is None
    assert ledger.maximum_drawdown_fraction is None
    updated = refresh_performance(snapshot, mark_price=Decimal("45"), now=_NOW)
    assert updated.performance_maximum_drawdown_fraction == Decimal("0.1")
    stamped = apply_venue_quote(snapshot.deployment, available=Decimal("1000"), now=_NOW)
    assert stamped.performance_capital_quote is capital


def test_unknown_capital_denies_drawdown_evaluation_instead_of_reporting_zero() -> None:
    """Complete marks do not make an unknown performance budget safe for entry."""
    snapshot = _live_snapshot()
    snapshot = replace(
        snapshot, deployment=replace(snapshot.deployment, performance_capital_quote=Decimal("0"))
    )
    verdict = evaluate_runtime_breakers(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        snapshot=snapshot,
        snapshots=(snapshot,),
        live_quote_cash=Decimal("1000"),
        observation=EntryObservation(
            as_of=_NOW,
            proposed_price=None,
            reference_price=Decimal("45"),
            marks={"BTC-USD": Decimal("45")},
        ),
    )
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert "performance-capital" in verdict.detail


def test_legacy_funded_live_ledger_keeps_its_existing_return_basis() -> None:
    """A live book whose opening cash was funded is not given that cash a second time."""
    deployment = replace(
        _live_snapshot().deployment,
        cash=Decimal("950"),
        initial_equity=Decimal("1000"),
        high_water_mark_equity=Decimal("1100"),
    )
    ledger = ledger_from_snapshot(DeploymentSnapshot(deployment))
    assert ledger.total_net_pnl == Decimal("-50")
    assert ledger.total_return_fraction == Decimal("-0.05")
    assert ledger.maximum_drawdown_fraction == Decimal("150") / Decimal("1100")


def test_multi_product_drawdown_never_marks_one_market_at_another_markets_fill() -> None:
    """Shared-capital percentages include both fees without inventing a cross-market loss."""
    snapshot = _live_snapshot()
    first_position = snapshot.position
    assert first_position is not None
    second_order = replace(snapshot.orders[0], id=uuid4(), product_id="ETH-USD", price=Decimal("5"))
    second_fill = replace(
        snapshot.fills[0],
        id=uuid4(),
        order_id=second_order.id,
        venue_fill_id="second-fill",
        price=Decimal("5"),
        fee=Decimal("0.05"),
    )
    snapshot = replace(
        snapshot,
        deployment=replace(snapshot.deployment, cash=Decimal("-55.55")),
        orders=(*snapshot.orders, second_order),
        fills=(*snapshot.fills, second_fill),
        positions=(
            first_position,
            replace(first_position, product_id="ETH-USD", entry_price=Decimal("5")),
        ),
    )
    ledger = ledger_from_snapshot(
        snapshot, marks={"BTC-USD": Decimal("50"), "ETH-USD": Decimal("5")}
    )
    assert ledger.total_net_pnl == Decimal("-0.55")
    assert ledger.total_return_fraction == Decimal("-0.0055")
    assert ledger.maximum_drawdown_fraction == Decimal("0.0055")


@pytest.mark.anyio
async def test_live_worker_pauses_and_latches_before_a_new_order() -> None:
    """The public bar loop enforces drawdown on a zero-based live book with no open inventory."""
    strategy = _always_entry_strategy()
    deployment = replace(
        _live_snapshot().deployment,
        strategy_id=strategy.strategy_id,
        strategy_fingerprint=strategy_fingerprint(strategy),
        cash=Decimal("-10"),
        phase=RuntimePhase.FLAT,
    )
    store = InMemoryExecutionStore()
    await store.create_deployment(deployment)
    snapshot = await store.get_deployment(deployment.id)
    paused = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_candles(60),
        broker=PaperBroker(),
        store=store,
        risk_policy=compiled_default_risk_policy().model_copy(
            update={"max_strategy_drawdown_fraction": "0.05"}
        ),
    )
    assert paused.deployment.status is DeploymentStatus.PAUSED
    assert paused.deployment.drawdown_latched
    assert paused.deployment.mismatch_detail is not None
    assert paused.deployment.mismatch_detail.startswith("STRATEGY_DRAWDOWN_LIMIT:")
    assert not paused.orders
    persisted = await store.get_deployment(deployment.id)
    assert persisted.deployment.drawdown_latched
