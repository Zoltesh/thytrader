"""Atomic, idempotent fill application for crash replay, live fees, and add-count."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from thytrader.execution.fill_ledger import (
    apply_fill,
    apply_or_import_fill,
    apply_unapplied_fills,
    import_live_fills,
)
from thytrader.execution.ids import uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    FillApplication,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.execution.broker import SubmitResult
    from thytrader.market_data.models import Candle

_DEPLOYMENT_ID = UUID(int=1)
_INTENT_ID = UUID(int=2)


def _now() -> datetime:
    """Return a fixed 2026-03-01 UTC instant so fixtures are deterministic."""
    return datetime(2026, 3, 1, tzinfo=UTC)


async def _seeded_store(*, cash: Decimal = Decimal("10000")) -> InMemoryExecutionStore:
    """Return a store with one discretionary paper deployment pending an entry fill."""
    store = InMemoryExecutionStore()
    now = _now()
    deployment = Deployment(
        id=_DEPLOYMENT_ID,
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="5m",
        cash=cash,
        paper_starting_cash=cash,
        paper_maker_fee_rate=Decimal("0"),
        paper_taker_fee_rate=Decimal("0"),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_stop_price=Decimal("50000"),
        pending_target_price=Decimal("200000"),
        created_at=now,
        updated_at=now,
    )
    await store.create_deployment(deployment)
    return store


def _order(
    *, quantity: Decimal, order_id: UUID | None = None, intent_id: UUID = _INTENT_ID
) -> Order:
    """Build one pending buy order for the seeded deployment."""
    now = _now()
    return Order(
        id=order_id or uuid7(now),
        deployment_id=_DEPLOYMENT_ID,
        intent_id=intent_id,
        client_order_id=f"client-{order_id or uuid7(now)}",
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=quantity,
        status=OrderStatus.PENDING,
        created_at=now,
        updated_at=now,
        venue_order_id=f"venue-{order_id or uuid7(now)}",
        product_id="BTC-USD",
    )


def _fill(
    *, order: Order, venue_fill_id: str, quantity: Decimal, price: Decimal, fee: Decimal
) -> Fill:
    """Build one exact fill against a known order."""
    return Fill(
        id=uuid7(_now()),
        deployment_id=_DEPLOYMENT_ID,
        order_id=order.id,
        venue_fill_id=venue_fill_id,
        price=price,
        quantity=quantity,
        fee=fee,
        filled_at=_now(),
    )


@dataclass
class _FillsBroker:
    """Broker double whose ``list_fills`` returns a scripted, mutable sequence of real fills."""

    fills: tuple[Fill, ...] = ()
    list_fills_calls: int = 0

    async def place_order(self, **_kwargs: object) -> SubmitResult:
        """Unused in these fill-ledger tests."""
        raise AssertionError("place_order should not run")

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Unused in these fill-ledger tests."""
        del venue_order_id, client_order_id
        raise AssertionError("cancel_order should not run")

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Unused in these fill-ledger tests."""
        del venue_order_id, client_order_id
        raise AssertionError("get_order should not run")

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Return the scripted fills, counting calls so tests can assert import cadence."""
        del product_id, order_id
        self.list_fills_calls += 1
        return self.fills

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live import tests do not candle-match."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Unused in fill-import tests."""
        del product_id, side
        return mark


@dataclass
class _NoLocalFillBroker:
    """Broker double whose ``list_fills`` must never run because a local fill exists."""

    called: bool = field(default=False)

    async def place_order(self, **_kwargs: object) -> SubmitResult:
        """Unused."""
        raise AssertionError("place_order should not run")

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Unused."""
        del venue_order_id, client_order_id
        raise AssertionError("cancel_order should not run")

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Unused."""
        del venue_order_id, client_order_id
        raise AssertionError("get_order should not run")

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Record that it ran; a local paper fill must make this unreachable."""
        del product_id, order_id
        self.called = True
        return ()

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """A local paper fill must never reach candle matching here."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Unused in the local-fill path."""
        del product_id, side
        return mark


@pytest.mark.anyio
async def test_apply_fill_replay_is_a_verified_no_op() -> None:
    """F01: applying the exact same fill twice leaves cash, quantity, and fees unchanged."""
    store = await _seeded_store()
    order = _order(quantity=Decimal("0.01"))
    await store.save_order(order)
    fill = _fill(
        order=order,
        venue_fill_id="v-1",
        quantity=Decimal("0.01"),
        price=Decimal("100000"),
        fee=Decimal("1"),
    )
    filled_order = replace(order, status=OrderStatus.FILLED, filled_quantity=fill.quantity)

    snapshot = await store.get_deployment(_DEPLOYMENT_ID)
    once = await apply_fill(snapshot, fill=fill, order=filled_order, store=store)
    expected_cash = Decimal("10000") - Decimal("1000") - Decimal("1")
    assert once.deployment.cash == expected_cash
    assert once.position is not None
    assert once.position.quantity == Decimal("0.01")
    assert len(once.fills) == 1

    twice = await apply_fill(once, fill=fill, order=filled_order, store=store)
    assert twice.deployment.cash == expected_cash
    assert twice.position is not None
    assert twice.position.quantity == Decimal("0.01")
    assert len(twice.fills) == 1


