"""Venue observation time must not be manufactured by local order writes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.execution.test_reconcile import _LookupBroker, _snapshot_with_order
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.reconcile import import_attached_children, reconcile_open_orders
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, Order, OrderKind, OrderSide, OrderStatus

_NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def observation_order() -> Order:
    """Return a legacy open order with venue identity but no fabricated verification time."""
    return Order(
        id=uuid7(_NOW),
        deployment_id=uuid7(_NOW),
        intent_id=uuid7(_NOW),
        client_order_id=str(uuid7(_NOW)),
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        status=OrderStatus.OPEN,
        created_at=_NOW,
        updated_at=_NOW,
        venue_order_id="venue-observation-test",
        product_id="BTC-USD",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", [DeploymentMode.LIVE, DeploymentMode.PAPER])
async def test_only_live_venue_reads_supply_observation_time(mode: DeploymentMode) -> None:
    """Only an identified live broker read stamps provenance, not a recent local row."""
    store = InMemoryExecutionStore()
    order = observation_order()
    bot = await _snapshot_with_order(store, order)
    await store.save_deployment(replace(bot, mode=mode))
    before = datetime.now(UTC)
    observed = await reconcile_open_orders(
        await store.get_deployment(bot.id),
        store=store,
        broker=_LookupBroker(
            SubmitResult(status=OrderStatus.OPEN, venue_order_id=order.venue_order_id or "")
        ),
    )
    stamp = observed.orders[0].venue_observed_at
    if mode is DeploymentMode.LIVE:
        assert stamp is not None and before <= stamp <= datetime.now(UTC)
        await store.save_order(
            replace(observed.orders[0], updated_at=stamp + timedelta(seconds=60))
        )
        reread = await store.get_deployment(bot.id)
        assert reread.orders[0].venue_observed_at == stamp
        assert reread.orders[0].updated_at > stamp
    else:
        assert stamp is None


@pytest.mark.anyio
async def test_unknown_venue_result_invalidates_old_observation() -> None:
    """A newer ambiguous read cannot inherit old successful verification as current proof."""
    store = InMemoryExecutionStore()
    order = replace(observation_order(), venue_observed_at=_NOW)
    bot = await _snapshot_with_order(store, order)
    observed = await reconcile_open_orders(
        await store.get_deployment(bot.id),
        store=store,
        broker=_LookupBroker(
            SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id=order.venue_order_id or "")
        ),
    )
    assert observed.orders[0].status is OrderStatus.UNKNOWN
    assert observed.orders[0].venue_observed_at is None


@pytest.mark.anyio
async def test_attached_child_receives_its_own_observation() -> None:
    """Child freshness comes from reading that child, never from parent submission time."""
    store = InMemoryExecutionStore()
    parent = replace(
        observation_order(),
        status=OrderStatus.FILLED,
        attached_child_venue_order_id="observed-child",
        venue_observed_at=_NOW,
    )
    bot = await _snapshot_with_order(store, parent)
    before = datetime.now(UTC)
    result = await import_attached_children(
        await store.get_deployment(bot.id),
        store=store,
        product_id=parent.product_id,
        broker=_LookupBroker(
            SubmitResult(status=OrderStatus.OPEN, venue_order_id="observed-child")
        ),
    )
    child = next(order for order in result.orders if order.parent_order_id == parent.id)
    assert child.venue_observed_at is not None
    assert before <= child.venue_observed_at <= datetime.now(UTC)
    assert child.venue_observed_at != parent.venue_observed_at


@pytest.mark.anyio
@pytest.mark.parametrize("failed_read", ["get_order", "list_fills"])
async def test_failed_read_invalidates_observation(
    failed_read: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lifecycle read failures cannot retain apparently current venue verification."""
    store = InMemoryExecutionStore()
    order = replace(observation_order(), venue_observed_at=_NOW)
    bot = await _snapshot_with_order(store, order)
    broker = _LookupBroker(SubmitResult(status=OrderStatus.OPEN, venue_order_id="observed-stop"))

    async def fail(**_kwargs: object) -> None:
        raise BrokerError("Unavailable")

    monkeypatch.setattr(broker, failed_read, fail)
    result = await reconcile_open_orders(
        await store.get_deployment(bot.id), broker=broker, store=store
    )
    assert result.orders[0].status is OrderStatus.UNKNOWN
    assert result.orders[0].venue_observed_at is None


def test_legacy_order_has_no_observation_by_default() -> None:
    """Created_at and updated_at cannot become invented venue verification metadata."""
    assert observation_order().venue_observed_at is None
