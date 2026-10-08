"""Live exit ordering, flatten, cancel-confirm, final state, and rejection latch.

Regressions from the first supervised live trade (2026-10-01, BTC-USDC 5m): a time exit
sold before the venue-attached child was known, a flatten rested a stray bracket, a
pending Coinbase cancel paused every cycle, a flattened book ended paused with a stale
bracket detail, and a rejected bracket was re-submitted every ~30 seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.broker import CANCEL_PENDING_REASON, SubmitResult
from thytrader.execution.exit_guards import (
    BRACKET_NOT_RESTED_DETAIL,
    CANCEL_BEFORE_EXIT_DETAIL,
    FLAT_AFTER_FAULT_DETAIL,
    PROTECTIVE_REJECTED_PREFIX,
)
from thytrader.execution.loop import (
    flatten_stopped_residual,
    maintain_open_inventory,
    process_closed_bar,
)
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    LifecycleCommand,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.trading.models import DeploymentSnapshot

_QTY = Decimal("0.01")
_STOP = Decimal("90")
_TARGET = Decimal("120")


def _product() -> MarketProduct:
    """Return BTC-USD venue increments."""
    return MarketProduct(
        product_id="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.0001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    )


def _strategy() -> StrategyDefinition:
    """Reference template strategy (max_bars_held 96, no trailing)."""
    draft = create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC))
    return StrategyDefinition.model_validate(draft.model_dump(mode="python"))


def _candle(hour: int) -> Candle:
    """Return a placeholder hourly candle that never reaches stop or target."""
    price = Decimal("100") + Decimal(hour)
    return Candle(
        starts_at=datetime(2026, 1, 1, hour, tzinfo=UTC),
        open=price,
        high=price + Decimal("2"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("10"),
    )


def _window() -> tuple[Candle, ...]:
    """Return three closed candles; the last is the bar under evaluation."""
    return (_candle(0), _candle(1), _candle(2))


@dataclass
class _ScriptedVenue:
    """Live-style broker double with scripted GET, cancel, create, and fill responses."""

    statuses: dict[str, list[OrderStatus]] = field(default_factory=dict)
    attached: dict[str, str] = field(default_factory=dict)
    cancel_results: dict[str, SubmitResult] = field(default_factory=dict)
    place_status: dict[OrderKind, OrderStatus] = field(default_factory=dict)
    reject_reason: str = "{'error': 'INSUFFICIENT_FUND'}"
    fills: dict[str, list[tuple[Fill, ...]]] = field(default_factory=dict)
    events: list[tuple[str, str]] = field(default_factory=list)
    placed: list[OrderKind] = field(default_factory=list)

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """Record one create and answer with the scripted status for its kind."""
        del product_id, side, quantity, price, stop_trigger_price, take_profit_price
        self.placed.append(kind)
        venue_id = f"{kind.value}-{len(self.placed)}"
        self.events.append(("place", venue_id))
        status = self.place_status.get(kind, OrderStatus.OPEN)
        if status is OrderStatus.REJECTED:
            return SubmitResult(
                status=status, venue_order_id=client_order_id, reject_reason=self.reject_reason
            )
        return SubmitResult(status=status, venue_order_id=venue_id)

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Record one cancel and answer with its scripted result (CANCELED by default)."""
        del client_order_id
        self.events.append(("cancel", venue_order_id))
        result = self.cancel_results.get(
            venue_order_id,
            SubmitResult(status=OrderStatus.CANCELED, venue_order_id=venue_order_id),
        )
        if result.status is OrderStatus.CANCELED:
            self.statuses[venue_order_id] = [OrderStatus.CANCELED]
        return result

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Pop the next scripted status (the last one repeats); report attached children."""
        del client_order_id
        self.events.append(("get", venue_order_id))
        queue = self.statuses.get(venue_order_id, [OrderStatus.OPEN])
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        return SubmitResult(
            status=status,
            venue_order_id=venue_order_id,
            attached_child_venue_order_id=self.attached.get(venue_order_id),
        )

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Pop the next scripted fill page for one order (empty when unscripted)."""
        del product_id
        pages = self.fills.get(order_id or "", [])
        if not pages:
            return ()
        return pages.pop(0) if len(pages) > 1 else pages[0]

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live never candle-matches."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Unused: these books never enter."""
        del product_id, side
        return mark

    def kinds(self, action: str) -> list[str]:
        """Return the venue ids touched by one action, in order."""
        return [venue_id for name, venue_id in self.events if name == action]


