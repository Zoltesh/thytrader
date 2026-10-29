"""Working-entry lifecycle regressions: attached children, reprice failures, blocked books.

Each reprice test rests an always-true entry, then closes two bars above it so the wait
expires. Every bar runs through the revision-fenced store, exactly as the execution
worker wraps one book's pass.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.execution.test_loop import _always_entry_strategy, _candles, _product, _running_snapshot
from thytrader.exchanges.fees import FeeProfile
from thytrader.execution import entry_reprice
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.discretionary import _watch_pending_entry
from thytrader.execution.entry_reprice import CANCEL_RACE_FILLS_PENDING_DETAIL
from thytrader.execution.entry_sizing import _attaches_bracket
from thytrader.execution.leases import RevisionFencedStore
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.execution.runtime_ops import _active_entry
from thytrader.market_data.models import Candle
from thytrader.strategies.models import atr_trailing_stop
from thytrader.trading.geometry import EntrySkipReason
from thytrader.trading.ids import utc_now
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
    with_runtime,
)

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyDefinition

pytestmark = pytest.mark.anyio


class _ScriptedPaper(PaperBroker):
    """Paper broker whose maker price or post-only create can be made to fail on demand."""

    def __init__(self) -> None:
        super().__init__()
        self.maker_price_fails = False
        self.reject_post_only = False

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        if self.maker_price_fails:
            raise BrokerError("empty top of book")
        return super().maker_limit_price(product_id=product_id, mark=mark, side=side)

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
        if self.reject_post_only and kind is OrderKind.POST_ONLY_LIMIT:
            return SubmitResult(
                status=OrderStatus.REJECTED,
                venue_order_id=client_order_id,
                reject_reason="INVALID_LIMIT_PRICE_POST_ONLY",
            )
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


async def _bar(
    store: InMemoryExecutionStore,
    current: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: tuple[Candle, ...],
    broker: PaperBroker,
) -> DeploymentSnapshot:
    """Run one closed bar through the worker's per-book revision fence."""
    fenced = RevisionFencedStore(store, current.deployment.id, current.deployment.revision)
    return await process_closed_bar(
        current,
        strategy=strategy,
        product=_product(),
        candles=candles,
        broker=broker,
        store=fenced,
    )


def _above(warmup: tuple[Candle, ...], count: int) -> tuple[Candle, ...]:
    """Closed bars trading above the resting entry, so it never fills."""
    last = warmup[-1]
    return tuple(
        Candle(
            starts_at=last.starts_at + timedelta(hours=offset),
            open=last.close + Decimal(10),
            high=last.close + Decimal(12),
            low=last.close + Decimal(9),
            close=last.close + Decimal(10),
            volume=Decimal(10),
        )
        for offset in range(1, count + 1)
    )


async def _rested(
    broker: PaperBroker, *, policy: str = "reprice"
) -> tuple[InMemoryExecutionStore, StrategyDefinition, DeploymentSnapshot, tuple[Candle, ...]]:
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy(on_unfilled_entry=policy)
    snapshot = await _running_snapshot(store, strategy)
    # Stored books carry their decision clock; fills read outside the bar loop need it.
    await store.save_deployment(replace(snapshot.deployment, timeframe=strategy.timeframe))
    snapshot = await store.get_deployment(snapshot.deployment.id)
    warmup = _candles(30, low_offset=Decimal("0"))
    current = await _bar(store, snapshot, strategy=strategy, candles=warmup, broker=broker)
    assert [order.status for order in current.orders] == [OrderStatus.OPEN]
    return store, strategy, current, warmup


async def _expire(
    store: InMemoryExecutionStore,
    current: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    warmup: tuple[Candle, ...],
    broker: PaperBroker,
) -> DeploymentSnapshot:
    """Close the two wait bars; the second expires the entry."""
    bars = _above(warmup, 2)
    for index in range(len(bars)):
        current = await _bar(
            store,
            current,
            strategy=strategy,
            candles=warmup + bars[: index + 1],
            broker=broker,
        )
    return current


def _working(snapshot: DeploymentSnapshot) -> list[Order]:
    return [order for order in snapshot.orders if order.status is OrderStatus.OPEN]


