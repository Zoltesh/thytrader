"""Stopped-book supervision must not drop protection or skip sibling orders."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest

from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.exit_guards import CANCEL_BEFORE_EXIT_DETAIL
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.loop import (
    FLATTEN_AWAITING_EXECUTABLE_CONTEXT,
    flatten_stopped_residual,
)
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    LifecycleCommand,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import (
    FILLED_WITHOUT_REST_FILLS_DETAIL,
    reconcile_open_orders,
)
from thytrader.execution.stopped import supervise_stopped_deployment
from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleQualityReport,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.market_data.service import MarketDataService

_QTY = Decimal("0.01")
_STOP = Decimal("90")
_TARGET = Decimal("120")
_WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def _product(product_id: str = "BTC-USD") -> MarketProduct:
    """Return venue increments for one USD spot product."""
    base, _dash, quote = product_id.partition("-")
    return MarketProduct(
        product_id=product_id,
        base_currency=base,
        quote_currency=quote,
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.0001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    )


def _strategy() -> StrategyDefinition:
    """Return the reference template strategy."""
    draft = create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC))
    return StrategyDefinition.model_validate(draft.model_dump(mode="python"))


def _candle(*, close: str = "101") -> Candle:
    """Return the most recent closed hourly candle with a real traded close."""
    price = Decimal(close)
    return Candle(
        starts_at=CandleInterval.ONE_HOUR.align_closed_end(utc_now()) - timedelta(hours=1),
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("10"),
    )


@dataclass
class _Venue:
    """Live broker double that records every read and refuses unscripted creates."""

    statuses: dict[str, list[OrderStatus]] = field(default_factory=dict)
    fills: dict[str, list[tuple[Fill, ...]]] = field(default_factory=dict)
    cancel_error: set[str] = field(default_factory=set)
    get_error: set[str] = field(default_factory=set)
    fills_error: set[str] = field(default_factory=set)
    attached: dict[str, str] = field(default_factory=dict)
    events: list[tuple[str, str]] = field(default_factory=list)
    placed: list[tuple[str, OrderKind, Decimal | None]] = field(default_factory=list)

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
        """Record one create. Marketable exits fill; anything else stays open."""
        del client_order_id, side, quantity, stop_trigger_price, take_profit_price
        self.placed.append((product_id, kind, price))
        venue_id = f"placed-{len(self.placed)}"
        self.events.append(("place", venue_id))
        if kind is OrderKind.MARKETABLE:
            return SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id=venue_id,
                filled_quantity=_QTY,
                fill_price=price,
            )
        return SubmitResult(status=OrderStatus.OPEN, venue_order_id=venue_id)

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Cancel unless this id is scripted as an ambiguous failure."""
        del client_order_id
        self.events.append(("cancel", venue_order_id))
        if venue_order_id in self.cancel_error:
            raise BrokerError("cancel outcome is unknown")
        self.statuses[venue_order_id] = [OrderStatus.CANCELED]
        return SubmitResult(status=OrderStatus.CANCELED, venue_order_id=venue_order_id)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Pop the next scripted status, repeating the last one."""
        del client_order_id
        self.events.append(("get", venue_order_id))
        if venue_order_id in self.get_error:
            raise BrokerError("untrusted read error payload")
        queue = self.statuses.setdefault(venue_order_id, [OrderStatus.OPEN])
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        return SubmitResult(
            status=status,
            venue_order_id=venue_order_id,
            attached_child_venue_order_id=self.attached.get(venue_order_id),
        )

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Pop the next scripted fill page, repeating the last page."""
        del product_id
        self.events.append(("fills", order_id or ""))
        if order_id in self.fills_error:
            raise BrokerError("untrusted fill error payload")
        pages = self.fills.get(order_id or "", [])
        if pages:
            return pages.pop(0) if len(pages) > 1 else pages[0]
        if order_id and order_id.startswith("placed-"):
            return (_remote_fill(order_id),)
        return ()

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live books do not candle-match."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Unused by these supervision tests."""
        del product_id, side
        return mark


class _RecordingPaper(PaperBroker):
    """Paper broker that records the product and price of each create."""

    def __init__(self) -> None:
        """Start with no recorded creates."""
        super().__init__()
        self.placed: list[tuple[str, OrderKind, Decimal | None]] = []

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
        """Record the create, then use the paper fill rules."""
        self.placed.append((product_id, kind, price))
        return await super().place_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            kind=kind,
            quantity=quantity,
            price=price,
            stop_trigger_price=stop_trigger_price,
            take_profit_price=take_profit_price,
        )


