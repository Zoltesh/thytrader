"""On-demand longs persist intent, pass risk, and never retry ambiguous timeouts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.execution.broker import SubmitResult
from thytrader.execution.discretionary import (
    DiscretionaryOrderRequest,
    parse_discretionary_request,
    place_discretionary_order,
    process_discretionary_bar,
)
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    DeploymentKind,
    DeploymentStatus,
    ExecutionConflictError,
    Fill,
    IntentOrigin,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import CapitalAllocation, compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore


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


def _request(
    *,
    entry_kind: str = "marketable",
    idempotency_key: str = "disc-1",
    origin: str = "agent",
    limit_price: str | None = None,
    paper_maker_fee_rate: str | None = None,
    paper_taker_fee_rate: str | None = None,
) -> DiscretionaryOrderRequest:
    """Build one valid paper long request."""
    return parse_discretionary_request(
        mode="paper",
        product_id="BTC-USD",
        entry_kind=entry_kind,
        stop_price="50000",
        take_profit_price="200000",
        origin=origin,
        idempotency_key=idempotency_key,
        timeframe="5m",
        quantity="0.01",
        limit_price=limit_price,
        paper_starting_cash="10000",
        paper_maker_fee_rate=paper_maker_fee_rate,
        paper_taker_fee_rate=paper_taker_fee_rate,
    )


@dataclass
class _TimeoutBroker:
    """Raise TimeoutError on place_order and never invent a venue id."""

    place_calls: int = 0

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
        """Count the submit then fail as an ambiguous timeout."""
        del (
            client_order_id,
            product_id,
            side,
            kind,
            quantity,
            price,
            stop_trigger_price,
            take_profit_price,
        )
        self.place_calls += 1
        raise TimeoutError("simulated timeout")

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Unused."""
        del venue_order_id, client_order_id
        raise AssertionError("cancel_order should not run")

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Leave the order unconfirmed."""
        del venue_order_id
        return SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id=client_order_id)

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """No remote fills."""
        del product_id, order_id
        return ()

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """No candle matching during timeout tests."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Unused."""
        del product_id, side
        return mark


@dataclass
class _LiveFillBroker:
    """Fill the entry immediately, then rest a venue OCO without candle matching."""

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
        """Record kind; fill entries and rest brackets."""
        del product_id, side, stop_trigger_price, take_profit_price
        self.placed.append(kind)
        if kind is OrderKind.TRIGGER_BRACKET:
            return SubmitResult(status=OrderStatus.OPEN, venue_order_id=client_order_id)
        if price is None:
            raise AssertionError("live entry tests require a mark price")
        return SubmitResult(
            status=OrderStatus.FILLED,
            venue_order_id=client_order_id,
            filled_quantity=quantity,
            fill_price=price,
        )

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Cancel is unused in the happy path."""
        del client_order_id
        return SubmitResult(status=OrderStatus.CANCELED, venue_order_id=venue_order_id)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Unused."""
        del venue_order_id, client_order_id
        raise AssertionError("get_order should not run")

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Immediate fill is persisted by submit_intent."""
        del product_id, order_id
        return ()

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


@dataclass
class _TimeoutThenObservedBroker:
    """Model an ambiguous POST acknowledgement, then a scripted reconcile observation.

    ``place_order`` returns ``UNKNOWN`` with a known venue id (the post-F36 shape:
    the POST was accepted, only the follow-up observation is ambiguous). Reconcile
    then calls ``get_order`` and ``list_fills`` exactly once each per submission,
    returning the scripted status, filled quantity, and remote fills.
    """

    get_status: OrderStatus
    filled_quantity: Decimal
    fills: tuple[Fill, ...] = ()
    place_calls: int = 0
    get_calls: int = 0
    list_fills_calls: int = 0

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
        """Accept the order at the venue but report the follow-up observation as ambiguous."""
        del product_id, side, kind, quantity, price, stop_trigger_price, take_profit_price
        self.place_calls += 1
        return SubmitResult(
            status=OrderStatus.UNKNOWN,
            venue_order_id=f"venue-{client_order_id}",
            reject_reason="order_observation_failed",
        )

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Unused: these tests never cancel."""
        del venue_order_id, client_order_id
        raise AssertionError("cancel_order should not run")

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Return the scripted reconcile observation for the known venue id."""
        del client_order_id
        self.get_calls += 1
        return SubmitResult(
            status=self.get_status,
            venue_order_id=venue_order_id,
            filled_quantity=self.filled_quantity,
        )

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Return the scripted remote fills."""
        del product_id, order_id
        self.list_fills_calls += 1
        return self.fills

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


