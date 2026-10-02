"""Paper vs live entry-fill comparison for twin deployments (ADR 0097).

A live post-only entry can fill on Coinbase within seconds while the paper twin running
the same snapshot waits for a closed candle to trade through its limit. The comparison
pairs the twins by strategy fingerprint and reports each side's entries rested, filled,
expired, and rejected, the fill against the limit, and the time to fill.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from tests.portfolios.runtime_support import portfolio, world
from thytrader.execution.fill_comparison import entry_fill_stats, paper_live_twins
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.operator.portfolios_report import build_portfolios_report

_REST = datetime(2026, 10, 1, 12, 0, 5, tzinfo=UTC)
_FINGERPRINT = "sha256:" + ("c" * 64)


def _deployment(
    mode: DeploymentMode,
    *,
    fingerprint: str = _FINGERPRINT,
    created_at: datetime = _REST,
    kind: DeploymentKind = DeploymentKind.STRATEGY,
) -> Deployment:
    """One running 1h ETH-USD strategy book."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=fingerprint,
        strategy_id=UUID("01985cf0-7b60-7000-8000-0000000000aa"),
        strategy_name="ETH trend",
        product_id="ETH-USD",
        mode=mode,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("1000"),
        phase=RuntimePhase.FLAT,
        created_at=created_at,
        updated_at=created_at,
        kind=kind,
        timeframe="1h",
    )


def _entry(
    deployment: Deployment,
    *,
    status: OrderStatus,
    side: OrderSide = OrderSide.BUY,
    price: str = "2000",
    purpose: IntentPurpose = IntentPurpose.ENTRY,
) -> tuple[OrderIntent, Order]:
    """One post-only entry intent and its order, rested at ``_REST``."""
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id=f"entry-{uuid4()}",
        purpose=purpose,
        side=side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.1"),
        created_at=_REST,
        candle_starts_at=_REST.replace(minute=0, second=0) - timedelta(hours=1),
        price=Decimal(price),
    )
    order = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        side=side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.1"),
        status=status,
        created_at=_REST,
        updated_at=_REST,
        price=Decimal(price),
        product_id="ETH-USD",
    )
    return intent, order


def _fill(order: Order, *, at: datetime, price: str, quantity: str = "0.1") -> Fill:
    """One fill of ``order``."""
    return Fill(
        id=uuid4(),
        deployment_id=order.deployment_id,
        order_id=order.id,
        venue_fill_id=f"fill-{uuid4()}",
        price=Decimal(price),
        quantity=Decimal(quantity),
        fee=Decimal("0"),
        filled_at=at,
    )


def test_live_entry_stats_count_outcomes_slippage_and_wait() -> None:
    """Filled, expired, rejected, and working entries; exits are not entries."""
    live = _deployment(DeploymentMode.LIVE)
    filled_intent, filled = _entry(live, status=OrderStatus.FILLED)
    partial_intent, partial = _entry(live, status=OrderStatus.FILLED)
    expired_intent, expired = _entry(live, status=OrderStatus.CANCELED)
    rejected_intent, rejected = _entry(live, status=OrderStatus.REJECTED)
    working_intent, working = _entry(live, status=OrderStatus.OPEN)
    exit_intent, exit_order = _entry(
        live, status=OrderStatus.FILLED, side=OrderSide.SELL, purpose=IntentPurpose.TAKE_PROFIT
    )
    snapshot = DeploymentSnapshot(
        deployment=live,
        intents=(
            filled_intent,
            partial_intent,
            expired_intent,
            rejected_intent,
            working_intent,
            exit_intent,
        ),
        orders=(filled, partial, expired, rejected, working, exit_order),
        fills=(
            _fill(filled, at=_REST + timedelta(seconds=5), price="2000"),
            _fill(partial, at=_REST + timedelta(seconds=10), price="2000", quantity="0.05"),
            _fill(partial, at=_REST + timedelta(seconds=30), price="2004", quantity="0.05"),
            _fill(exit_order, at=_REST + timedelta(hours=2), price="2100"),
        ),
    )
    stats = entry_fill_stats(snapshot)
    assert (
        stats.entries_rested,
        stats.entries_filled,
        stats.entries_expired,
        stats.entries_rejected,
        stats.entries_working,
    ) == (5, 2, 1, 1, 1)
    # Partial average 2002 against a 2000 buy limit is 10 bps worse; the other is 0.
    assert stats.average_fill_vs_limit_bps == Decimal(5)
    assert stats.average_seconds_to_fill == Decimal("7.5")
    assert stats.median_seconds_to_fill == Decimal("7.5")