class _EmptyPreview:
    """Market-data double whose preview has no candles."""

    async def get_preview(self, product_id: str, interval: CandleInterval) -> MarketDataPreview:
        """Return an enabled product and no candles."""
        return _preview(product_id, interval, ())


class _PricedPreview(_EmptyPreview):
    """Preview that exposes one provider closed candle."""

    def __init__(self, candle: Candle) -> None:
        """Bind the only verified close."""
        self._candle = candle

    async def get_preview(self, product_id: str, interval: CandleInterval) -> MarketDataPreview:
        """Return that provider candle."""
        return _preview(product_id, interval, (self._candle,))


def _preview(
    product_id: str, interval: CandleInterval, candles: tuple[Candle, ...]
) -> MarketDataPreview:
    """Build a preview without synthesizing missing bars."""
    latest = candles[-1].starts_at if candles else None
    quality = CandleQualityReport(
        candles=candles,
        candle_count=len(candles),
        gap_count=0,
        missing_intervals=0,
        latest_completed_at=latest,
        is_stale=False,
    )
    return MarketDataPreview(
        product=_product(product_id), interval=interval, as_of=_WHEN, quality=quality
    )


def _as_market_data(preview: _EmptyPreview) -> MarketDataService:
    """Adapt a preview double to the service parameter without calling a venue."""
    return cast("MarketDataService", preview)


async def _windows(
    market_data: object,
    *,
    product_id: str,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
    candles_by_product: dict[str, tuple[Candle, ...]],
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Return scripted closed candles and never fill a gap."""
    del market_data, timeframe, warmup_bars, deploy_anchor
    return _product(product_id), candles_by_product.get(product_id, ()), _candle().starts_at


async def _deployment(
    store: InMemoryExecutionStore,
    *,
    mode: DeploymentMode = DeploymentMode.LIVE,
    kind: DeploymentKind = DeploymentKind.STRATEGY,
    command: LifecycleCommand = LifecycleCommand.FLATTEN,
    status: DeploymentStatus = DeploymentStatus.STOPPED,
    detail: str | None = None,
    product_id: str = "BTC-USD",
) -> Deployment:
    """Insert one deployment row."""
    strategy = _strategy()
    now = utc_now()
    fingerprint = None if kind is DeploymentKind.DISCRETIONARY else strategy_fingerprint(strategy)
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=fingerprint,
        strategy_id=None if kind is DeploymentKind.DISCRETIONARY else strategy.strategy_id,
        product_id=product_id,
        mode=mode,
        status=status,
        cash=Decimal("10000"),
        phase=RuntimePhase.OPEN,
        created_at=now,
        updated_at=now,
        kind=kind,
        timeframe="1h",
        lifecycle_command=command,
        mismatch_detail=detail,
    )
    await store.create_deployment(deployment)
    return deployment


async def _position(store: InMemoryExecutionStore, deployment_id: UUID, product_id: str) -> None:
    """Persist one open long book."""
    await store.save_position(
        Position(
            deployment_id=deployment_id,
            quantity=_QTY,
            entry_price=Decimal("100"),
            stop_price=_STOP,
            target_price=_TARGET,
            entered_bar=_WHEN,
            updated_at=utc_now(),
            side=PositionSide.LONG,
            product_id=product_id,
        ),
        deployment_id=deployment_id,
        product_id=product_id,
    )


async def _order(
    store: InMemoryExecutionStore,
    deployment_id: UUID,
    *,
    venue_order_id: str,
    product_id: str,
    kind: OrderKind = OrderKind.TRIGGER_BRACKET,
    side: OrderSide = OrderSide.SELL,
    status: OrderStatus = OrderStatus.OPEN,
    purpose: IntentPurpose = IntentPurpose.BRACKET,
    attached_child: str | None = None,
) -> Order:
    """Persist one intent-backed order."""
    now = utc_now()
    intent = OrderIntent(
        id=uuid7(now),
        deployment_id=deployment_id,
        client_order_id=f"intent-{venue_order_id or 'unknown'}",
        purpose=purpose,
        side=side,
        kind=kind,
        quantity=_QTY,
        created_at=now,
        candle_starts_at=_WHEN,
        price=_TARGET,
        product_id=product_id,
    )
    await store.save_intent(intent)
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=intent.id,
        client_order_id=f"order-{venue_order_id or 'unknown'}",
        side=side,
        kind=kind,
        quantity=_QTY,
        price=_TARGET,
        status=status,
        created_at=now,
        updated_at=now,
        venue_order_id=venue_order_id or None,
        product_id=product_id,
        attached_child_venue_order_id=attached_child,
        stop_trigger_price=_STOP,
        take_profit_price=_TARGET,
    )
    await store.save_order(order)
    return order


def _remote_fill(venue_order_id: str, *, price: str = "101") -> Fill:
    """Build one venue fill page row."""
    return Fill(
        id=uuid7(utc_now()),
        deployment_id=UUID(int=0),
        order_id=UUID(int=0),
        venue_fill_id=f"{venue_order_id}-fill",
        price=Decimal(price),
        quantity=_QTY,
        fee=Decimal("0.01"),
        filled_at=_WHEN,
        venue_order_id=venue_order_id,
    )


@pytest.mark.anyio
async def test_paused_reconcile_visits_every_order_and_attached_child() -> None:
    """An operator pause must not stop after the first watched order."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(
        store,
        status=DeploymentStatus.PAUSED,
        command=LifecycleCommand.STOP_NEW_ENTRIES,
        detail="Operator paused entries.",
    )
    await _order(store, deployment.id, venue_order_id="order-a", product_id="BTC-USD")
    await _order(
        store,
        deployment.id,
        venue_order_id="order-b",
        product_id="ETH-USD",
        attached_child="child-b",
    )
    venue = _Venue(
        statuses={
            "order-a": [OrderStatus.OPEN],
            "order-b": [OrderStatus.OPEN],
            "child-b": [OrderStatus.OPEN],
        },
        attached={"order-b": "child-b"},
    )
    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id),
        broker=venue,
        store=store,
        product_id="BTC-USD",
    )
    got = {event[1] for event in venue.events if event[0] == "get"}
    assert result.deployment.status is DeploymentStatus.PAUSED
    assert result.deployment.mismatch_detail == "Operator paused entries."
    assert {"order-a", "order-b", "child-b"} <= got
    stored = await store.get_deployment(deployment.id)
    assert any(order.venue_order_id == "child-b" for order in stored.orders)
    assert stored.deployment.status is DeploymentStatus.PAUSED


