"""Closed-candle paper maker loop for one published long strategy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.strategies.authoring import create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint


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


def _always_entry_strategy(*, on_unfilled_entry: str = "cancel") -> StrategyDefinition:
    """Published reference strategy whose entry condition is always true."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python")
    payload["status"] = StrategyStatus.PUBLISHED.value
    payload["entry"]["when"] = {
        "all": [
            {
                "left": {"literal": "1"},
                "operator": "greater_than_or_equal",
                "right": {"literal": "0"},
            }
        ]
    }
    payload["execution"]["max_entry_wait_bars"] = 2
    payload["execution"]["on_unfilled_entry"] = on_unfilled_entry
    return StrategyDefinition.model_validate(payload)


def _candles(count: int, *, low_offset: Decimal = Decimal("1")) -> tuple[Candle, ...]:
    """Build a rising 1h series with a configurable low relative to close."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    for index in range(count):
        close = Decimal("100") + Decimal(index)
        candles.append(
            Candle(
                starts_at=start + timedelta(hours=index),
                open=close,
                high=close + Decimal("2"),
                low=close - low_offset,
                close=close,
                volume=Decimal("10"),
            )
        )
    return tuple(candles)


async def _running_snapshot(
    store: InMemoryExecutionStore, strategy: StrategyDefinition
) -> DeploymentSnapshot:
    """Insert one running paper deployment and return its snapshot."""
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(strategy),
        strategy_id=strategy.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000"),
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
    )
    await store.create_deployment(deployment)
    return await store.get_deployment(deployment.id)


@pytest.mark.anyio
async def test_paper_loop_places_maker_entry_once_then_fills() -> None:
    """A matched closed bar rests a post-only buy; the next bar fills when low trades through."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    snapshot = await _running_snapshot(store, strategy)
    warmup = _candles(30, low_offset=Decimal("0.01"))
    first = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=warmup,
        broker=PaperBroker(),
        store=store,
    )
    assert first.deployment.phase is RuntimePhase.PENDING_ENTRY
    assert first.deployment.last_signal == "matched"
    assert len(first.orders) == 1
    assert first.orders[0].status.value == "open"
    assert first.orders[0].client_order_id

    repeat = await process_closed_bar(
        first,
        strategy=strategy,
        product=_product(),
        candles=warmup,
        broker=PaperBroker(),
        store=store,
    )
    assert len(repeat.orders) == 1

    last = warmup[-1]
    continuation = Candle(
        starts_at=last.starts_at + timedelta(hours=1),
        open=last.close,
        high=last.close + Decimal("1"),
        low=last.close - Decimal("0.5"),
        close=last.close,
        volume=Decimal("10"),
    )
    filled_window = (*warmup, continuation)
    filled = await process_closed_bar(
        repeat,
        strategy=strategy,
        product=_product(),
        candles=filled_window,
        broker=PaperBroker(),
        store=store,
    )
    assert filled.deployment.phase in {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}
    assert filled.position is not None
    assert filled.fills


@pytest.mark.anyio
async def test_unfilled_entry_cancels_after_max_wait() -> None:
    """When the candle never trades through the limit, the entry is canceled."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    snapshot = await _running_snapshot(store, strategy)
    warmup = _candles(30, low_offset=Decimal("0"))
    pending = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=warmup,
        broker=PaperBroker(),
        store=store,
    )
    last = warmup[-1]
    wait_bars: list[Candle] = []
    current = pending
    for offset in (1, 2):
        bar = Candle(
            starts_at=last.starts_at + timedelta(hours=offset),
            open=last.close + Decimal("10"),
            high=last.close + Decimal("12"),
            low=last.close + Decimal("9"),
            close=last.close + Decimal("10"),
            volume=Decimal("10"),
        )
        wait_bars.append(bar)
        current = await process_closed_bar(
            current,
            strategy=strategy,
            product=_product(),
            candles=warmup + tuple(wait_bars),
            broker=PaperBroker(),
            store=store,
        )
    assert current.deployment.phase is RuntimePhase.FLAT
    assert all(order.status.value != "open" for order in current.orders)
