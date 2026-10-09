"""Discretionary protect and sell of coins already held at the venue (ADR 0124)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from tests.adoption_support import balance, balance_reader, live_book
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.adoption import (
    ADOPTION_BELOW_VENUE_MINIMUM,
    ADOPTION_BOOK_OCCUPIED,
    ADOPTION_LIVE_ONLY,
    AdoptionRequest,
    parse_adoption_request,
    preview_adoption,
)
from thytrader.execution.adoption_discretionary import adopt_held_inventory
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.broker import SubmitResult
from thytrader.execution.closed_windows import _closed_window_for
from thytrader.execution.paper import PaperBroker
from thytrader.execution.stopped import supervise_stopped_deployment
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.memory.store import InMemoryExperientialMemoryStore
from thytrader.risk.models import CapitalAllocation, compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.trading.adoption_write import ADOPTION_QUANTITY_UNAVAILABLE, AdoptionRefusedError
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.memory_adoption import InMemoryInventoryAdoptionStore
from thytrader.trading.models import (
    DeploymentKind,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    Fill,
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from thytrader.market_data.models import Candle
    from thytrader.trading.entry_latch import InhibitionSnapshot
    from thytrader.trading.models import Order

pytestmark = pytest.mark.anyio

_PRODUCT = "SOL-USD"
_WIDE = {
    "version": 2,
    "max_portfolio_exposure_fraction": "1",
    "per_product_max_exposure_fraction": "1",
}


@dataclass
class _Venue:
    """A live broker double: fills marketable orders at once, rests everything else."""

    placed: list[tuple[OrderKind, OrderSide, Decimal]] = field(default_factory=list)
    statuses: dict[str, OrderStatus] = field(default_factory=dict)
    fills: dict[str, Fill] = field(default_factory=dict)

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
        """Record the order; fill marketable ones at the given price."""
        del product_id, stop_trigger_price, take_profit_price
        self.placed.append((kind, side, quantity))
        if kind is OrderKind.MARKETABLE:
            self.statuses[client_order_id] = OrderStatus.FILLED
            self.fills[client_order_id] = Fill(
                id=uuid4(),
                deployment_id=uuid4(),
                order_id=uuid4(),
                venue_fill_id=f"venue-fill-{client_order_id}",
                price=price or Decimal(0),
                quantity=quantity,
                fee=Decimal("0.5"),
                filled_at=datetime.now(UTC),
                venue_order_id=client_order_id,
            )
            return SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id=client_order_id,
                filled_quantity=quantity,
                fill_price=price,
                fill_fee=Decimal("0.5"),
            )
        self.statuses[client_order_id] = OrderStatus.OPEN
        return SubmitResult(status=OrderStatus.OPEN, venue_order_id=client_order_id)

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Cancel immediately."""
        self.statuses[client_order_id] = OrderStatus.CANCELED
        return SubmitResult(status=OrderStatus.CANCELED, venue_order_id=venue_order_id)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Report what this double did; an adoption is never looked up."""
        if client_order_id.startswith("adopt:"):
            raise AssertionError("an adoption order was looked up at the venue")
        status = self.statuses.get(client_order_id, OrderStatus.OPEN)
        return SubmitResult(status=status, venue_order_id=venue_order_id)

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Live fills arrive through REST reconcile, as at Coinbase."""
        del product_id
        fill = self.fills.get(order_id or "")
        return () if fill is None else (fill,)

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live does not match candles."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Unused."""
        del product_id, side
        return mark

    def protective(self) -> list[OrderKind]:
        """Every protective order kind placed so far."""
        return [kind for kind, _side, _quantity in self.placed if kind is not OrderKind.MARKETABLE]


class _InhibitedLatch:
    """A fleet latch that inhibits every mode."""

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        """No lock is needed for a constant latch."""
        yield

    def raise_if_inhibited(self, mode: str, *, action: str) -> None:
        """Always refuse."""
        raise ExecutionConflictError(f"ENTRY_INHIBITED: {mode} {action} refused")

    async def read_inhibition(self) -> InhibitionSnapshot:
        """Not read by these tests."""
        raise AssertionError("unused")