@pytest.mark.anyio
async def test_apply_fill_effect_reports_applied_false_on_replay() -> None:
    """F01: the store's own applied flag distinguishes a fresh apply from a verified replay."""
    store = await _seeded_store()
    order = _order(quantity=Decimal("0.02"))
    await store.save_order(order)
    fill = _fill(
        order=order,
        venue_fill_id="v-2",
        quantity=Decimal("0.02"),
        price=Decimal("1000"),
        fee=Decimal("0"),
    )
    filled_order = replace(order, status=OrderStatus.FILLED, filled_quantity=fill.quantity)

    snapshot = await store.get_deployment(_DEPLOYMENT_ID)
    position = Position(
        deployment_id=_DEPLOYMENT_ID,
        quantity=fill.quantity,
        entry_price=fill.price,
        stop_price=Decimal("50000"),
        target_price=Decimal("200000"),
        entered_bar=_now(),
        updated_at=_now(),
        product_id="BTC-USD",
        add_count=1,
        last_fill_intent_id=filled_order.intent_id,
    )
    application = FillApplication(
        fill=fill,
        order=filled_order,
        deployment=replace(snapshot.deployment, cash=Decimal("8000"), phase=RuntimePhase.OPEN),
        position=position,
        clear_position=False,
    )
    first = await store.apply_fill_effect(application)
    assert first.applied is True
    second = await store.apply_fill_effect(application)
    assert second.applied is False


@pytest.mark.anyio
async def test_apply_or_import_fill_uses_local_paper_fill_without_calling_the_broker() -> None:
    """F02: a local (paper-simulated) fill is evidence on its own; live import never runs."""
    store = await _seeded_store()
    order = _order(quantity=Decimal("0.01"))
    await store.save_order(order)
    fill = _fill(
        order=order,
        venue_fill_id=f"{order.venue_order_id}:immediate",
        quantity=Decimal("0.01"),
        price=Decimal("100000"),
        fee=Decimal("2"),
    )
    await store.save_fill(fill)
    filled_order = replace(order, status=OrderStatus.FILLED, filled_quantity=fill.quantity)

    broker = _NoLocalFillBroker()
    snapshot = await store.get_deployment(_DEPLOYMENT_ID)
    result = await apply_or_import_fill(
        snapshot, order=filled_order, store=store, broker=broker, product_id="BTC-USD"
    )
    assert broker.called is False
    assert result.position is not None
    assert result.position.quantity == Decimal("0.01")
    assert result.position.entry_price == Decimal("100000")
    assert result.deployment.cash == Decimal("10000") - Decimal("1000") - Decimal("2")


@pytest.mark.anyio
async def test_apply_or_import_fill_imports_real_partial_live_fills_and_fees() -> None:
    """F02: no local fill exists for a live order, so real venue fills/fees are imported.

    Two partial executions with real nonzero fees, exactly as the audit's
    regression gate describes, must both apply and sum correctly.
    """
    store = await _seeded_store()
    order = _order(quantity=Decimal("0.02"))
    await store.save_order(order)
    filled_order = replace(order, status=OrderStatus.FILLED, filled_quantity=order.quantity)
    remote_fills = (
        _fill(
            order=order,
            venue_fill_id="remote-1",
            quantity=Decimal("0.01"),
            price=Decimal("100000"),
            fee=Decimal("0.6"),
        ),
        _fill(
            order=order,
            venue_fill_id="remote-2",
            quantity=Decimal("0.01"),
            price=Decimal("100100"),
            fee=Decimal("0.7"),
        ),
    )
    broker = _FillsBroker(fills=remote_fills)

    snapshot = await store.get_deployment(_DEPLOYMENT_ID)
    result = await apply_or_import_fill(
        snapshot, order=filled_order, store=store, broker=broker, product_id="BTC-USD"
    )
    assert broker.list_fills_calls == 1
    assert result.position is not None
    assert result.position.quantity == Decimal("0.02")
    expected_cash = (
        Decimal("10000")
        - (Decimal("100000") * Decimal("0.01") + Decimal("0.6"))
        - (Decimal("100100") * Decimal("0.01") + Decimal("0.7"))
    )
    assert result.deployment.cash == expected_cash
    assert len(result.fills) == 2
    assert all(item.fee != Decimal("0") for item in result.fills)