def _live_entry_request(idempotency_key: str) -> DiscretionaryOrderRequest:
    """Build one valid live long request sized to exactly 0.01 BTC."""
    return parse_discretionary_request(
        mode="live",
        product_id="BTC-USD",
        entry_kind="marketable",
        stop_price="50000",
        take_profit_price="200000",
        origin="agent",
        idempotency_key=idempotency_key,
        timeframe="5m",
        quantity="0.01",
    )


def _remote_fill(venue_fill_id: str, *, quantity: Decimal, price: Decimal, fee: Decimal) -> Fill:
    """Build one remote fill row as ``broker.list_fills`` would report it.

    ``order_id``/``deployment_id`` are placeholders: reconcile rebuilds the local
    fill against the exact local order, using only price/quantity/fee/filled_at
    from this remote row.
    """
    return Fill(
        id=uuid4(),
        deployment_id=uuid4(),
        order_id=uuid4(),
        venue_fill_id=venue_fill_id,
        price=price,
        quantity=quantity,
        fee=fee,
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_parse_rejects_illegal_combinations() -> None:
    """Quantity xor notional, maker limit, and human/agent origin are required."""
    with pytest.raises(ExecutionConflictError, match="exactly one"):
        parse_discretionary_request(
            mode="paper",
            product_id="BTC-USD",
            entry_kind="marketable",
            stop_price="1",
            take_profit_price="3",
            origin="agent",
            idempotency_key="k",
            quantity="1",
            quote_notional="1",
            paper_starting_cash="10000",
        )
    with pytest.raises(ExecutionConflictError, match="limit_price"):
        parse_discretionary_request(
            mode="paper",
            product_id="BTC-USD",
            entry_kind="post_only_limit",
            stop_price="1",
            take_profit_price="3",
            origin="agent",
            idempotency_key="k",
            quantity="0.01",
            paper_starting_cash="10000",
        )
    with pytest.raises(ExecutionConflictError, match="origin"):
        parse_discretionary_request(
            mode="paper",
            product_id="BTC-USD",
            entry_kind="marketable",
            stop_price="1",
            take_profit_price="3",
            origin="runtime",
            idempotency_key="k",
            quantity="0.01",
            paper_starting_cash="10000",
        )


def test_parse_accepts_ingested_venue_clocks() -> None:
    """Discretionary books accept every ingested venue clock, including 1m."""
    request = parse_discretionary_request(
        mode="paper",
        product_id="BTC-USD",
        entry_kind="marketable",
        stop_price="50000",
        take_profit_price="200000",
        origin="agent",
        idempotency_key="disc-1m",
        timeframe="1m",
        quantity="0.01",
        paper_starting_cash="10000",
    )
    assert request.timeframe == "1m"


def test_parse_rejects_unknown_clock() -> None:
    """A non-venue timeframe is not a discretionary book clock."""
    with pytest.raises(ExecutionConflictError, match="ingested venue timeframe"):
        parse_discretionary_request(
            mode="paper",
            product_id="BTC-USD",
            entry_kind="marketable",
            stop_price="1",
            take_profit_price="3",
            origin="agent",
            idempotency_key="k",
            timeframe="3h",
            quantity="0.01",
            paper_starting_cash="10000",
        )


@pytest.mark.anyio
async def test_intent_persists_before_submit_with_unique_client_id() -> None:
    """The order intent is durable before the broker is called, with a unique client id."""
    store = InMemoryExecutionStore()
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=_request(),
        live_allowed=False,
    )
    assert snapshot.deployment.kind is DeploymentKind.DISCRETIONARY
    assert snapshot.intents
    intent = snapshot.intents[0]
    assert intent.origin is IntentOrigin.AGENT
    assert intent.idempotency_key == "disc-1"
    assert intent.client_order_id
    assert snapshot.orders[0].client_order_id == intent.client_order_id
    assert ":" in intent.client_order_id