@dataclass
class _Harness:
    """One memory-backed live account holding 0.5 SOL and 100000 USD."""

    store: InMemoryExecutionStore = field(default_factory=InMemoryExecutionStore)
    venue: _Venue = field(default_factory=_Venue)
    market: MarketDataService = field(default_factory=lambda: MarketDataService(DemoMarketData()))
    risk: InMemoryRiskPolicyStore = field(default_factory=InMemoryRiskPolicyStore)
    memory: InMemoryExperientialMemoryStore = field(default_factory=InMemoryExperientialMemoryStore)
    audit: InMemoryAuditEventStore = field(default_factory=InMemoryAuditEventStore)
    sol: str = "0.5"

    async def adopt(self, request: AdoptionRequest) -> DeploymentSnapshot:
        """Run one adoption with the audit scope bound."""
        with execution_audit_scope(self.audit):
            return await adopt_held_inventory(
                request,
                store=self.store,
                adoption_store=InMemoryInventoryAdoptionStore(self.store),
                market_data=self.market,
                broker=self.venue,
                read_balances=balance_reader(balance("SOL", self.sol), balance("USD", "100000")),
                live_quote_cash=Decimal(100000),
                risk_store=self.risk,
                memory_store=self.memory,
            )


async def _harness(**policy: object) -> _Harness:
    """A harness with a published wide live policy."""
    harness = _Harness()
    await harness.risk.publish(
        compiled_default_risk_policy().model_copy(update={**_WIDE, **policy})
    )
    return harness


def _protect(quantity: str = "0.3", key: str = "protect-1", **levels: str) -> AdoptionRequest:
    """Protect held SOL with a wide stop and take-profit."""
    return parse_adoption_request(
        mode="live",
        action="protect",
        product_id=_PRODUCT,
        quantity=quantity,
        idempotency_key=key,
        origin="human",
        timeframe="5m",
        stop_price=levels.get("stop", "50000"),
        take_profit_price=levels.get("target", "200000"),
        note="Took over SOL bought by hand.",
    )


def _sell(quantity: str = "all", key: str = "sell-1") -> AdoptionRequest:
    """Sell held SOL."""
    return parse_adoption_request(
        mode="live",
        action="sell",
        product_id=_PRODUCT,
        quantity=quantity,
        idempotency_key=key,
        origin="agent",
        timeframe="5m",
    )


async def test_protect_adopts_and_rests_the_bracket_like_a_live_fill() -> None:
    """A running discretionary long opens at the mark and gets one venue OCO; nothing is bought."""
    harness = await _harness()
    snapshot = await harness.adopt(_protect())
    deployment = snapshot.deployment
    assert deployment.kind is DeploymentKind.DISCRETIONARY
    assert deployment.status is DeploymentStatus.RUNNING
    assert deployment.performance_capital_quote is not None
    position = snapshot.position
    assert position is not None and position.quantity == Decimal("0.3")
    assert position.stop_price == Decimal(50000) and position.target_price == Decimal(200000)
    assert harness.venue.placed == [(OrderKind.TRIGGER_BRACKET, OrderSide.SELL, Decimal("0.3"))]
    assert [intent.purpose for intent in snapshot.intents] == [
        IntentPurpose.ADOPTION,
        IntentPurpose.BRACKET,
    ]
    records = await harness.memory.list_trade_reasons(deployment_id=deployment.id)
    adoption_record = next(item for item in records if item.purpose == "adoption")
    assert adoption_record.notes and adoption_record.risk.decision == "allow"
    events = [
        event
        for event in await harness.audit.list_recent(limit=50)
        if event.action == "inventory_adopted"
    ]
    assert len(events) == 1 and "balance_total=0.5" in events[0].detail
    assert "claimed=0" in events[0].detail and "mark=" in events[0].detail