@pytest.mark.anyio
async def test_paused_reconcile_keeps_a_new_fault_and_still_ingests_the_sibling() -> None:
    """A new fault stays paused, and a later watched fill is still applied."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(
        store,
        status=DeploymentStatus.PAUSED,
        command=LifecycleCommand.STOP_NEW_ENTRIES,
        detail="Operator paused entries.",
    )
    await store.save_deployment(
        replace(deployment, pending_stop_price=_STOP, pending_target_price=_TARGET)
    )
    await _order(
        store,
        deployment.id,
        venue_order_id="",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        status=OrderStatus.UNKNOWN,
        purpose=IntentPurpose.ENTRY,
    )
    filled = await _order(
        store,
        deployment.id,
        venue_order_id="filled-b",
        product_id="ETH-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        status=OrderStatus.OPEN,
        purpose=IntentPurpose.ENTRY,
    )
    venue = _Venue(
        statuses={"filled-b": [OrderStatus.FILLED]},
        fills={"filled-b": [(_remote_fill("filled-b", price="100"),)]},
    )
    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id),
        broker=venue,
        store=store,
        product_id="BTC-USD",
    )
    assert result.deployment.status is DeploymentStatus.PAUSED
    assert result.deployment.mismatch_detail is not None
    assert "unconfirmed" in result.deployment.mismatch_detail
    assert "filled-b" in {event[1] for event in venue.events if event[0] == "get"}
    stored = await store.get_deployment(deployment.id)
    sibling = next(order for order in stored.orders if order.id == filled.id)
    assert sibling.status is OrderStatus.FILLED
    assert stored.deployment.status is DeploymentStatus.PAUSED


@pytest.mark.anyio
async def test_flatten_without_candles_keeps_protection() -> None:
    """An empty window must not cancel protection or report a successful flatten."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    protection = await _order(store, deployment.id, venue_order_id="child-1", product_id="BTC-USD")
    venue = _Venue()
    updated = await flatten_stopped_residual(
        await store.get_deployment(deployment.id),
        strategy=_strategy(),
        product=_product(),
        candles=(),
        broker=venue,
        store=store,
    )
    stored = await store.get_deployment(deployment.id)
    assert venue.placed == []
    assert all(action in {"get", "fills"} for action, _identity in venue.events)
    assert updated.deployment.status is DeploymentStatus.STOPPED
    assert updated.deployment.mismatch_detail == FLATTEN_AWAITING_EXECUTABLE_CONTEXT
    assert updated.position is not None
    kept = next(order for order in stored.orders if order.id == protection.id)
    assert kept.status is OrderStatus.OPEN