def _venue_fill(order_id: str, *, price: str = "101") -> Fill:
    """Build one REST fill for a venue order id (local ids are assigned at ingest)."""
    return Fill(
        id=uuid7(utc_now()),
        deployment_id=UUID(int=0),
        order_id=UUID(int=0),
        venue_fill_id=f"{order_id}-fill",
        price=Decimal(price),
        quantity=_QTY,
        fee=Decimal("0.01"),
        filled_at=_candle(2).starts_at,
        venue_order_id=order_id,
    )


async def _live_book(
    store: InMemoryExecutionStore,
    strategy: StrategyDefinition,
    *,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    command: LifecycleCommand = LifecycleCommand.NONE,
    bars_held: int = 1,
    last_evaluated: int = 1,
    phase: RuntimePhase = RuntimePhase.OPEN,
    with_position: bool = True,
    detail: str | None = None,
) -> DeploymentSnapshot:
    """Insert one live BTC-USD book, optionally holding a long position entered at bar 1."""
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(strategy),
        strategy_id=strategy.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=status,
        paper_starting_cash=None,
        cash=Decimal("10000"),
        phase=phase,
        last_evaluated_bar=_candle(last_evaluated).starts_at,
        bars_held=bars_held,
        created_at=now,
        updated_at=now,
        lifecycle_command=command,
        mismatch_detail=detail,
    )
    await store.create_deployment(deployment)
    if with_position:
        await store.save_position(
            Position(
                deployment_id=deployment.id,
                quantity=_QTY,
                entry_price=Decimal("100"),
                stop_price=_STOP,
                target_price=_TARGET,
                entered_bar=_candle(1).starts_at,
                updated_at=now,
                product_id="BTC-USD",
            ),
            deployment_id=deployment.id,
        )
    return await store.get_deployment(deployment.id)


async def _filled_attached_entry(
    store: InMemoryExecutionStore, snapshot: DeploymentSnapshot
) -> None:
    """Persist the filled attached entry that opened the position, without its child id."""
    now = utc_now()
    entry = Order(
        id=uuid7(now),
        deployment_id=snapshot.deployment.id,
        intent_id=uuid7(now),
        client_order_id="attached-entry",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=_QTY,
        price=Decimal("100"),
        stop_trigger_price=_STOP,
        take_profit_price=_TARGET,
        status=OrderStatus.FILLED,
        venue_order_id="attached-entry",
        filled_quantity=_QTY,
        created_at=now,
        updated_at=now,
    )
    await store.save_order(entry)
    await store.save_fill(
        Fill(
            id=uuid7(now),
            deployment_id=snapshot.deployment.id,
            order_id=entry.id,
            venue_fill_id="attached-entry-fill",
            price=Decimal("100"),
            quantity=_QTY,
            fee=Decimal("0"),
            filled_at=_candle(1).starts_at,
            economics_applied_at=now,
        )
    )


async def _resting_child(store: InMemoryExecutionStore, snapshot: DeploymentSnapshot) -> None:
    """Persist an already-known venue OCO child covering the position."""
    now = utc_now()
    await store.save_order(
        Order(
            id=uuid7(now),
            deployment_id=snapshot.deployment.id,
            intent_id=uuid7(now),
            client_order_id="entry:child",
            side=OrderSide.SELL,
            kind=OrderKind.TRIGGER_BRACKET,
            quantity=_QTY,
            price=_TARGET,
            stop_trigger_price=_STOP,
            take_profit_price=_TARGET,
            status=OrderStatus.OPEN,
            venue_order_id="child-1",
            created_at=now,
            updated_at=now,
        )
    )