async def test_protect_replays_its_idempotency_key_without_adopting_twice() -> None:
    """The same key returns the same book; no second adoption or bracket."""
    harness = await _harness()
    first = await harness.adopt(_protect())
    again = await harness.adopt(_protect())
    assert again.deployment.id == first.deployment.id
    assert len(harness.venue.placed) == 1
    assert [o.kind for o in again.orders].count(OrderKind.ADOPTION) == 1


async def test_protect_all_adopts_every_unmanaged_unit_and_reuses_a_flat_book() -> None:
    """'all' resolves under the claims; a flat running discretionary book is reused."""
    harness = await _harness()
    # A flat book left by an earlier live place-order: funded at its opening quote cash.
    funded = replace(
        live_book(product_id=_PRODUCT, kind=DeploymentKind.DISCRETIONARY),
        cash=Decimal(1000),
        initial_equity=Decimal(1000),
    )
    flat = await harness.store.create_deployment(funded)
    snapshot = await harness.adopt(_protect(quantity="all"))
    assert snapshot.deployment.id == flat.id
    assert snapshot.position is not None and snapshot.position.quantity == Decimal("0.5")
    assert len(harness.store.deployments) == 1


@pytest.mark.parametrize(
    ("quantity", "code"),
    [("0.9", ADOPTION_QUANTITY_UNAVAILABLE), ("0.001", ADOPTION_BELOW_VENUE_MINIMUM)],
)
async def test_protect_refuses_too_much_or_too_little(quantity: str, code: str) -> None:
    """Never more than the unmanaged base, never a lot the venue cannot protect."""
    harness = await _harness()
    with pytest.raises(AdoptionRefusedError) as refused:
        await harness.adopt(_protect(quantity=quantity))
    assert refused.value.code == code
    assert harness.venue.placed == [] and harness.store.intents == {}


async def test_protect_refuses_an_occupied_book_and_inverted_levels() -> None:
    """One occupied discretionary book per product; stop below and target above the mark."""
    harness = await _harness()
    await harness.adopt(_protect(quantity="0.2"))
    with pytest.raises(AdoptionRefusedError) as occupied:
        await harness.adopt(_protect(quantity="0.2", key="protect-2"))
    assert occupied.value.code == ADOPTION_BOOK_OCCUPIED
    fresh = await _harness()
    with pytest.raises(ExecutionConflictError, match="stop"):
        await fresh.adopt(_protect(stop="300000"))
    assert fresh.venue.placed == []


async def test_protect_keeps_the_entry_gate_membership() -> None:
    """With allocations in force, a discretionary adoption is denied like a discretionary entry."""
    harness = await _harness(
        allocations=(CapitalAllocation(strategy_id=_strategy_id(), allocated_quote="10"),)
    )
    with pytest.raises(ExecutionConflictError, match="DISCRETIONARY_NOT_ALLOCATED"):
        await harness.adopt(_protect())
    assert harness.store.deployments == {}


async def test_protect_is_allowed_under_fleet_disarm() -> None:
    """Protect only adds protection, so the disarmed fleet latch does not refuse it."""
    harness = await _harness()
    harness.store.bind_entry_gate(_InhibitedLatch())
    snapshot = await harness.adopt(_protect())
    assert snapshot.position is not None and harness.venue.protective() == [
        OrderKind.TRIGGER_BRACKET
    ]


async def test_a_foreign_idempotency_key_and_paper_are_refused() -> None:
    """A key that names another order conflicts; paper has no venue holdings."""
    with pytest.raises(AdoptionRefusedError) as paper:
        parse_adoption_request(
            mode="paper",
            action="sell",
            product_id=_PRODUCT,
            quantity="all",
            idempotency_key="k",
            origin="human",
            timeframe="5m",
        )
    assert paper.value.code == ADOPTION_LIVE_ONLY
    harness = await _harness()
    await harness.adopt(_protect())
    with pytest.raises(ExecutionConflictError, match="another order"):
        await harness.adopt(
            parse_adoption_request(
                mode="live",
                action="protect",
                product_id="BTC-USD",
                quantity="all",
                idempotency_key="protect-1",
                origin="human",
                timeframe="5m",
                stop_price="1",
                take_profit_price="300000",
            )
        )