@pytest.mark.anyio
async def test_missing_window_uses_a_provider_preview_close_not_an_invented_price() -> None:
    """A provider preview candle may price a flatten; it is not a fabricated close."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store, mode=DeploymentMode.PAPER)
    await _position(store, deployment.id, "BTC-USD")
    paper = _RecordingPaper()

    async def _empty_window(
        market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        return await _windows(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
            candles_by_product={},
        )

    await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=_strategy(),
        store=store,
        market_data=_as_market_data(_PricedPreview(_candle(close="103"))),
        paper_broker=paper,
        live_broker=None,
        load_closed_window=_empty_window,
    )
    stored = await store.get_deployment(deployment.id)
    assert paper.placed == [("BTC-USD", OrderKind.MARKETABLE, Decimal("103"))]
    assert stored.position is None
    assert stored.deployment.mismatch_detail is None


@pytest.mark.anyio
async def test_discretionary_flatten_without_price_keeps_protection() -> None:
    """A stopped discretionary book is supervised, but not stripped without a price."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(
        store, kind=DeploymentKind.DISCRETIONARY, mode=DeploymentMode.PAPER
    )
    await _position(store, deployment.id, "BTC-USD")
    await _order(store, deployment.id, venue_order_id="disc-child", product_id="BTC-USD")
    venue = _Venue()

    async def _empty(
        market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        return await _windows(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
            candles_by_product={},
        )

    await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=None,
        store=store,
        market_data=_as_market_data(_EmptyPreview()),
        paper_broker=venue,
        live_broker=None,
        load_closed_window=_empty,
    )
    stored = await store.get_deployment(deployment.id)
    kept = next(order for order in stored.orders if order.venue_order_id == "disc-child")
    assert venue.placed == []
    assert stored.position is not None
    assert stored.deployment.status is DeploymentStatus.STOPPED
    assert stored.deployment.mismatch_detail == FLATTEN_AWAITING_EXECUTABLE_CONTEXT
    assert kept.status is OrderStatus.OPEN


@pytest.mark.anyio
async def test_sole_secondary_flatten_uses_that_products_price() -> None:
    """A flat primary book must not hide or mis-price the only secondary position."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store, mode=DeploymentMode.PAPER)
    await _position(store, deployment.id, "ETH-USD")
    paper = _RecordingPaper()
    eth_candle = _candle(close="207")

    async def _eth_only(
        market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        candles = (eth_candle,) if product_id == "ETH-USD" else ()
        return await _windows(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
            candles_by_product={product_id: candles},
        )

    await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=_strategy(),
        store=store,
        market_data=_as_market_data(_EmptyPreview()),
        paper_broker=paper,
        live_broker=None,
        load_closed_window=_eth_only,
    )
    stored = await store.get_deployment(deployment.id)
    assert stored.positions == ()
    assert paper.placed == [("ETH-USD", OrderKind.MARKETABLE, Decimal("207"))]
    assert stored.deployment.mismatch_detail is None


@pytest.mark.anyio
async def test_cancel_fill_race_does_not_submit_a_second_exit() -> None:
    """A fill discovered after cancel is applied, and no replacement exit is sent."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    await _order(store, deployment.id, venue_order_id="child-1", product_id="BTC-USD")
    venue = _Venue(
        statuses={"child-1": [OrderStatus.OPEN, OrderStatus.CANCELED]},
        fills={"child-1": [(), (_remote_fill("child-1"),)]},
    )

    async def _with_candle(
        market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        return await _windows(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
            candles_by_product={product_id: (_candle(),)},
        )

    await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=_strategy(),
        store=store,
        market_data=_as_market_data(_EmptyPreview()),
        paper_broker=_Venue(),
        live_broker=venue,
        load_closed_window=_with_candle,
    )
    stored = await store.get_deployment(deployment.id)
    assert venue.placed == []
    assert stored.position is None
    assert stored.deployment.status is DeploymentStatus.STOPPED
    assert any(fill.venue_fill_id == "child-1-fill" for fill in stored.fills)