@pytest.mark.anyio
async def test_time_exit_adopts_and_cancels_the_attached_child_before_selling() -> None:
    """Defect 1: a due time exit learns the venue child first and cancels it before the sell."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    max_bars = strategy.exits.time_exit.max_bars_held
    snapshot = await _live_book(store, strategy, bars_held=max_bars - 1)
    await _filled_attached_entry(store, snapshot)
    venue = _ScriptedVenue(
        attached={"attached-entry": "attached-child"},
        statuses={"attached-entry": [OrderStatus.FILLED]},
        place_status={OrderKind.MARKETABLE: OrderStatus.FILLED},
        fills={"marketable-1": [(_venue_fill("marketable-1"),)]},
    )
    updated = await process_closed_bar(
        await store.get_deployment(snapshot.deployment.id),
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=venue,
        store=store,
    )
    assert venue.kinds("cancel") == ["attached-child"]
    assert venue.events.index(("cancel", "attached-child")) < venue.events.index(
        ("place", "marketable-1")
    )
    assert venue.placed == [OrderKind.MARKETABLE]
    assert updated.position is None
    assert updated.deployment.phase is RuntimePhase.FLAT
    assert updated.deployment.status is DeploymentStatus.RUNNING
    assert updated.deployment.mismatch_detail is None


@pytest.mark.anyio
async def test_flatten_waits_on_a_pending_cancel_then_exits_without_a_new_bracket() -> None:
    """Defects 2-4: a pending cancel neither pauses nor re-cancels, and no bracket is rested."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _live_book(
        store,
        strategy,
        status=DeploymentStatus.STOPPED,
        command=LifecycleCommand.FLATTEN,
        phase=RuntimePhase.PENDING_EXIT,
    )
    await _resting_child(store, snapshot)
    venue = _ScriptedVenue(
        cancel_results={
            "child-1": SubmitResult(
                status=OrderStatus.OPEN,
                venue_order_id="child-1",
                reject_reason=CANCEL_PENDING_REASON,
            )
        },
        statuses={"child-1": [OrderStatus.OPEN, OrderStatus.CANCELED]},
        place_status={OrderKind.MARKETABLE: OrderStatus.FILLED},
        fills={"marketable-1": [(_venue_fill("marketable-1"),)]},
    )
    first = await flatten_stopped_residual(
        await store.get_deployment(snapshot.deployment.id),
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=venue,
        store=store,
    )
    assert venue.placed == []
    assert first.deployment.status is DeploymentStatus.STOPPED
    assert first.deployment.mismatch_detail is None
    assert first.position is not None
    second = await flatten_stopped_residual(
        first,
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=venue,
        store=store,
    )
    assert venue.kinds("cancel") == ["child-1"]
    assert venue.placed == [OrderKind.MARKETABLE]
    assert second.position is None
    assert second.deployment.status is DeploymentStatus.STOPPED
    assert second.deployment.phase is RuntimePhase.FLAT
    assert second.deployment.mismatch_detail is None


@pytest.mark.anyio
async def test_failed_cancel_during_flatten_keeps_the_book_stopped() -> None:
    """Defect 4: a fault while flattening records the detail but never re-pauses a stop."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _live_book(
        store, strategy, status=DeploymentStatus.STOPPED, command=LifecycleCommand.FLATTEN
    )
    await _resting_child(store, snapshot)
    venue = _ScriptedVenue(
        cancel_results={
            "child-1": SubmitResult(
                status=OrderStatus.OPEN,
                venue_order_id="child-1",
                reject_reason="cancel_failed:COMMANDER_REJECTED_CANCEL_ORDER",
            )
        },
    )
    updated = await flatten_stopped_residual(
        await store.get_deployment(snapshot.deployment.id),
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=venue,
        store=store,
    )
    assert venue.placed == []
    assert updated.deployment.status is DeploymentStatus.STOPPED
    assert updated.deployment.mismatch_detail == CANCEL_BEFORE_EXIT_DETAIL


@pytest.mark.anyio
async def test_paused_flatten_book_exits_between_bars_and_settles_stopped() -> None:
    """Defects 2 and 4: a paused book carrying a flatten exits instead of re-bracketing."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _live_book(
        store,
        strategy,
        status=DeploymentStatus.PAUSED,
        command=LifecycleCommand.FLATTEN,
        last_evaluated=2,
        detail=CANCEL_BEFORE_EXIT_DETAIL,
    )
    venue = _ScriptedVenue(
        place_status={OrderKind.MARKETABLE: OrderStatus.FILLED},
        fills={"marketable-1": [(_venue_fill("marketable-1"),)]},
    )
    updated = await maintain_open_inventory(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=venue,
        store=store,
    )
    assert venue.placed == [OrderKind.MARKETABLE]
    assert updated.position is None
    assert updated.deployment.status is DeploymentStatus.STOPPED
    assert updated.deployment.phase is RuntimePhase.FLAT
    assert updated.deployment.mismatch_detail is None