async def test_reprice_saves_on_the_current_revision() -> None:
    """S5: the replacement entry is followed by a save that carries the fresh revision."""
    broker = _ScriptedPaper()
    store, strategy, current, warmup = await _rested(broker)
    original = current.orders[0].id

    current = await _expire(store, current, strategy=strategy, warmup=warmup, broker=broker)

    working = _working(current)
    assert len(working) == 1
    assert working[0].id != original
    assert current.deployment.phase is RuntimePhase.PENDING_ENTRY
    assert current.deployment.status is DeploymentStatus.RUNNING
    assert current.deployment.mismatch_detail is None


async def test_rejected_replacement_returns_flat_without_a_pause() -> None:
    """S4: a venue-rejected replacement abandons the entry instead of stranding PENDING_ENTRY."""
    broker = _ScriptedPaper()
    store, strategy, current, warmup = await _rested(broker)
    broker.reject_post_only = True

    current = await _expire(store, current, strategy=strategy, warmup=warmup, broker=broker)

    assert _working(current) == []
    assert current.deployment.status is DeploymentStatus.RUNNING
    assert current.deployment.phase is RuntimePhase.FLAT
    assert current.deployment.mismatch_detail is None
    assert current.deployment.cooldown_bars_remaining >= 1


async def test_reprice_without_a_maker_price_returns_flat_without_a_pause() -> None:
    """S3/S4: no maker price at reprice time abandons the entry; nothing must resume by hand."""
    broker = _ScriptedPaper()
    store, strategy, current, warmup = await _rested(broker)
    broker.maker_price_fails = True

    current = await _expire(store, current, strategy=strategy, warmup=warmup, broker=broker)

    assert _working(current) == []
    assert current.deployment.status is DeploymentStatus.RUNNING
    assert current.deployment.phase is RuntimePhase.FLAT
    assert current.deployment.mismatch_detail is None


async def test_missing_maker_price_skips_the_bar_and_the_next_bar_enters() -> None:
    """S3: a transient maker-price failure skips that bar instead of pausing for good."""
    broker = _ScriptedPaper()
    broker.maker_price_fails = True
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    snapshot = await _running_snapshot(store, strategy)
    warmup = _candles(30, low_offset=Decimal("0"))

    skipped = await _bar(store, snapshot, strategy=strategy, candles=warmup, broker=broker)

    assert skipped.orders == ()
    assert skipped.deployment.status is DeploymentStatus.RUNNING
    assert skipped.deployment.mismatch_detail is None
    broker.maker_price_fails = False
    entered = await _bar(
        store, skipped, strategy=strategy, candles=warmup + _above(warmup, 1), broker=broker
    )
    assert len(_working(entered)) == 1


async def test_blocked_reprice_book_still_cancels_the_expired_entry() -> None:
    """L5: a latched book that may not reprice still cancels the expired entry."""
    broker = _ScriptedPaper()
    store, strategy, current, warmup = await _rested(broker)
    original = current.orders[0].id
    await store.save_deployment(
        with_runtime(current.deployment, updated_at=utc_now(), daily_loss_latched=True)
    )
    current = await store.get_deployment(current.deployment.id)

    current = await _expire(store, current, strategy=strategy, warmup=warmup, broker=broker)

    assert _working(current) == []
    assert next(order for order in current.orders if order.id == original).status is (
        OrderStatus.CANCELED
    )
    assert current.deployment.phase is RuntimePhase.FLAT


def test_reprice_never_attaches_a_bracket_to_a_live_futures_entry() -> None:
    """S6: the reprice attach rule is the first entry's, including the live futures exclusion."""
    strategy = _always_entry_strategy()
    assert atr_trailing_stop(strategy.exits) is None
    deployment = with_runtime(
        _deployment_stub(), updated_at=utc_now(), phase=RuntimePhase.PENDING_ENTRY
    )
    live = replace(deployment, mode=DeploymentMode.LIVE)
    target = Decimal("120")

    assert _attaches_bracket(
        deployment, strategy=strategy, target_price=target, product_id="BTC-USD"
    )
    assert _attaches_bracket(live, strategy=strategy, target_price=target, product_id="BTC-USD")
    assert not _attaches_bracket(
        live, strategy=strategy, target_price=target, product_id="BIP-20DEC30-CDE"
    )
    assert not _attaches_bracket(
        deployment, strategy=strategy, target_price=None, product_id="BTC-USD"
    )


def _deployment_stub() -> Deployment:
    now = utc_now()
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000"),
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
    )