@pytest.mark.anyio
async def test_unknown_cancel_stays_supervised_across_restart() -> None:
    """An ambiguous cancel is not success, and the next cycle still owns the book."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    await _order(store, deployment.id, venue_order_id="child-1", product_id="BTC-USD")
    venue = _Venue(cancel_error={"child-1"})

    async def _with_candle(
        market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        return await _windows(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
            candles_by_product={product_id: (_candle(),)},
        )

    await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=_strategy(),
        store=store,
        market_data=_as_market_data(_EmptyPreview()),
        paper_broker=_Venue(),
        live_broker=venue,
        load_closed_window=_with_candle,
    )
    first = await store.get_deployment(deployment.id)
    assert venue.placed == []
    assert first.position is not None
    assert first.deployment.status is DeploymentStatus.STOPPED
    assert first.deployment.mismatch_detail == CANCEL_BEFORE_EXIT_DETAIL
    venue.cancel_error.clear()
    await supervise_stopped_deployment(
        first,
        strategy=_strategy(),
        store=store,
        market_data=_as_market_data(_EmptyPreview()),
        paper_broker=_Venue(),
        live_broker=venue,
        load_closed_window=_with_candle,
    )
    stored = await store.get_deployment(deployment.id)
    assert stored.position is None
    assert stored.deployment.status is DeploymentStatus.STOPPED
    assert venue.placed
    assert venue.placed[0][0] == "BTC-USD"


@pytest.mark.anyio
async def test_stopped_late_fill_is_reconciled_before_the_book_is_called_flat() -> None:
    """A fill already at the venue flattens the book before another exit is considered."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    await _order(
        store,
        deployment.id,
        venue_order_id="child-1",
        product_id="BTC-USD",
        status=OrderStatus.FILLED,
    )
    venue = _Venue(
        statuses={"child-1": [OrderStatus.FILLED]},
        fills={"child-1": [(_remote_fill("child-1", price="99"),)]},
    )

    async def _with_candle(
        market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        return await _windows(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
            candles_by_product={product_id: (_candle(),)},
        )

    await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=_strategy(),
        store=store,
        market_data=_as_market_data(_EmptyPreview()),
        paper_broker=_Venue(),
        live_broker=venue,
        load_closed_window=_with_candle,
    )
    stored = await store.get_deployment(deployment.id)
    assert venue.placed == []
    assert stored.position is None
    assert stored.deployment.status is DeploymentStatus.STOPPED
    assert stored.fills[0].economics_applied_at is not None


async def _supervise(
    store: InMemoryExecutionStore,
    deployment: Deployment,
    venue: _Venue,
    *,
    candles: tuple[Candle, ...] | None = None,
    preview: _EmptyPreview | None = None,
) -> None:
    """Run one hermetic stopped cycle with scripted window and preview evidence."""
    supplied = (_candle(),) if candles is None else candles

    async def _load(
        market_data: MarketDataService,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """Provide the requested product's verified or explicitly missing context."""
        return await _windows(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
            candles_by_product={product_id: supplied},
        )

    await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=None if deployment.kind is DeploymentKind.DISCRETIONARY else _strategy(),
        store=store,
        market_data=_as_market_data(preview or _EmptyPreview()),
        paper_broker=venue,
        live_broker=venue,
        load_closed_window=_load,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["get", "fills", "unknown", "filled_without_fills"])
async def test_paused_child_import_fault_is_retained_and_all_siblings_are_read(
    failure: str,
) -> None:
    """Child import faults cannot be overwritten by an old operator pause or a later child."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(
        store, status=DeploymentStatus.PAUSED, detail="Operator paused entries."
    )
    await _order(
        store,
        deployment.id,
        venue_order_id="parent-a",
        product_id="BTC-USD",
        attached_child="child-a",
    )
    await _order(
        store,
        deployment.id,
        venue_order_id="parent-b",
        product_id="ETH-USD",
        attached_child="child-b",
    )
    venue = _Venue()
    if failure == "get":
        venue.get_error.add("child-a")
    elif failure == "fills":
        venue.fills_error.add("child-a")
    else:
        venue.statuses["child-a"] = [
            OrderStatus.UNKNOWN if failure == "unknown" else OrderStatus.FILLED
        ]
    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )
    assert result.deployment.status is DeploymentStatus.PAUSED
    assert result.deployment.mismatch_detail != "Operator paused entries."
    assert result.deployment.mismatch_detail is not None
    assert "untrusted" not in result.deployment.mismatch_detail
    assert {"parent-a", "parent-b", "child-a", "child-b"} <= {
        identity for action, identity in venue.events if action == "get"
    }
    assert len(result.orders) == 4


@pytest.mark.anyio
@pytest.mark.parametrize("status", [DeploymentStatus.PAUSED, DeploymentStatus.STOPPED])
async def test_initial_fill_replay_fault_is_retained(status: DeploymentStatus) -> None:
    """An economics replay fault survives healthy order and child phases, without unstopping."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store, status=status, detail="Operator paused entries.")
    entry = await _order(
        store,
        deployment.id,
        venue_order_id="replay-entry",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
        status=OrderStatus.FILLED,
    )
    remote = _remote_fill("replay-entry")
    await store.save_fill(replace(remote, deployment_id=deployment.id, order_id=entry.id))
    await _order(store, deployment.id, venue_order_id="sibling", product_id="ETH-USD")
    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=_Venue(), store=store
    )
    assert result.deployment.status is status
    assert result.deployment.mismatch_detail == "Entry fill is missing its stored stop price."