@pytest.mark.anyio
async def test_import_live_fills_handles_delayed_availability_then_restart_safe_replay() -> None:
    """F02: fills not yet visible import nothing; once visible, importing twice is safe."""
    store = await _seeded_store()
    order = _order(quantity=Decimal("0.01"))
    await store.save_order(order)
    filled_order = replace(order, status=OrderStatus.FILLED, filled_quantity=order.quantity)

    delayed_broker = _FillsBroker(fills=())
    snapshot = await store.get_deployment(_DEPLOYMENT_ID)
    still_delayed = await import_live_fills(
        snapshot, order=filled_order, broker=delayed_broker, store=store, product_id="BTC-USD"
    )
    assert still_delayed.position is None
    assert still_delayed.deployment.cash == Decimal("10000")

    now_available = _fill(
        order=order,
        venue_fill_id="delayed-1",
        quantity=Decimal("0.01"),
        price=Decimal("100000"),
        fee=Decimal("1"),
    )
    available_broker = _FillsBroker(fills=(now_available,))
    applied = await import_live_fills(
        still_delayed,
        order=filled_order,
        broker=available_broker,
        store=store,
        product_id="BTC-USD",
    )
    expected_cash = Decimal("10000") - Decimal("1000") - Decimal("1")
    assert applied.position is not None
    assert applied.deployment.cash == expected_cash

    # Simulates a restart before the import had a chance to be observed as complete:
    # re-importing from the same (still fully visible) venue fill list must not double-apply.
    replayed = await import_live_fills(
        applied, order=filled_order, broker=available_broker, store=store, product_id="BTC-USD"
    )
    assert replayed.deployment.cash == expected_cash
    assert replayed.position is not None
    assert replayed.position.quantity == Decimal("0.01")
    assert len(replayed.fills) == 1


@pytest.mark.anyio
async def test_add_count_counts_distinct_intents_not_fragments() -> None:
    """F28: 20 same-intent fragments hold at add_count 1; a new intent adds exactly one more."""
    store = await _seeded_store(cash=Decimal("1000000"))
    entry_order = _order(quantity=Decimal("0.20"), intent_id=UUID(int=100))
    await store.save_order(entry_order)

    snapshot = await store.get_deployment(_DEPLOYMENT_ID)
    for fragment in range(20):
        fill = _fill(
            order=entry_order,
            venue_fill_id=f"entry-fragment-{fragment}",
            quantity=Decimal("0.01"),
            price=Decimal("1000"),
            fee=Decimal("0"),
        )
        snapshot = await apply_fill(snapshot, fill=fill, order=entry_order, store=store)
    assert snapshot.position is not None
    assert snapshot.position.add_count == 1
    assert snapshot.position.quantity == Decimal("0.20")

    add_order = _order(quantity=Decimal("0.05"), intent_id=UUID(int=101))
    await store.save_order(add_order)
    for fragment in range(5):
        fill = _fill(
            order=add_order,
            venue_fill_id=f"add-fragment-{fragment}",
            quantity=Decimal("0.01"),
            price=Decimal("1000"),
            fee=Decimal("0"),
        )
        snapshot = await apply_fill(snapshot, fill=fill, order=add_order, store=store)
    assert snapshot.position is not None
    assert snapshot.position.add_count == 2
    assert snapshot.position.quantity == Decimal("0.25")
    assert snapshot.position.last_fill_intent_id == UUID(int=101)


@pytest.mark.anyio
async def test_unapplied_local_fill_is_repaired_on_the_next_cycle() -> None:
    """F01: ``save_fill`` then a crash must not leave the fill as fake coverage.

    Paper submit records the immediate fill as evidence only. If the process dies
    before ``apply_fill_effect``, the next cycle must apply that exact fill once
    and leave cash/quantity unchanged on a second pass.
    """
    store = await _seeded_store()
    order = _order(quantity=Decimal("0.01"))
    await store.save_order(order)
    fill = _fill(
        order=order,
        venue_fill_id=f"{order.venue_order_id}:immediate",
        quantity=Decimal("0.01"),
        price=Decimal("100000"),
        fee=Decimal("1"),
    )
    await store.save_fill(fill)
    filled_order = replace(order, status=OrderStatus.FILLED, filled_quantity=fill.quantity)
    await store.save_order(filled_order)

    snapshot = await store.get_deployment(_DEPLOYMENT_ID)
    assert snapshot.position is None
    assert snapshot.fills[0].applied_at is None

    repaired = await apply_unapplied_fills(snapshot, store=store)
    expected_cash = Decimal("10000") - Decimal("1000") - Decimal("1")
    assert repaired.position is not None
    assert repaired.position.quantity == Decimal("0.01")
    assert repaired.deployment.cash == expected_cash
    assert repaired.fills[0].applied_at is not None

    replayed = await apply_unapplied_fills(repaired, store=store)
    assert replayed.deployment.cash == expected_cash
    assert replayed.position is not None
    assert replayed.position.quantity == Decimal("0.01")
    assert len(replayed.fills) == 1