@pytest.mark.anyio
async def test_timeout_reconciles_and_idempotent_retry_does_not_place_again() -> None:
    """A timeout persists unknown, reconciles, pauses, and retries do not place again."""
    store = InMemoryExecutionStore()
    broker = _TimeoutBroker()
    market_data = MarketDataService(DemoMarketData())
    first = await place_discretionary_order(
        store=store,
        broker=broker,
        market_data=market_data,
        request=_request(idempotency_key="timeout-1"),
        live_allowed=False,
    )
    assert broker.place_calls == 1
    assert first.deployment.status is DeploymentStatus.PAUSED
    assert any(order.status is OrderStatus.UNKNOWN for order in first.orders)
    second = await place_discretionary_order(
        store=store,
        broker=broker,
        market_data=market_data,
        request=_request(idempotency_key="timeout-1"),
        live_allowed=False,
    )
    assert broker.place_calls == 1
    assert second.deployment.id == first.deployment.id


@pytest.mark.anyio
async def test_allocations_deny_before_intent_persist() -> None:
    """Nonempty allocations are a strategy allowlist; discretionary is fail-closed."""
    store = InMemoryExecutionStore()
    risk = InMemoryRiskPolicyStore()
    await risk.publish(
        compiled_default_risk_policy().model_copy(
            update={
                "allocations": (CapitalAllocation(strategy_id=uuid4(), allocated_quote="10000"),)
            }
        )
    )
    with pytest.raises(ExecutionConflictError, match="allocations"):
        await place_discretionary_order(
            store=store,
            broker=PaperBroker(),
            market_data=MarketDataService(DemoMarketData()),
            request=_request(),
            live_allowed=False,
            risk_store=risk,
        )
    assert not store.intents
    assert not store.deployments


@pytest.mark.anyio
async def test_paper_fill_rests_synthetic_take_profit_not_trigger_bracket() -> None:
    """Paper marketable entries fill, then rest a post-only take-profit."""
    store = InMemoryExecutionStore()
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=_request(),
        live_allowed=False,
    )
    assert snapshot.deployment.phase in {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}
    assert snapshot.position is not None
    kinds = {order.kind for order in snapshot.orders}
    assert OrderKind.TRIGGER_BRACKET not in kinds
    assert OrderKind.POST_ONLY_LIMIT in kinds or snapshot.deployment.phase is RuntimePhase.OPEN


@pytest.mark.anyio
async def test_paper_stop_fires_marketable_exit_on_closed_bar() -> None:
    """A closed bar through the stop cancels the TP and sells marketable."""
    store = InMemoryExecutionStore()
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=_request(),
        live_allowed=False,
    )
    position = snapshot.position
    assert position is not None
    start = datetime(2026, 9, 15, 12, tzinfo=UTC)
    crash = Candle(
        starts_at=start,
        open=position.entry_price,
        high=position.entry_price,
        low=position.stop_price - Decimal("1"),
        close=position.stop_price - Decimal("1"),
        volume=Decimal("10"),
    )
    after = await process_discretionary_bar(
        snapshot,
        product=_product(),
        candles=(crash,),
        broker=PaperBroker(),
        store=store,
    )
    assert after.position is None
    assert after.deployment.phase is RuntimePhase.FLAT
    assert any(
        order.side is OrderSide.SELL and order.kind is OrderKind.MARKETABLE
        for order in after.orders
    )


@pytest.mark.anyio
async def test_live_fill_attaches_bracket_without_second_oco() -> None:
    """Live discretionary entries attach SL/TP; they do not rest a second OCO."""
    store = InMemoryExecutionStore()
    broker = _LiveFillBroker()
    snapshot = await place_discretionary_order(
        store=store,
        broker=broker,
        market_data=MarketDataService(DemoMarketData()),
        request=parse_discretionary_request(
            mode="live",
            product_id="BTC-USD",
            entry_kind="marketable",
            stop_price="50000",
            take_profit_price="200000",
            origin="human",
            idempotency_key="live-1",
            timeframe="5m",
            quantity="0.01",
        ),
        live_allowed=True,
        live_quote_cash=Decimal("20000"),
    )
    assert OrderKind.TRIGGER_BRACKET not in broker.placed
    assert all(order.kind is not OrderKind.TRIGGER_BRACKET for order in snapshot.orders)
    assert snapshot.deployment.strategy_id is None
    assert snapshot.deployment.strategy_fingerprint is None
    assert snapshot.orders[0].take_profit_price is not None
    assert snapshot.orders[0].stop_trigger_price is not None


