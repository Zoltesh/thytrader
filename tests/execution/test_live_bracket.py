"""Live venue OCO brackets after fill; paper never submits trigger_bracket."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from thytrader.execution.broker import SubmitResult
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
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
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.strategies.authoring import create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.execution.models import DeploymentSnapshot


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
    """Published reference strategy used only for exit configuration."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python")
    payload["status"] = StrategyStatus.PUBLISHED.value
    return StrategyDefinition.model_validate(payload)


def _candle(hour: int) -> Candle:
    """Return a placeholder hourly candle."""
    start = datetime(2026, 1, 1, hour, tzinfo=UTC)
    price = Decimal("100") + Decimal(hour)
    return Candle(
        starts_at=start,
        open=price,
        high=price + Decimal("2"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("10"),
    )


@dataclass
class _RecordingBroker:
    """Live-style broker that records place/cancel and never candle-matches."""

    placed: list[dict[str, object]] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)

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
    ) -> SubmitResult:
        """Record one submit and rest it as OPEN."""
        self.placed.append(
            {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": side,
                "kind": kind,
                "quantity": quantity,
                "price": price,
                "stop_trigger_price": stop_trigger_price,
            }
        )
        return SubmitResult(status=OrderStatus.OPEN, venue_order_id=client_order_id)

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Record one cancel."""
        del client_order_id
        self.canceled.append(venue_order_id)
        return SubmitResult(status=OrderStatus.CANCELED, venue_order_id=venue_order_id)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Unused in these tests."""
        del client_order_id
        return SubmitResult(status=OrderStatus.OPEN, venue_order_id=venue_order_id)

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Live fills arrive through REST reconcile, not this double."""
        del product_id, order_id
        return ()

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live does not match candles."""
        del order, candle
        return None

    def maker_limit_price(self, *, product_id: str, mark: Decimal) -> Decimal:
        """Unused in these tests."""
        del product_id
        return mark


async def _live_open(
    store: InMemoryExecutionStore,
    strategy: StrategyDefinition,
    *,
    last_evaluated_bar: datetime,
    entered_bar: datetime,
    stop_price: Decimal = Decimal("90"),
    target_price: Decimal = Decimal("120"),
    bars_held: int = 1,
) -> DeploymentSnapshot:
    """Insert one live OPEN deployment with a long position."""
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(strategy),
        strategy_id=strategy.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=None,
        cash=Decimal("10000"),
        phase=RuntimePhase.OPEN,
        last_evaluated_bar=last_evaluated_bar,
        bars_held=bars_held,
        created_at=now,
        updated_at=now,
    )
    await store.create_deployment(deployment)
    await store.save_position(
        Position(
            deployment_id=deployment.id,
            quantity=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_price=stop_price,
            target_price=target_price,
            entered_bar=entered_bar,
            updated_at=now,
        ),
        deployment_id=deployment.id,
    )
    return await store.get_deployment(deployment.id)


@pytest.mark.anyio
async def test_live_open_position_rests_trigger_bracket_oco() -> None:
    """After a live fill the worker rests one venue OCO, not a paper take-profit."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    broker = _RecordingBroker()
    snapshot = await _live_open(
        store,
        strategy,
        last_evaluated_bar=_candle(1).starts_at,
        entered_bar=_candle(1).starts_at,
        bars_held=1,
    )
    candle = _candle(2)
    updated = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1), candle),
        broker=broker,
        store=store,
    )
    assert len(broker.placed) == 1
    placed = broker.placed[0]
    assert placed["kind"] is OrderKind.TRIGGER_BRACKET
    assert placed["side"] is OrderSide.SELL
    assert placed["price"] == Decimal("120")
    assert placed["stop_trigger_price"] == Decimal("90")
    assert updated.deployment.phase is RuntimePhase.PENDING_EXIT
    assert updated.orders[0].kind is OrderKind.TRIGGER_BRACKET


@pytest.mark.anyio
async def test_live_does_not_fire_synthetic_stop_while_bracket_is_open() -> None:
    """A closed bar through the stop must not submit a second marketable live exit."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    broker = _RecordingBroker()
    snapshot = await _live_open(
        store,
        strategy,
        last_evaluated_bar=_candle(1).starts_at,
        entered_bar=_candle(1).starts_at,
        bars_held=1,
    )
    now = utc_now()
    bracket = Order(
        id=uuid7(now),
        deployment_id=snapshot.deployment.id,
        intent_id=uuid7(now),
        client_order_id="bracket-1",
        side=OrderSide.SELL,
        kind=OrderKind.TRIGGER_BRACKET,
        quantity=Decimal("0.01"),
        price=Decimal("120"),
        stop_trigger_price=Decimal("90"),
        status=OrderStatus.OPEN,
        venue_order_id="bracket-1",
        created_at=now,
        updated_at=now,
    )
    await store.save_order(bracket)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    crash = Candle(
        starts_at=_candle(2).starts_at,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("80"),
        close=Decimal("85"),
        volume=Decimal("10"),
    )
    updated = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1), crash),
        broker=broker,
        store=store,
    )
    assert all(item["kind"] is not OrderKind.MARKETABLE for item in broker.placed)
    assert updated.position is not None
    assert updated.orders[0].kind is OrderKind.TRIGGER_BRACKET


@pytest.mark.anyio
async def test_paper_rejects_trigger_bracket_submit() -> None:
    """Paper simulates OCO locally and must not send venue trigger brackets."""
    with pytest.raises(ValueError, match="paper does not submit venue trigger brackets"):
        await PaperBroker().place_order(
            client_order_id="paper-1",
            product_id="BTC-USD",
            side=OrderSide.SELL,
            kind=OrderKind.TRIGGER_BRACKET,
            quantity=Decimal("0.01"),
            price=Decimal("120"),
            stop_trigger_price=Decimal("90"),
        )