def test_paper_waits_run_to_the_fill_bar_close() -> None:
    """A paper fill stamped at its bar's start is only known when that bar closes."""
    paper = _deployment(DeploymentMode.PAPER)
    intent, order = _entry(paper, status=OrderStatus.FILLED)
    fill_bar = _REST.replace(minute=0, second=0) + timedelta(hours=1)
    snapshot = DeploymentSnapshot(
        deployment=paper,
        intents=(intent,),
        orders=(order,),
        fills=(_fill(order, at=fill_bar, price="2000"),),
    )
    stats = entry_fill_stats(snapshot)
    assert stats.average_fill_vs_limit_bps == Decimal(0)
    assert stats.median_seconds_to_fill == Decimal(2 * 3600 - 5)


def test_a_sell_entry_below_its_limit_is_adverse() -> None:
    """Short entries sell: a fill under the limit is worse, so the bps are positive."""
    live = _deployment(DeploymentMode.LIVE)
    intent, order = _entry(live, status=OrderStatus.FILLED, side=OrderSide.SELL)
    snapshot = DeploymentSnapshot(
        deployment=live,
        intents=(intent,),
        orders=(order,),
        fills=(_fill(order, at=_REST + timedelta(seconds=1), price="1998"),),
    )
    assert entry_fill_stats(snapshot).average_fill_vs_limit_bps == Decimal(10)


def test_twins_pair_the_newest_paper_and_live_books_per_fingerprint() -> None:
    """Only strategy books with equal fingerprints pair; the newest of each mode wins."""
    older_paper = _deployment(DeploymentMode.PAPER, created_at=_REST - timedelta(days=2))
    paper = _deployment(DeploymentMode.PAPER)
    live = _deployment(DeploymentMode.LIVE, created_at=_REST + timedelta(minutes=1))
    lonely = _deployment(DeploymentMode.PAPER, fingerprint="sha256:" + ("d" * 64))
    discretionary = _deployment(DeploymentMode.LIVE, kind=DeploymentKind.DISCRETIONARY)
    twins = paper_live_twins((older_paper, paper, live, lonely, discretionary), limit=10)
    assert [(twin.paper_deployment_id, twin.live_deployment_id) for twin in twins] == [
        (paper.id, live.id)
    ]
    assert twins[0].strategy_name == "ETH trend"
    assert paper_live_twins((paper, live), limit=0) == ()


@pytest.mark.anyio
async def test_portfolios_report_carries_the_paper_live_fill_comparison() -> None:
    """The operator ``portfolios`` report lists every twin pair with both digests."""
    state = world()
    await portfolio(state)
    store = state.execution
    paper = await store.create_deployment(_deployment(DeploymentMode.PAPER))
    live = await store.create_deployment(_deployment(DeploymentMode.LIVE))
    for deployment, filled_at in (
        (paper, _REST.replace(minute=0, second=0) + timedelta(hours=1)),
        (live, _REST + timedelta(seconds=5)),
    ):
        intent, order = _entry(deployment, status=OrderStatus.FILLED)
        await store.save_intent(intent)
        await store.save_order(order)
        await store.save_fill(_fill(order, at=filled_at, price="2000"))
    report = await build_portfolios_report(state.portfolios, state.execution)
    (comparison,) = report.payload.paper_live_fill_comparisons
    assert comparison.strategy_fingerprint == _FINGERPRINT
    assert (comparison.paper.deployment_id, comparison.live.deployment_id) == (paper.id, live.id)
    assert (comparison.paper.entries_filled, comparison.live.entries_filled) == (1, 1)
    assert comparison.live.median_seconds_to_fill == "5"
    assert comparison.paper.median_seconds_to_fill == "7195"
    assert comparison.live.average_fill_vs_limit_bps == "0"