@pytest.mark.anyio
async def test_paper_short_sells_to_open_and_buys_to_cover() -> None:
    """A paper short credits a sell entry and covers with a marketable buy."""
    store = InMemoryExecutionStore()
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=parse_discretionary_request(
            mode="paper",
            product_id="BTC-USD",
            entry_kind="marketable",
            side="short",
            stop_price="200000",
            take_profit_price="50000",
            origin="agent",
            idempotency_key="short-1",
            timeframe="5m",
            quantity="0.01",
            paper_starting_cash="10000",
        ),
        live_allowed=False,
    )
    position = snapshot.position
    assert position is not None
    assert position.side.value == "short"
    assert any(order.side is OrderSide.SELL for order in snapshot.orders)
    start = datetime(2026, 9, 15, 12, tzinfo=UTC)
    spike = Candle(
        starts_at=start,
        open=position.entry_price,
        high=position.stop_price + Decimal("1"),
        low=position.entry_price,
        close=position.stop_price + Decimal("1"),
        volume=Decimal("10"),
    )
    after = await process_discretionary_bar(
        snapshot,
        product=_product(),
        candles=(spike,),
        broker=PaperBroker(),
        store=store,
    )
    assert after.position is None
    assert any(
        order.side is OrderSide.BUY and order.kind is OrderKind.MARKETABLE for order in after.orders
    )


@pytest.mark.anyio
async def test_live_short_fails_closed_without_base() -> None:
    """Spot shorts do not borrow; missing live base inventory is a conflict."""
    store = InMemoryExecutionStore()
    with pytest.raises(ExecutionConflictError, match="INSUFFICIENT_BASE_FOR_SPOT_SHORT"):
        await place_discretionary_order(
            store=store,
            broker=_LiveFillBroker(),
            market_data=MarketDataService(DemoMarketData()),
            request=parse_discretionary_request(
                mode="live",
                product_id="BTC-USD",
                entry_kind="marketable",
                side="short",
                stop_price="200000",
                take_profit_price="50000",
                origin="human",
                idempotency_key="short-live-1",
                timeframe="5m",
                quantity="0.01",
            ),
            live_allowed=True,
            live_quote_cash=Decimal("20000"),
            live_base_available=None,
        )
    assert not store.intents


def test_parse_paper_fee_rates_stay_optional_and_reject_live() -> None:
    """Omitted paper rates stay unset; supplied rates validate; live rejects them."""
    omitted = _request()
    assert omitted.paper_maker_fee_rate is None
    assert omitted.paper_taker_fee_rate is None
    custom = _request(paper_maker_fee_rate="0.0025", paper_taker_fee_rate="0.004")
    assert custom.paper_maker_fee_rate == Decimal("0.0025")
    assert custom.paper_taker_fee_rate == Decimal("0.004")
    with pytest.raises(ExecutionConflictError, match="both maker_fee_rate"):
        _request(paper_maker_fee_rate="0.0025")
    with pytest.raises(ExecutionConflictError, match="Live deployments"):
        parse_discretionary_request(
            mode="live",
            product_id="BTC-USD",
            entry_kind="marketable",
            stop_price="50000",
            take_profit_price="200000",
            origin="human",
            idempotency_key="live-fees",
            timeframe="5m",
            quantity="0.01",
            paper_maker_fee_rate="0.001",
            paper_taker_fee_rate="0.002",
        )


@pytest.mark.anyio
async def test_paper_discretionary_fill_uses_book_taker_rate() -> None:
    """A new paper book defaults omitted rates and charges them on marketable fills."""
    store = InMemoryExecutionStore()
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=_request(paper_maker_fee_rate="0.0025", paper_taker_fee_rate="0.004"),
        live_allowed=False,
    )
    assert snapshot.deployment.paper_maker_fee_rate == Decimal("0.0025")
    assert snapshot.deployment.paper_taker_fee_rate == Decimal("0.004")
    assert snapshot.fills
    fill = snapshot.fills[0]
    assert fill.fee == fill.price * fill.quantity * Decimal("0.004")