@pytest.mark.anyio
async def test_lagging_exit_fills_never_trigger_a_bracket_or_second_exit() -> None:
    """Defect 4: a FILLED exit whose REST fills lag stays pending; nothing else is sent."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    max_bars = strategy.exits.time_exit.max_bars_held
    snapshot = await _live_book(store, strategy, bars_held=max_bars - 1)
    venue = _ScriptedVenue(
        place_status={OrderKind.MARKETABLE: OrderStatus.FILLED},
        fills={"marketable-1": [(), (_venue_fill("marketable-1"),)]},
    )
    first = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=venue,
        store=store,
    )
    assert first.position is not None
    assert first.deployment.phase is RuntimePhase.PENDING_EXIT
    assert first.deployment.status is DeploymentStatus.RUNNING
    settled = await maintain_open_inventory(
        first,
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=venue,
        store=store,
    )
    assert venue.placed == [OrderKind.MARKETABLE]
    assert settled.position is None
    assert settled.deployment.phase is RuntimePhase.FLAT


@pytest.mark.anyio
async def test_flat_paused_book_retires_a_stale_bracket_detail() -> None:
    """Defect 4: once flat and idle, a position-only pause detail no longer applies."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _live_book(
        store,
        strategy,
        status=DeploymentStatus.PAUSED,
        last_evaluated=2,
        phase=RuntimePhase.FLAT,
        with_position=False,
        detail=BRACKET_NOT_RESTED_DETAIL,
    )
    updated = await maintain_open_inventory(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=_ScriptedVenue(),
        store=store,
    )
    assert updated.deployment.status is DeploymentStatus.PAUSED
    assert updated.deployment.mismatch_detail == FLAT_AFTER_FAULT_DETAIL


async def _maintain(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    venue: _ScriptedVenue,
    store: InMemoryExecutionStore,
) -> DeploymentSnapshot:
    """Run one between-bars maintenance cycle."""
    return await maintain_open_inventory(
        await store.get_deployment(snapshot.deployment.id),
        strategy=strategy,
        product=_product(),
        candles=_window(),
        broker=venue,
        store=store,
    )


async def _backdate_rejections(store: InMemoryExecutionStore, deployment_id: UUID) -> None:
    """Move every rejected order's timestamps two minutes into the past."""
    snapshot = await store.get_deployment(deployment_id)
    past = utc_now() - timedelta(minutes=2)
    for order in snapshot.orders:
        if order.status is OrderStatus.REJECTED:
            await store.save_order(replace(order, created_at=past, updated_at=past))


@pytest.mark.anyio
async def test_rejected_bracket_is_latched_with_backoff_and_audited_once() -> None:
    """Defect 5: an identical venue rejection pauses once, then retries only after backoff."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _live_book(store, strategy, last_evaluated=2)
    venue = _ScriptedVenue(place_status={OrderKind.TRIGGER_BRACKET: OrderStatus.REJECTED})
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        for _cycle in range(3):
            latched = await _maintain(snapshot, strategy=strategy, venue=venue, store=store)
        assert venue.placed == [OrderKind.TRIGGER_BRACKET]
        assert latched.deployment.status is DeploymentStatus.PAUSED
        detail = latched.deployment.mismatch_detail or ""
        assert detail.startswith(PROTECTIVE_REJECTED_PREFIX)
        assert "INSUFFICIENT_FUND" in detail
        await _backdate_rejections(store, snapshot.deployment.id)
        retried = await _maintain(snapshot, strategy=strategy, venue=venue, store=store)
        await _maintain(snapshot, strategy=strategy, venue=venue, store=store)
    assert venue.placed == [OrderKind.TRIGGER_BRACKET, OrderKind.TRIGGER_BRACKET]
    assert "2 time(s)" in (retried.deployment.mismatch_detail or "")
    actions = [event.action for event in await audit.list_recent(limit=50)]
    assert actions.count("protective_submit_latched") == 2