@pytest.mark.anyio
async def test_filled_protection_with_lagging_fills_blocks_flatten_until_reconciled() -> None:
    """FILLED protection is not canceled/replaced while its economics still lag at the venue."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    await _order(store, deployment.id, venue_order_id="child", product_id="BTC-USD")
    venue = _Venue(statuses={"child": [OrderStatus.FILLED]})
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    assert current.position is not None
    assert venue.placed == []
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert current.deployment.mismatch_detail == FILLED_WITHOUT_REST_FILLS_DETAIL
    venue.fills["child"] = [(_remote_fill("child"),)]
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    assert current.position is None
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert current.deployment.mismatch_detail is None
    assert venue.placed == []


@pytest.mark.anyio
async def test_canceled_terminal_order_is_rechecked_for_a_late_remaining_fill() -> None:
    """Partial fill coverage at cancel time is not proof that no later fill can arrive."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    order = await _order(
        store,
        deployment.id,
        venue_order_id="canceled-child",
        product_id="BTC-USD",
        status=OrderStatus.CANCELED,
    )
    partial = Decimal("0.004")
    await store.save_order(replace(order, filled_quantity=partial))
    current = await store.get_deployment(deployment.id)
    assert current.position is not None
    await store.save_position(
        replace(current.position, quantity=_QTY - partial), deployment_id=deployment.id
    )
    await store.save_fill(
        replace(
            _remote_fill("canceled-child"),
            deployment_id=deployment.id,
            order_id=order.id,
            quantity=partial,
            economics_applied_at=utc_now(),
        )
    )
    late = replace(
        _remote_fill("canceled-child"), venue_fill_id="late-remainder", quantity=_QTY - partial
    )
    venue = _Venue(
        statuses={"canceled-child": [OrderStatus.CANCELED]}, fills={"canceled-child": [(late,)]}
    )
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    assert current.position is None
    assert venue.placed == []
    assert len(current.fills) == 2
    assert all(fill.economics_applied_at is not None for fill in current.fills)


@pytest.mark.anyio
async def test_retained_attached_reference_is_imported_and_its_cancel_race_is_applied() -> None:
    """An attached child missing locally must be discovered before the cover is submitted."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    parent = await _order(
        store,
        deployment.id,
        venue_order_id="parent",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
        status=OrderStatus.FILLED,
        attached_child="unimported-child",
    )
    await store.save_fill(
        replace(
            _remote_fill("parent"),
            deployment_id=deployment.id,
            order_id=parent.id,
            economics_applied_at=utc_now(),
        )
    )
    venue = _Venue(fills={"unimported-child": [(), (), (_remote_fill("unimported-child"),)]})
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    assert current.position is None
    assert venue.placed == []
    assert ("cancel", "unimported-child") in venue.events
    assert current.deployment.status is DeploymentStatus.STOPPED


@pytest.mark.anyio
@pytest.mark.parametrize("context", ["stale", "future", "zero_volume", "disabled"])
async def test_unbounded_or_non_executable_context_does_not_strip_protection(context: str) -> None:
    """A stale/future/no-trade candle or disabled product never authorizes a flatten."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store, kind=DeploymentKind.DISCRETIONARY)
    await _position(store, deployment.id, "BTC-USD")
    await _order(store, deployment.id, venue_order_id="protected", product_id="BTC-USD")
    candle = _candle()
    if context == "stale":
        candle = replace(candle, starts_at=candle.starts_at - timedelta(hours=1))
    elif context == "future":
        candle = replace(candle, starts_at=candle.starts_at + timedelta(hours=1))
    elif context == "zero_volume":
        candle = replace(candle, volume=Decimal("0"))

    class _DisabledPreview(_PricedPreview):
        """Return a disabled venue product, not invented constraints."""

        async def get_preview(self, product_id: str, interval: CandleInterval) -> MarketDataPreview:
            """Disable the scripted product."""
            preview = await super().get_preview(product_id, interval)
            return replace(preview, product=replace(preview.product, trading_enabled=False))

    preview = _DisabledPreview(candle) if context == "disabled" else _PricedPreview(candle)
    venue = _Venue()
    await _supervise(store, deployment, venue, candles=(), preview=preview)
    current = await store.get_deployment(deployment.id)
    assert venue.placed == []
    assert not any(action == "cancel" for action, _identity in venue.events)
    assert current.position is not None
    assert current.deployment.mismatch_detail == FLATTEN_AWAITING_EXECUTABLE_CONTEXT