@pytest.mark.anyio
async def test_reused_paper_book_rejects_different_fee_rates() -> None:
    """A second ticket cannot silently change stored paper fee assumptions."""
    store = InMemoryExecutionStore()
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=_request(),
        live_allowed=False,
    )
    position = snapshot.position
    assert position is not None
    crash = Candle(
        starts_at=datetime(2026, 9, 15, 12, tzinfo=UTC),
        open=position.entry_price,
        high=position.entry_price,
        low=position.stop_price - Decimal("1"),
        close=position.stop_price - Decimal("1"),
        volume=Decimal("10"),
    )
    flat = await process_discretionary_bar(
        snapshot,
        product=_product(),
        candles=(crash,),
        broker=PaperBroker(),
        store=store,
    )
    assert flat.deployment.phase is RuntimePhase.FLAT
    with pytest.raises(ExecutionConflictError, match="fixed"):
        await place_discretionary_order(
            store=store,
            broker=PaperBroker(),
            market_data=MarketDataService(DemoMarketData()),
            request=_request(
                idempotency_key="disc-2",
                paper_maker_fee_rate="0.0025",
                paper_taker_fee_rate="0.004",
            ),
            live_allowed=False,
        )


@pytest.mark.anyio
async def test_timeout_then_found_open_does_not_apply_a_fill_or_repost() -> None:
    """F03: timeout -> found OPEN pauses without inventing a fill or re-submitting."""
    store = InMemoryExecutionStore()
    broker = _TimeoutThenObservedBroker(get_status=OrderStatus.OPEN, filled_quantity=Decimal("0"))
    snapshot = await place_discretionary_order(
        store=store,
        broker=broker,
        market_data=MarketDataService(DemoMarketData()),
        request=_live_entry_request("f03-open"),
        live_allowed=True,
        live_quote_cash=Decimal("10000"),
    )
    assert broker.place_calls == 1
    assert broker.get_calls == 1
    # Reconcile always looks for fills once an order is watched, but an OPEN order
    # with none reported must apply nothing and must not repost.
    assert broker.list_fills_calls == 1
    assert snapshot.position is None
    assert snapshot.deployment.cash == Decimal("10000")
    assert not snapshot.fills


@pytest.mark.anyio
async def test_timeout_then_partial_fill_applies_exactly_once() -> None:
    """F03: timeout -> partially filled applies only the reconciled partial quantity."""
    store = InMemoryExecutionStore()
    partial_fill = _remote_fill(
        "partial-1", quantity=Decimal("0.004"), price=Decimal("100000"), fee=Decimal("0.4")
    )
    broker = _TimeoutThenObservedBroker(
        get_status=OrderStatus.OPEN,
        filled_quantity=Decimal("0.004"),
        fills=(partial_fill,),
    )
    snapshot = await place_discretionary_order(
        store=store,
        broker=broker,
        market_data=MarketDataService(DemoMarketData()),
        request=_live_entry_request("f03-partial"),
        live_allowed=True,
        live_quote_cash=Decimal("10000"),
    )
    assert broker.get_calls == 1
    assert broker.list_fills_calls == 1
    assert snapshot.position is not None
    assert snapshot.position.quantity == Decimal("0.004")
    expected_cash = Decimal("10000") - (Decimal("100000") * Decimal("0.004")) - Decimal("0.4")
    assert snapshot.deployment.cash == expected_cash
    assert len(snapshot.fills) == 1