async def test_sell_adopts_into_a_stopped_flatten_book_and_submits_nothing() -> None:
    """The API writes the book only; the worker's stopped-book flatten sells it."""
    harness = await _harness()
    snapshot = await harness.adopt(_sell())
    deployment = snapshot.deployment
    assert deployment.status is DeploymentStatus.STOPPED
    assert deployment.lifecycle_command is LifecycleCommand.FLATTEN
    position = snapshot.position
    assert position is not None and position.quantity == Decimal("0.5")
    assert position.stop_price == Decimal("0.01") and position.target_price is None
    assert harness.venue.placed == []
    records = await harness.memory.list_trade_reasons(deployment_id=deployment.id)
    assert [item.purpose for item in records] == ["adoption"]
    assert "Sell-holdings reduces risk" in records[0].risk.detail


async def test_the_worker_sells_the_book_flat_without_ever_protecting_it() -> None:
    """FLATTEN takes priority: one marketable sell, no bracket or stop, then flat and stopped."""
    harness = await _harness()
    adopted = await harness.adopt(_sell())
    for _cycle in range(3):
        await supervise_stopped_deployment(
            await harness.store.get_deployment(adopted.deployment.id),
            strategy=None,
            store=harness.store,
            market_data=harness.market,
            paper_broker=PaperBroker(),
            live_broker=harness.venue,
            load_closed_window=_closed_window_for,
        )
    assert harness.venue.placed == [(OrderKind.MARKETABLE, OrderSide.SELL, Decimal("0.5"))]
    assert harness.venue.protective() == []
    final = await harness.store.get_deployment(adopted.deployment.id)
    assert final.position is None and final.deployment.phase is RuntimePhase.FLAT
    assert final.deployment.status is DeploymentStatus.STOPPED
    sale = next(order for order in final.orders if order.kind is OrderKind.MARKETABLE)
    assert sale.status is OrderStatus.FILLED
    assert all(fill.economics_applied_at is not None for fill in final.fills)


async def test_sell_is_not_entry_gated_and_works_under_disarm() -> None:
    """Allocations, a narrow allowlist and a disarmed fleet do not block selling held coins."""
    harness = await _harness(
        product_allowlist=("BTC-USD",),
        allocations=(CapitalAllocation(strategy_id=_strategy_id(), allocated_quote="10"),),
    )
    harness.store.bind_entry_gate(_InhibitedLatch())
    snapshot = await harness.adopt(_sell(quantity="0.2"))
    assert snapshot.position is not None and snapshot.position.quantity == Decimal("0.2")


async def test_preview_reports_figures_and_blocking_reasons() -> None:
    """Read-only: balance, claims, adoptable and why protect is blocked once a book is busy."""
    harness = await _harness()
    reader = balance_reader(balance("SOL", "0.5"), balance("USD", "100000"))
    clear = await preview_adoption(
        store=harness.store,
        market_data=harness.market,
        read_balances=reader,
        product_id=_PRODUCT,
        timeframe="5m",
    )
    assert clear.availability is not None and clear.availability.adoptable == Decimal("0.5")
    assert clear.mark is not None and clear.protect_blocking_reasons == ()
    await harness.adopt(_protect(quantity="0.2"))
    busy = await preview_adoption(
        store=harness.store,
        market_data=harness.market,
        read_balances=reader,
        product_id=_PRODUCT,
        timeframe="5m",
    )
    assert busy.availability is not None and busy.availability.adoptable == Decimal("0.3")
    assert busy.protect_blocking_reasons[0].startswith(ADOPTION_BOOK_OCCUPIED)
    assert busy.sell_blocking_reasons == ()
    blind = await preview_adoption(
        store=harness.store,
        market_data=harness.market,
        read_balances=None,
        product_id=_PRODUCT,
        timeframe="5m",
    )
    assert blind.availability is None and blind.sell_blocking_reasons


def _strategy_id() -> UUID:
    """Some listed strategy, so allocations are in force."""
    return UUID(int=77)