@pytest.mark.anyio
async def test_managed_discretionary_shutdown_keeps_protection_and_cancels_only_entries() -> None:
    """Managed shutdown is not flatten even for a discretionary book."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(
        store, kind=DeploymentKind.DISCRETIONARY, command=LifecycleCommand.MANAGED_SHUTDOWN
    )
    await _position(store, deployment.id, "BTC-USD")
    await _order(store, deployment.id, venue_order_id="protection", product_id="BTC-USD")
    await _order(
        store,
        deployment.id,
        venue_order_id="entry",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
    )
    venue = _Venue()
    await _supervise(store, deployment, venue, candles=())
    current = await store.get_deployment(deployment.id)
    assert current.position is not None
    assert venue.placed == []
    assert [identity for action, identity in venue.events if action == "cancel"] == ["entry"]
    assert current.deployment.status is DeploymentStatus.STOPPED


@pytest.mark.anyio
async def test_stopped_flat_book_unknown_entry_cancel_stays_pending() -> None:
    """A zero local position with an ambiguous working entry is not a completed flatten."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _order(
        store,
        deployment.id,
        venue_order_id="entry",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
    )
    venue = _Venue(cancel_error={"entry"})
    await _supervise(store, deployment, venue, candles=())
    current = await store.get_deployment(deployment.id)
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert current.deployment.mismatch_detail == CANCEL_BEFORE_EXIT_DETAIL
    assert current.orders[0].status is OrderStatus.OPEN


@pytest.mark.anyio
async def test_live_reconcile_applies_each_siblings_exit_to_its_own_inventory() -> None:
    """Atomic stores must project the order product, not whichever book is focused first."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    for product_id in ("BTC-USD", "ETH-USD"):
        await _position(store, deployment.id, product_id)
        await _order(store, deployment.id, venue_order_id=product_id, product_id=product_id)
    venue = _Venue(
        statuses={"BTC-USD": [OrderStatus.FILLED], "ETH-USD": [OrderStatus.FILLED]},
        fills={
            "BTC-USD": [(_remote_fill("BTC-USD", price="101"),)],
            "ETH-USD": [(_remote_fill("ETH-USD", price="207"),)],
        },
    )
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    assert current.positions == ()
    assert current.position is None
    assert len(current.fills) == 2
    assert current.deployment.cash == Decimal("10003.06")
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert venue.placed == []


@pytest.mark.anyio
async def test_flatten_exits_every_book_without_writing_a_sibling_into_the_closed_book() -> None:
    """A multi-book flatten exits each product exactly once at that product's own price."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store, mode=DeploymentMode.PAPER)
    await _position(store, deployment.id, "BTC-USD")
    await _position(store, deployment.id, "ETH-USD")
    paper = _RecordingPaper()

    async def _prices(
        market_data: MarketDataService,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """Return explicit provider prices per book."""
        del market_data, timeframe, warmup_bars, deploy_anchor
        candle = _candle(close="101" if product_id == "BTC-USD" else "207")
        return _product(product_id), (candle,), candle.starts_at

    await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=_strategy(),
        store=store,
        market_data=_as_market_data(_EmptyPreview()),
        paper_broker=paper,
        live_broker=None,
        load_closed_window=_prices,
    )
    current = await store.get_deployment(deployment.id)
    assert current.positions == ()
    assert paper.placed == [
        ("BTC-USD", OrderKind.MARKETABLE, Decimal("101")),
        ("ETH-USD", OrderKind.MARKETABLE, Decimal("207")),
    ]
    assert current.deployment.mismatch_detail is None
    assert current.deployment.status is DeploymentStatus.STOPPED