@pytest.mark.anyio
async def test_timeout_then_filled_applies_the_fill_exactly_once() -> None:
    """F03: timeout -> FILLED must not double-apply, reproducing the audit's exact numbers.

    The pre-fix code reconciled the remote fill (applying it once), then
    unconditionally searched the book for "any FILLED order" and re-applied
    whatever fill it found: one 0.01-unit fill at 100,000 with a 1-unit fee
    produced a 0.02-unit position and cash 7,998 instead of 8,999. The fix scopes
    post-submit processing to the exact order and applies through the idempotent
    ledger, so a second look at the same order is a verified no-op.
    """
    store = InMemoryExecutionStore()
    fill = _remote_fill(
        "fill-1", quantity=Decimal("0.01"), price=Decimal("100000"), fee=Decimal("1")
    )
    broker = _TimeoutThenObservedBroker(
        get_status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.01"),
        fills=(fill,),
    )
    snapshot = await place_discretionary_order(
        store=store,
        broker=broker,
        market_data=MarketDataService(DemoMarketData()),
        request=_live_entry_request("f03-filled"),
        live_allowed=True,
        live_quote_cash=Decimal("10000"),
    )
    assert broker.place_calls == 1
    assert broker.get_calls == 1
    assert broker.list_fills_calls == 1
    assert snapshot.position is not None
    assert snapshot.position.quantity == Decimal("0.01")
    assert snapshot.deployment.cash == Decimal("8999")
    assert len(snapshot.fills) == 1


@pytest.mark.anyio
async def test_timeout_then_filled_repeated_retry_does_not_reobserve_or_reapply() -> None:
    """F03: a repeated call after the submission already resolved does not re-reconcile.

    The idempotency key alone (not a second reconcile pass) must short-circuit a
    retried API call once the intent's outcome is durable.
    """
    store = InMemoryExecutionStore()
    fill = _remote_fill(
        "fill-retry-1", quantity=Decimal("0.01"), price=Decimal("100000"), fee=Decimal("1")
    )
    broker = _TimeoutThenObservedBroker(
        get_status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.01"),
        fills=(fill,),
    )
    market_data = MarketDataService(DemoMarketData())
    request = _live_entry_request("f03-retry")
    first = await place_discretionary_order(
        store=store,
        broker=broker,
        market_data=market_data,
        request=request,
        live_allowed=True,
        live_quote_cash=Decimal("10000"),
    )
    assert broker.place_calls == 1
    assert broker.get_calls == 1
    second = await place_discretionary_order(
        store=store,
        broker=broker,
        market_data=market_data,
        request=request,
        live_allowed=True,
        live_quote_cash=Decimal("10000"),
    )
    assert broker.place_calls == 1
    assert broker.get_calls == 1
    assert broker.list_fills_calls == 1
    assert second.deployment.id == first.deployment.id
    assert second.deployment.cash == first.deployment.cash
    assert second.position is not None
    assert second.position.quantity == first.position.quantity if first.position else True


@pytest.mark.anyio
async def test_new_entry_after_an_older_filled_order_applies_only_its_own_fill() -> None:
    """F03: post-submit fill application scopes to the exact new order, not any FILLED one.

    The pre-fix code searched ``current.orders`` for "any FILLED order" instead of
    the order this submission produced. A stale, already-fully-applied FILLED
    order from an earlier entry+exit cycle in the same discretionary book must
    never be picked instead of (or in addition to) the new order's own fill.
    """
    store = InMemoryExecutionStore()
    market_data = MarketDataService(DemoMarketData())
    opened = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=market_data,
        request=_request(idempotency_key="older-1"),
        live_allowed=False,
    )
    assert opened.position is not None
    start = datetime(2026, 9, 15, 12, tzinfo=UTC)
    crash = Candle(
        starts_at=start,
        open=opened.position.entry_price,
        high=opened.position.entry_price,
        low=opened.position.stop_price - Decimal("1"),
        close=opened.position.stop_price - Decimal("1"),
        volume=Decimal("10"),
    )
    closed = await process_discretionary_bar(
        opened,
        product=_product(),
        candles=(crash,),
        broker=PaperBroker(),
        store=store,
    )
    assert closed.position is None
    old_fill_ids = {item.id for item in closed.fills}
    assert len(old_fill_ids) >= 2

    reopened = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=market_data,
        request=_request(idempotency_key="older-2"),
        live_allowed=False,
    )
    assert reopened.position is not None
    assert reopened.position.quantity == Decimal("0.01")
    new_fills = [item for item in reopened.fills if item.id not in old_fill_ids]
    assert len(new_fills) == 1
    assert reopened.position.entry_price == new_fills[0].price
    # Every previously-applied fill is still present and untouched: no re-application.
    assert old_fill_ids.issubset({item.id for item in reopened.fills})