def test_attached_child_is_protection_not_a_working_entry() -> None:
    """L1: a filled entry's attached TP/SL child shares the ENTRY intent but is not an entry."""
    deployment = _deployment_stub()
    now = utc_now()
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id="entry-1",
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        created_at=now,
        candle_starts_at=now,
        product_id="BTC-USD",
    )
    parent = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=intent.id,
        client_order_id="entry-1",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("1"),
        created_at=now,
        updated_at=now,
        venue_order_id="v-parent",
        product_id="BTC-USD",
    )
    child = replace(
        parent,
        id=uuid4(),
        client_order_id="entry-1:child",
        side=OrderSide.SELL,
        kind=OrderKind.TRIGGER_BRACKET,
        price=Decimal("120"),
        stop_trigger_price=Decimal("90"),
        take_profit_price=Decimal("120"),
        status=OrderStatus.OPEN,
        filled_quantity=Decimal("0"),
        venue_order_id="v-child",
        parent_order_id=parent.id,
    )
    # Postgres loads newest first, so the child precedes its parent.
    snapshot = DeploymentSnapshot(deployment=deployment, orders=(child, parent), intents=(intent,))

    assert _active_entry(snapshot) is None
    working_parent = replace(parent, status=OrderStatus.OPEN, filled_quantity=Decimal("0"))
    resting = replace(snapshot, orders=(child, working_parent))
    assert _active_entry(resting) == working_parent


class _RacingPaper(_ScriptedPaper):
    """Paper broker whose cancel or post-only create can report executions at once.

    Their fills may be made unreadable, as when the venue's fill listing lags.
    """

    def __init__(self) -> None:
        super().__init__()
        self.cancel_filled = Decimal("0")
        self.fill_now = False
        self.fills_readable = True
        self.fills: dict[str, tuple[Fill, ...]] = {}

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        result = await super().cancel_order(
            venue_order_id=venue_order_id, client_order_id=client_order_id
        )
        if self.cancel_filled > 0:
            self.fills[venue_order_id] = (
                _venue_fill(venue_order_id, self.cancel_filled, minutes=1),
            )
            return replace(result, filled_quantity=self.cancel_filled)
        return result

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
        if self.fill_now and kind is OrderKind.POST_ONLY_LIMIT:
            self.fills[client_order_id] = (_venue_fill(client_order_id, quantity, minutes=2),)
            return SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id=client_order_id,
                filled_quantity=quantity,
            )
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

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        del product_id
        if not self.fills_readable:
            return ()
        return self.fills.get(order_id or "", ())


def _venue_fill(venue_order_id: str, quantity: Decimal, *, minutes: int) -> Fill:
    now = utc_now()
    return Fill(
        id=uuid4(),
        deployment_id=uuid4(),
        order_id=uuid4(),
        venue_fill_id=f"{venue_order_id}:race",
        price=Decimal("129"),
        quantity=quantity,
        fee=Decimal("0"),
        filled_at=now + timedelta(minutes=minutes),
        venue_order_id=venue_order_id,
    )


@pytest.mark.parametrize("policy", ["cancel", "reprice"])
async def test_executions_reported_by_the_cancel_open_the_position_with_its_stop(
    policy: str,
) -> None:
    """A cancel that raced a partial fill applies it before abandoning, keeping the stop."""
    broker = _RacingPaper()
    store, strategy, current, warmup = await _rested(broker, policy=policy)
    stop = current.deployment.pending_stop_price
    broker.cancel_filled = current.orders[0].quantity / 4
    if policy == "reprice":
        # A latched book may not reprice: the cancel and its race fill still apply.
        await store.save_deployment(
            with_runtime(current.deployment, updated_at=utc_now(), daily_loss_latched=True)
        )
        current = await store.get_deployment(current.deployment.id)

    current = await _expire(store, current, strategy=strategy, warmup=warmup, broker=broker)

    assert current.position is not None
    assert current.position.quantity == broker.cancel_filled
    assert current.position.stop_price == stop
    assert current.deployment.phase in {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}
    if policy == "cancel":
        assert current.deployment.mismatch_detail is None


async def test_unreadable_cancel_race_executions_pause_and_keep_the_stop() -> None:
    """Executions the venue reported but whose fills cannot be read yet keep the pending levels."""
    broker = _RacingPaper()
    store, strategy, current, warmup = await _rested(broker, policy="cancel")
    stop = current.deployment.pending_stop_price
    broker.cancel_filled = current.orders[0].quantity / 4
    broker.fills_readable = False

    current = await _expire(store, current, strategy=strategy, warmup=warmup, broker=broker)

    assert current.position is None
    assert current.deployment.status is DeploymentStatus.PAUSED
    assert current.deployment.mismatch_detail == CANCEL_RACE_FILLS_PENDING_DETAIL
    assert current.deployment.phase is RuntimePhase.PENDING_ENTRY
    assert current.deployment.pending_stop_price == stop