@pytest.mark.anyio
async def test_flatten_does_not_settle_or_hide_orphan_unapplied_fill_evidence() -> None:
    """A fill whose parent is unavailable is unresolved economics, not proof of flatness."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await store.save_fill(replace(_remote_fill("orphan"), deployment_id=deployment.id))
    await _supervise(store, deployment, _Venue())
    current = await store.get_deployment(deployment.id)
    assert current.fills[0].economics_applied_at is None
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert current.deployment.mismatch_detail == "Stored fills have unapplied economics."


@pytest.mark.anyio
async def test_flat_entry_cancel_fill_race_reopens_owned_inventory_before_settling() -> None:
    """A buy discovered during cancellation creates owned residual inventory, not flat success."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await store.save_deployment(
        replace(
            deployment,
            pending_stop_price=_STOP,
            pending_target_price=_TARGET,
            phase=RuntimePhase.PENDING_ENTRY,
        )
    )
    await _order(
        store,
        deployment.id,
        venue_order_id="entry-race",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
    )
    venue = _Venue(fills={"entry-race": [(), (_remote_fill("entry-race", price="100"),)]})
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    assert current.position is not None
    assert current.position.quantity == _QTY
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert venue.placed == []
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    assert current.position is None
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert len(venue.placed) == 1


@pytest.mark.anyio
async def test_partial_applied_fragment_does_not_hide_the_rest_of_a_terminal_order() -> None:
    """Locally FILLED after a partial projection is not evidence the venue has no more fills."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    current = await store.get_deployment(deployment.id)
    assert current.position is not None
    await store.save_position(
        replace(current.position, quantity=Decimal("0.004")), deployment_id=deployment.id
    )
    entry = await _order(
        store,
        deployment.id,
        venue_order_id="fragmented-entry",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
        status=OrderStatus.FILLED,
    )
    await store.save_order(replace(entry, filled_quantity=Decimal("0.004")))
    await store.save_fill(
        replace(
            _remote_fill("fragmented-entry", price="100"),
            deployment_id=deployment.id,
            order_id=entry.id,
            quantity=Decimal("0.004"),
            economics_applied_at=utc_now(),
        )
    )
    remainder = replace(
        _remote_fill("fragmented-entry", price="100"),
        venue_fill_id="remaining-entry-fragment",
        quantity=Decimal("0.006"),
    )
    venue = _Venue(
        statuses={"fragmented-entry": [OrderStatus.FILLED]},
        fills={"fragmented-entry": [(remainder,)]},
    )
    current = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )
    assert current.position is not None
    assert current.position.quantity == _QTY
    assert len(current.fills) == 2
    assert current.deployment.status is DeploymentStatus.STOPPED


@pytest.mark.anyio
async def test_protective_fill_can_settle_a_previously_price_pending_flatten() -> None:
    """Pending-price detail must clear when real venue fills subsequently prove the book flat."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _position(store, deployment.id, "BTC-USD")
    await _order(store, deployment.id, venue_order_id="protection", product_id="BTC-USD")
    venue = _Venue()
    await _supervise(store, deployment, venue, candles=())
    current = await store.get_deployment(deployment.id)
    assert current.deployment.mismatch_detail == FLATTEN_AWAITING_EXECUTABLE_CONTEXT
    venue.statuses["protection"] = [OrderStatus.FILLED]
    venue.fills["protection"] = [(_remote_fill("protection"),)]
    await _supervise(store, deployment, venue, candles=())
    current = await store.get_deployment(deployment.id)
    assert current.position is None
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert current.deployment.mismatch_detail is None
    assert venue.placed == []


@pytest.mark.anyio
async def test_missing_entry_projection_metadata_keeps_protection_and_an_explicit_fault() -> None:
    """A missing stored stop is unknown venue inventory, never authority to strip protection."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    await _order(
        store,
        deployment.id,
        venue_order_id="entry",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
    )
    await _order(store, deployment.id, venue_order_id="protection", product_id="BTC-USD")
    venue = _Venue(
        statuses={"entry": [OrderStatus.FILLED]}, fills={"entry": [(_remote_fill("entry"),)]}
    )
    await _supervise(store, deployment, venue, candles=())
    current = await store.get_deployment(deployment.id)
    assert current.deployment.status is DeploymentStatus.STOPPED
    assert current.deployment.mismatch_detail == "Entry fill is missing its stored stop price."
    assert not any(action == "cancel" for action, _identity in venue.events)
    assert venue.placed == []
