"""Ambiguous live submits: bounded client-id recovery, fail-closed pause, and audit."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.execution.test_reconcile import _LookupBroker, _snapshot_with_order
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    DeploymentStatus,
    IntentPurpose,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
)
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.market_data.models import Candle

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class _RecoveringBroker(_LookupBroker):
    """Broker double that also implements the bounded client-id lookup."""

    def __init__(self, found: SubmitResult | None) -> None:
        """Bind the lookup answer; get_order must not be used for id-less orders."""
        super().__init__(SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id=""))
        self.found = found
        self.lookups: list[tuple[str, str, datetime]] = []

    async def find_order_by_client_id(
        self,
        *,
        client_order_id: str,
        product_id: str,
        submitted_at: datetime,
    ) -> SubmitResult | None:
        """Record the bounded lookup and return the configured answer."""
        self.lookups.append((client_order_id, product_id, submitted_at))
        return self.found


class _SubmitBroker(_LookupBroker):
    """Broker double whose place_order returns or raises a prepared outcome."""

    def __init__(
        self, *, result: SubmitResult | None = None, error: Exception | None = None
    ) -> None:
        """Bind the create outcome."""
        super().__init__(SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id=""))
        self.result = result
        self.error = error
        self.placed = 0

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
        """Return or raise the prepared outcome exactly once per call."""
        del client_order_id, product_id, side, kind, quantity, price
        del stop_trigger_price, take_profit_price
        self.placed += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _unknown_order(deployment_id: UUID, *, client_order_id: str = "cid-unknown") -> Order:
    """Return one UNKNOWN id-less entry, as submit records after an ambiguous create."""
    now = utc_now()
    return Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=uuid7(now),
        client_order_id=client_order_id,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        status=OrderStatus.UNKNOWN,
        created_at=now,
        updated_at=now,
        price=Decimal("100"),
        venue_order_id=None,
        product_id="BTC-USD",
    )


@pytest.mark.anyio
async def test_unconfirmed_submit_found_by_client_id_is_adopted() -> None:
    """A lookup hit adopts the venue id and status; the book is not paused by it."""
    store = InMemoryExecutionStore()
    deployment_id = uuid7(utc_now())
    order = _unknown_order(deployment_id)
    await _snapshot_with_order(store, order)
    broker = _RecoveringBroker(SubmitResult(status=OrderStatus.OPEN, venue_order_id="venue-1"))
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        result = await reconcile_open_orders(
            await store.get_deployment(deployment_id),
            broker=broker,
            store=store,
            product_id="BTC-USD",
        )
    adopted = next(item for item in result.orders if item.id == order.id)
    assert adopted.venue_order_id == "venue-1"
    assert adopted.status is OrderStatus.OPEN
    assert result.deployment.status is DeploymentStatus.RUNNING
    assert broker.lookups == [("cid-unknown", "BTC-USD", order.created_at)]
    events = await audit.list_recent(limit=5)
    assert [event.action for event in events] == ["unconfirmed_order_recovered"]


@pytest.mark.anyio
async def test_unconfirmed_submit_not_found_stays_unknown_and_audits_once() -> None:
    """No match keeps the order UNKNOWN, pauses fail-closed, and audits only once."""
    store = InMemoryExecutionStore()
    deployment_id = uuid7(utc_now())
    order = _unknown_order(deployment_id, client_order_id="cid-missing")
    await _snapshot_with_order(store, order)
    broker = _RecoveringBroker(None)
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        first = await reconcile_open_orders(
            await store.get_deployment(deployment_id),
            broker=broker,
            store=store,
            product_id="BTC-USD",
        )
        second = await reconcile_open_orders(
            first, broker=broker, store=store, product_id="BTC-USD"
        )
    assert second.deployment.status is DeploymentStatus.PAUSED
    detail = second.deployment.mismatch_detail or ""
    assert detail.startswith("Order submit is unconfirmed")
    assert "cid-missing" in detail
    still = next(item for item in second.orders if item.id == order.id)
    assert still.status is OrderStatus.UNKNOWN
    assert len(broker.lookups) == 2
    actions = [event.action for event in await audit.list_recent(limit=5)]
    assert actions == ["unconfirmed_order_not_found"]


@pytest.mark.anyio
async def test_incomplete_lookup_raises_instead_of_claiming_absence() -> None:
    """An incomplete lookup remains unknown with a fault, never claims venue absence."""

    class _BrokenLookup(_RecoveringBroker):
        """Lookup that fails mid-scan."""

        async def find_order_by_client_id(
            self,
            *,
            client_order_id: str,
            product_id: str,
            submitted_at: datetime,
        ) -> SubmitResult | None:
            """Raise like a pagination anomaly."""
            del client_order_id, product_id, submitted_at
            raise BrokerError("Coinbase client-order lookup exceeded the page limit.")

    store = InMemoryExecutionStore()
    deployment_id = uuid7(utc_now())
    order = _unknown_order(deployment_id)
    await _snapshot_with_order(store, order)
    await reconcile_open_orders(
        await store.get_deployment(deployment_id),
        broker=_BrokenLookup(None),
        store=store,
        product_id="BTC-USD",
    )
    current = await store.get_deployment(deployment_id)
    assert current.deployment.status is DeploymentStatus.PAUSED
    assert current.deployment.mismatch_detail == (
        "Order reconciliation is unconfirmed: order read failed."
    )
    assert next(item for item in current.orders if item.id == order.id).status is (
        OrderStatus.UNKNOWN
    )


def _candle() -> Candle:
    """Return one closed bar used to stamp the client order id."""
    starts = utc_now().replace(second=0, microsecond=0)
    return Candle(
        starts_at=starts,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


async def _submit(
    store: InMemoryExecutionStore, broker: _SubmitBroker, deployment_id: UUID
) -> Order:
    """Submit one live entry intent through the shared path."""
    return await submit_intent(
        store=store,
        broker=broker,
        deployment_id=deployment_id,
        product_id="BTC-USD",
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.01"),
        price=None,
        candle=_candle(),
    )


@pytest.mark.anyio
async def test_definite_rejection_is_terminal_audited_and_not_retried() -> None:
    """REJECTED persists with the venue reason, audits once, and places exactly once."""
    store = InMemoryExecutionStore()
    deployment_id = uuid7(utc_now())
    await _snapshot_with_order(store, _unknown_order(deployment_id))
    broker = _SubmitBroker(
        result=SubmitResult(
            status=OrderStatus.REJECTED,
            venue_order_id="cid",
            reject_reason="coinbase_http_400:INVALID_ARGUMENT",
        )
    )
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        order = await _submit(store, broker, deployment_id)
    assert order.status is OrderStatus.REJECTED
    assert order.reject_reason == "coinbase_http_400:INVALID_ARGUMENT"
    assert broker.placed == 1
    events = await audit.list_recent(limit=5)
    assert [event.action for event in events] == ["order_submit_rejected"]
    assert "coinbase_http_400" in events[0].detail


@pytest.mark.anyio
async def test_ambiguous_submit_is_unknown_audited_and_not_retried() -> None:
    """A BrokerError create stays UNKNOWN without a venue id and is never re-submitted."""
    store = InMemoryExecutionStore()
    deployment_id = uuid7(utc_now())
    await _snapshot_with_order(store, _unknown_order(deployment_id))
    broker = _SubmitBroker(error=BrokerError("Coinbase create-order request failed."))
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        order = await _submit(store, broker, deployment_id)
    assert order.status is OrderStatus.UNKNOWN
    assert order.venue_order_id is None
    assert broker.placed == 1
    events = await audit.list_recent(limit=5)
    assert [event.action for event in events] == ["order_submit_unconfirmed"]
    assert events[0].outcome.value == "failure"