async def test_a_replacement_filled_at_once_opens_the_position() -> None:
    """A reprice the venue reports filled on create is applied, not left pending entry."""
    broker = _RacingPaper()
    store, strategy, current, warmup = await _rested(broker)
    broker.fill_now = True

    current = await _expire(store, current, strategy=strategy, warmup=warmup, broker=broker)

    assert current.position is not None
    assert current.deployment.phase is RuntimePhase.OPEN
    assert current.deployment.status is DeploymentStatus.RUNNING
    assert current.deployment.mismatch_detail is None


async def test_a_futures_reprice_sizes_with_its_fee_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S6: a futures reprice passes the worker's fee evidence to sizing, as its first entry does."""
    seen: list[FeeProfile | None] = []

    def _capture(*args: object, fee_profile: FeeProfile | None = None, **kwargs: object) -> object:
        del args, kwargs
        seen.append(fee_profile)
        return EntrySkipReason.ECONOMICS_FEE_UNAVAILABLE

    monkeypatch.setattr(entry_reprice, "_size_entry_or_add", _capture)
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy(on_unfilled_entry="reprice")
    snapshot = await _running_snapshot(store, strategy)
    fees = FeeProfile(
        taker_fee_rate=Decimal("0.0003"),
        maker_fee_rate=Decimal("0"),
        usd_volume_30d=Decimal("0"),
        fee_tier="synthetic",
        as_of=utc_now(),
    )
    futures = replace(_product(), product_id="BIP-20DEC30-CDE")
    candles = _candles(30, low_offset=Decimal("0"))
    prior = Order(
        id=uuid4(),
        deployment_id=snapshot.deployment.id,
        intent_id=uuid4(),
        client_order_id="expired",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        status=OrderStatus.CANCELED,
        created_at=utc_now(),
        updated_at=utc_now(),
        venue_order_id="expired",
        product_id=futures.product_id,
    )

    await entry_reprice._reprice_entry(
        snapshot,
        strategy=strategy,
        candles=candles,
        candle=candles[-1],
        product=futures,
        broker=PaperBroker(),
        store=store,
        prior=prior,
        fee_profile=fees,
    )

    assert seen == [fees]


async def test_an_entry_the_cancel_found_filled_is_not_rested_again() -> None:
    """A cancel that finds the whole entry filled applies it and rests no replacement."""
    broker = _RacingPaper()
    store, strategy, current, warmup = await _rested(broker)
    original = current.orders[0]
    broker.cancel_filled = original.quantity

    current = await _expire(store, current, strategy=strategy, warmup=warmup, broker=broker)

    entries = [order for order in current.orders if order.kind is OrderKind.POST_ONLY_LIMIT]
    assert [order.id for order in entries if order.side is OrderSide.BUY] == [original.id]
    assert current.position is not None
    assert current.position.quantity == original.quantity
    assert current.deployment.mismatch_detail is None


async def test_a_discretionary_entry_awaiting_its_fills_keeps_the_pending_levels() -> None:
    """A filled discretionary entry whose fills are not applied yet is not flattened."""
    store = InMemoryExecutionStore()
    deployment = replace(
        _deployment_stub(),
        kind=DeploymentKind.DISCRETIONARY,
        mode=DeploymentMode.LIVE,
        timeframe="1h",
        phase=RuntimePhase.PENDING_ENTRY,
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
    )
    await store.create_deployment(deployment)
    now = utc_now()
    parent = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="desk-entry",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("1"),
        created_at=now,
        updated_at=now,
        venue_order_id="desk-entry",
        product_id="BTC-USD",
    )
    child = replace(
        parent,
        id=uuid4(),
        client_order_id="desk-entry:child",
        side=OrderSide.SELL,
        kind=OrderKind.TRIGGER_BRACKET,
        status=OrderStatus.OPEN,
        filled_quantity=Decimal("0"),
        venue_order_id="desk-child",
        parent_order_id=parent.id,
    )
    await store.save_order(parent)
    await store.save_order(child)

    watched = await _watch_pending_entry(await store.get_deployment(deployment.id), store=store)

    assert watched.deployment.phase is RuntimePhase.PENDING_ENTRY
    assert watched.deployment.pending_stop_price == Decimal("90")
    assert watched.deployment.pending_target_price == Decimal("120")
