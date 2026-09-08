"""Closed-candle paper maker loop for one published long strategy."""

from dataclasses import replace
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
    OrderStatus,
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


async def _filled_long(
    store: InMemoryExecutionStore, strategy: StrategyDefinition
) -> tuple[DeploymentSnapshot, tuple[Candle, ...]]:
    """Place and fill a paper maker entry, returning the snapshot and candle window."""
    snapshot = await _running_snapshot(store, strategy)
    warmup = _candles(30, low_offset=Decimal("0.01"))
    pending = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=warmup,
        broker=PaperBroker(),
        store=store,
    )
    last = warmup[-1]
    fill_bar = Candle(
        starts_at=last.starts_at + timedelta(hours=1),
        open=last.close,
        high=last.close + Decimal("1"),
        low=last.close - Decimal("0.5"),
        close=last.close,
        volume=Decimal("10"),
    )
    window = (*warmup, fill_bar)
    filled = await process_closed_bar(
        pending,
        strategy=strategy,
        product=_product(),
        candles=window,
        broker=PaperBroker(),
        store=store,
    )
    return filled, window


def _next_bar(
    window: tuple[Candle, ...],
    *,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
) -> Candle:
    """Build the next hourly candle after the current window."""
    last = window[-1]
    return Candle(
        starts_at=last.starts_at + timedelta(hours=1),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=Decimal("10"),
    )


@pytest.mark.anyio
async def test_stop_still_fires_while_take_profit_is_resting() -> None:
    """A resting TP must not disable the synthetic stop on a later closed bar."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    filled, window = await _filled_long(store, strategy)
    assert filled.position is not None
    assert filled.deployment.phase is RuntimePhase.PENDING_EXIT
    stop = filled.position.stop_price
    crash = _next_bar(
        window,
        open_=stop + Decimal("1"),
        high=stop + Decimal("1"),
        low=stop - Decimal("5"),
        close=stop - Decimal("2"),
    )
    exited = await process_closed_bar(
        filled,
        strategy=strategy,
        product=_product(),
        candles=(*window, crash),
        broker=PaperBroker(),
        store=store,
    )
    assert exited.position is None
    assert exited.deployment.phase is RuntimePhase.FLAT
    assert any(order.kind.value == "marketable" for order in exited.orders)


@pytest.mark.anyio
async def test_paused_deployment_still_exits_on_stop() -> None:
    """Pause blocks new entries but still evaluates protective exits."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    filled, window = await _filled_long(store, strategy)
    assert filled.position is not None
    paused = replace(filled.deployment, status=DeploymentStatus.PAUSED)
    await store.save_deployment(paused)
    filled = await store.get_deployment(filled.deployment.id)
    stop = filled.position.stop_price
    crash = _next_bar(
        window,
        open_=stop + Decimal("1"),
        high=stop + Decimal("1"),
        low=stop - Decimal("5"),
        close=stop - Decimal("2"),
    )
    exited = await process_closed_bar(
        filled,
        strategy=strategy,
        product=_product(),
        candles=(*window, crash),
        broker=PaperBroker(),
        store=store,
    )
    assert exited.position is None
    assert exited.deployment.status is DeploymentStatus.PAUSED
    assert all(
        order.status is not OrderStatus.OPEN or order.side.value != "buy" for order in exited.orders
    )


@pytest.mark.anyio
async def test_take_profit_fill_applies_cooldown_before_reentry() -> None:
    """TP fills must honor cooldown_bars instead of flattening to zero cooldown."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    filled, window = await _filled_long(store, strategy)
    assert filled.position is not None
    target = filled.position.target_price
    tp_bar = _next_bar(
        window,
        open_=target - Decimal("1"),
        high=target + Decimal("2"),
        low=target - Decimal("1"),
        close=target,
    )
    after = await process_closed_bar(
        filled,
        strategy=strategy,
        product=_product(),
        candles=(*window, tp_bar),
        broker=PaperBroker(),
        store=store,
    )
    assert after.position is None
    assert after.deployment.phase is RuntimePhase.FLAT
    assert after.deployment.cooldown_bars_remaining > 0
    assert all(order.status is not OrderStatus.OPEN for order in after.orders)


@pytest.mark.anyio
async def test_stop_fill_uses_gap_open_when_bar_opens_through_stop() -> None:
    """A gap through the stop fills at the adverse open, not the stop price."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    filled, window = await _filled_long(store, strategy)
    assert filled.position is not None
    stop = filled.position.stop_price
    gap_open = stop - Decimal("8")
    crash = _next_bar(
        window,
        open_=gap_open,
        high=gap_open + Decimal("1"),
        low=gap_open - Decimal("1"),
        close=gap_open,
    )
    exited = await process_closed_bar(
        filled,
        strategy=strategy,
        product=_product(),
        candles=(*window, crash),
        broker=PaperBroker(),
        store=store,
    )
    sell_fills = [fill for fill in exited.fills if fill.price == gap_open]
    assert sell_fills
    assert exited.position is None
