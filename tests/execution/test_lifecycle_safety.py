"""Reload-safe regressions for the five integrated lifecycle safety findings."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import pytest

from tests.execution.test_adr_0110_stopped_lifecycle import (
    _WHEN,
    _as_market_data,
    _candle,
    _deployment,
    _order,
    _preview,
    _PricedPreview,
    _product,
    _remote_fill,
    _strategy,
    _supervise as _supervise_persisted,
    _Venue,
)
from tests.worker_patching import patch_worker_global
from thytrader.execution.fill_ledger import (
    ingest_fill,
    replay_unapplied_fills,
    unprojected_inventory_products,
    unsettled_fill_evidence,
)
from thytrader.execution.loop import (
    cancel_resting_orders,
    maintain_open_inventory,
    settle_stopped_book,
)
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    DeploymentStatus,
    InstrumentRuntime,
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
    snapshot_positions,
)
from thytrader.execution.overlay import InstrumentScopedStore
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution_worker import service
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.strategies.models import StrategyDefinition

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.execution.broker import SubmitResult
    from thytrader.execution.models import DeploymentSnapshot, Order
    from thytrader.market_data.models import Candle, MarketDataPreview, MarketProduct


@dataclass(frozen=True)
class _Submission:
    """Retain the exact product, quantity and geometry sent to the fake venue."""

    product_id: str
    quantity: Decimal
    kind: OrderKind
    stop: Decimal | None
    target: Decimal | None


@dataclass
class _QuantityVenue(_Venue):
    """Script executed-quantity lag and record exact protection/exit submissions."""

    reported: dict[str, Decimal] = field(default_factory=dict)
    submissions: list[_Submission] = field(default_factory=list)

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
        """Supply exact-size fills for exits and keep submitted protection working."""
        self.submissions.append(
            _Submission(
                product_id,
                quantity,
                kind,
                stop_trigger_price,
                price if kind is OrderKind.TRIGGER_BRACKET else take_profit_price,
            )
        )
        result = await super().place_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            kind=kind,
            quantity=quantity,
            price=price,
            stop_trigger_price=stop_trigger_price,
            take_profit_price=take_profit_price,
        )
        venue_id = result.venue_order_id
        assert venue_id is not None
        self.statuses[venue_id] = [result.status]
        if kind is OrderKind.MARKETABLE:
            self.reported[venue_id] = quantity
            self.fills[venue_id] = [
                (replace(_remote_fill(venue_id), quantity=quantity, price=price or Decimal(0)),)
            ]
            return replace(result, filled_quantity=quantity)
        return result

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Report executions independently of whether List Fills has published them."""
        result = await super().get_order(
            venue_order_id=venue_order_id, client_order_id=client_order_id
        )
        return replace(result, filled_quantity=self.reported.get(venue_order_id, Decimal(0)))


class _ProductPreview(_PricedPreview):
    """Expose genuinely different BTC/ETH prices with matching product metadata."""

    async def get_preview(self, product_id: str, interval: CandleInterval) -> MarketDataPreview:
        """Return only this product's latest traded candle."""
        price = Decimal("201" if product_id == "ETH-USD" else "101")
        candle = replace(self._candle, open=price, high=price + 1, low=price - 1, close=price)
        return _preview(product_id, interval, (candle,))


def _warming_error() -> WindowCacheWarmingError:
    """Supply a genuine bounded-prefetch signal, not a fake data-gap exception."""
    return WindowCacheWarmingError(
        product_id="BTC-USD",
        interval=CandleInterval.ONE_HOUR,
        starts_at=_WHEN,
        scanned_through=_WHEN,
        requested_end=_candle().starts_at,
        range_requests=1,
    )


def _multi_strategy() -> StrategyDefinition:
    """Cover BTC and ETH with valid Coinbase-first declarative rules."""
    payload = _strategy().model_dump(mode="python")
    payload["additional_instruments"] = [
        {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
    ]
    return StrategyDefinition.model_validate(payload)


async def _book(
    store: InMemoryExecutionStore,
    *,
    status: DeploymentStatus = DeploymentStatus.PAUSED,
    command: LifecycleCommand = LifecycleCommand.NONE,
) -> UUID:
    """Persist a real parent and two product runtime rows before any fills."""
    parent = await _deployment(store, status=status, command=command, detail="Operator pause.")
    await store.save_deployment(
        replace(parent, created_at=_WHEN, last_evaluated_bar=_candle().starts_at)
    )
    for product_id in ("BTC-USD", "ETH-USD"):
        await store.save_instrument_runtime(
            InstrumentRuntime(product_id=product_id, phase=RuntimePhase.FLAT),
            deployment_id=parent.id,
        )
    return parent.id


async def _entry(
    store: InMemoryExecutionStore,
    deployment_id: UUID,
    product_id: str,
    *,
    missing: Literal["stop", "timeframe"] | None = None,
    legacy_product: bool = False,
    stop_only: bool = False,
) -> Order:
    """Apply an intent-backed entry through the actual scoped atomic-fill boundary."""
    secondary = product_id == "ETH-USD"
    await store.save_instrument_runtime(
        InstrumentRuntime(
            product_id=product_id,
            phase=RuntimePhase.PENDING_ENTRY,
            pending_stop_price=None if missing == "stop" else Decimal("190" if secondary else "90"),
            pending_target_price=None if stop_only else Decimal("240" if secondary else "120"),
            last_evaluated_bar=_WHEN,
        ),
        deployment_id=deployment_id,
    )
    if missing == "timeframe":
        current = await store.get_deployment(deployment_id)
        await store.save_deployment(replace(current.deployment, timeframe=None))
    order = await _order(
        store,
        deployment_id,
        venue_order_id=f"{product_id}-entry",
        product_id="" if legacy_product else product_id,
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
        status=OrderStatus.FILLED,
    )
    scoped = InstrumentScopedStore(store, product_id)
    await ingest_fill(
        await scoped.get_deployment(deployment_id),
        fill=replace(
            _remote_fill(order.venue_order_id or ""),
            deployment_id=deployment_id,
            order_id=order.id,
            price=Decimal("200" if secondary else "100"),
        ),
        order=order,
        store=scoped,
    )
    return order


async def _supervise(
    store: InMemoryExecutionStore,
    broker: _QuantityVenue,
    deployment_id: UUID,
    *,
    candles: dict[str, tuple[Candle, ...]],
) -> None:
    """Run the stopped loop on reloaded rows using the test's explicit traded candle."""
    snapshot = await store.get_deployment(deployment_id)
    await _supervise_persisted(
        store, snapshot.deployment, broker, candles=next(iter(candles.values()))
    )


async def _restart(snapshot: DeploymentSnapshot) -> InMemoryExecutionStore:
    """Reload only persisted rows into a fresh store, with no process-local guard state."""
    store = InMemoryExecutionStore()
    await store.create_deployment(snapshot.deployment)
    for runtime in snapshot.instrument_runtimes:
        await store.save_instrument_runtime(runtime, deployment_id=snapshot.deployment.id)
    for position in snapshot_positions(snapshot):
        await store.save_position(position, deployment_id=snapshot.deployment.id)
    for intent in snapshot.intents:
        await store.save_intent(intent)
    for order in snapshot.orders:
        await store.save_order(order)
    for fill in snapshot.fills:
        await store.save_fill(fill)
    return store


@pytest.mark.anyio
@pytest.mark.parametrize("secondary_only", [True, False])
@pytest.mark.parametrize("path", ["no_due", "gap", "missing", "warming", "feed_down"])
async def test_worker_fallback_supervises_each_owned_product_after_restart(
    monkeypatch: pytest.MonkeyPatch, secondary_only: bool, path: str
) -> None:
    """Primary metadata never supplies a secondary book's stop or order product."""
    store = InMemoryExecutionStore()
    deployment_id = await _book(store)
    if not secondary_only:
        await _entry(store, deployment_id, "BTC-USD")
    await _entry(store, deployment_id, "ETH-USD")
    current = await store.get_deployment(deployment_id)
    if path == "gap":
        await store.save_deployment(
            replace(current.deployment, last_evaluated_bar=_candle().starts_at - timedelta(hours=2))
        )
    before = await store.get_deployment(deployment_id)
    store = await _restart(before)
    broker = _QuantityVenue()
    candle = _candle()
    strategy = _multi_strategy()

    async def _warming(*_args: object, **_kwargs: object) -> None:
        """Force verified-preview maintenance instead of full signal history."""
        raise _warming_error()

    patch_worker_global(monkeypatch, "_closed_window_for", _warming)
    snapshot = await store.get_deployment(deployment_id)
    market_data = _as_market_data(_ProductPreview(candle))
    if path == "warming":
        await service._supervise_warming_window(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=PaperBroker(),
            live_broker=broker,
        )
    else:

        async def _closed(
            *_args: object, **_kwargs: object
        ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
            """Expose only the tested decision window; previews stay independently verified."""
            return _product(), (() if path == "missing" else (candle,)), candle.starts_at

        async def _feed(*_args: object, **_kwargs: object) -> bool:
            """Exercise the existing no-entry feed fallback without mutating a pause."""
            return path == "feed_down"

        patch_worker_global(monkeypatch, "_closed_window", _closed)
        patch_worker_global(monkeypatch, "_pause_five_minute_live_if_feed_down", _feed)
        await service._advance_strategy_ready(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=PaperBroker(),
            live_broker=broker,
            quote_reader=None,
            risk_policy=compiled_default_risk_policy(),
            portfolio=(snapshot,),
            user_feed_store=None,
            memory_store=None,
        )
    submissions = {item.product_id: item for item in broker.submissions}
    assert set(submissions) == ({"ETH-USD"} if secondary_only else {"BTC-USD", "ETH-USD"})
    assert submissions["ETH-USD"].stop == Decimal("190")
    assert submissions["ETH-USD"].target == Decimal("240")
    assert all(item.quantity == Decimal("0.01") for item in submissions.values())
    assert all(item.kind is OrderKind.TRIGGER_BRACKET for item in submissions.values())
    if not secondary_only:
        assert submissions["BTC-USD"].stop == Decimal("90")
    after = await store.get_deployment(deployment_id)
    assert after.deployment.status is DeploymentStatus.PAUSED
    assert after.deployment.last_evaluated_bar == before.deployment.last_evaluated_bar
    assert after.deployment.mismatch_detail == "Operator pause."
    assert len(after.positions) == (1 if secondary_only else 2)


@pytest.mark.anyio
@pytest.mark.parametrize("published", ["0", "0.002"])
async def test_canceled_executions_block_exit_until_all_fragments_survive_reload(
    published: str,
) -> None:
    """An executed cancel does not release authority to sell the pre-fill quantity."""
    store = InMemoryExecutionStore()
    deployment_id = await _book(
        store, status=DeploymentStatus.STOPPED, command=LifecycleCommand.FLATTEN
    )
    await _entry(store, deployment_id, "BTC-USD")
    protection = await _order(store, deployment_id, venue_order_id="protect", product_id="BTC-USD")
    broker = _QuantityVenue(statuses={"protect": [OrderStatus.CANCELED]})
    broker.reported["protect"] = Decimal("0.004")
    fragments = (
        ()
        if published == "0"
        else (
            replace(_remote_fill("protect"), quantity=Decimal(published), venue_fill_id="partial"),
        )
    )
    broker.fills["protect"] = [fragments]
    await _supervise(store, broker, deployment_id, candles={"BTC-USD": (_candle(),)})
    waiting = await store.get_deployment(deployment_id)
    assert unsettled_fill_evidence(waiting)
    assert broker.submissions == []
    assert waiting.deployment.status is DeploymentStatus.STOPPED
    stored_order = next(order for order in waiting.orders if order.id == protection.id)
    assert stored_order.status is OrderStatus.CANCELED
    assert stored_order.filled_quantity == Decimal("0.004")
    store = await _restart(waiting)
    await _supervise(store, broker, deployment_id, candles={"BTC-USD": (_candle(),)})
    assert broker.submissions == []
    rest = Decimal("0.004") - Decimal(published)
    broker.fills["protect"] = [
        (*fragments, replace(_remote_fill("protect"), quantity=rest, venue_fill_id="remainder"))
    ]
    await _supervise(store, broker, deployment_id, candles={"BTC-USD": (_candle(),)})
    assert len(broker.submissions) == 1
    assert broker.submissions[0].quantity == Decimal("0.006")
    assert broker.submissions[0].kind is OrderKind.MARKETABLE
    settled = await store.get_deployment(deployment_id)
    assert settled.positions == ()
    assert not unsettled_fill_evidence(settled)
    assert not unprojected_inventory_products(settled)
    assert settled.deployment.status is DeploymentStatus.STOPPED
    assert settled.deployment.mismatch_detail is None


@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["stop", "timeframe"])
async def test_unprojected_inventory_survives_new_read_fault_restart_and_cleared_detail(
    missing: Literal["stop", "timeframe"],
) -> None:
    """Cash-applied inventory retains protection even when its display fault is overwritten."""
    store = InMemoryExecutionStore()
    deployment_id = await _book(
        store, status=DeploymentStatus.STOPPED, command=LifecycleCommand.FLATTEN
    )
    await _entry(store, deployment_id, "ETH-USD", missing=missing)
    protection = await _order(store, deployment_id, venue_order_id="protect", product_id="ETH-USD")
    first = await store.get_deployment(deployment_id)
    assert first.positions == ()
    assert first.deployment.cash == Decimal("9997.99")
    assert (first.deployment.mismatch_detail or "").startswith("Entry fill is missing")
    assert unprojected_inventory_products(first) == ("ETH-USD",)
    broker = _QuantityVenue(get_error={"protect"})
    await _supervise(store, broker, deployment_id, candles={"ETH-USD": (_candle(close="201"),)})
    faulted = await store.get_deployment(deployment_id)
    assert "order read failed" in (faulted.deployment.mismatch_detail or "")
    assert not any(event == "cancel" for event, _ in broker.events)
    store = await _restart(faulted)
    broker.get_error.clear()
    # Clearing/replacing display state cannot manufacture proof of zero inventory.
    await store.save_deployment(replace(faulted.deployment, mismatch_detail=None))
    await _supervise(store, broker, deployment_id, candles={"ETH-USD": (_candle(close="201"),)})
    after = await store.get_deployment(deployment_id)
    await cancel_resting_orders(after, broker=broker, store=store)
    retained = await store.get_deployment(deployment_id)
    await settle_stopped_book(retained, store=store)
    assert unprojected_inventory_products(retained) == ("ETH-USD",)
    assert (
        next(order for order in retained.orders if order.id == protection.id).status
        is OrderStatus.OPEN
    )
    assert retained.deployment.cash == first.deployment.cash
    assert retained.fills == first.fills
    assert broker.submissions == []
    assert not any(event == "cancel" for event, _ in broker.events)


@pytest.mark.anyio
@pytest.mark.parametrize("legacy_product", [False, True])
async def test_atomic_entry_and_exit_persist_focused_runtime_and_parent_cursor(
    legacy_product: bool,
) -> None:
    """Atomic fills update actual runtime rows, leaving siblings and scheduling intact."""
    store = InMemoryExecutionStore()
    deployment_id = await _book(store)
    before = await store.get_deployment(deployment_id)
    entry = await _entry(
        store, deployment_id, "ETH-USD", legacy_product=legacy_product, stop_only=True
    )
    opened = await store.get_deployment(deployment_id)
    assert opened.deployment.phase is RuntimePhase.OPEN
    assert opened.deployment.last_evaluated_bar == before.deployment.last_evaluated_bar
    assert opened.instrument_runtimes[0] == before.instrument_runtimes[0]
    focused = await InstrumentScopedStore(store, "ETH-USD").get_deployment(deployment_id)
    assert focused.deployment.phase is RuntimePhase.OPEN
    assert focused.deployment.pending_stop_price is None
    assert focused.position is not None and focused.position.product_id == "ETH-USD"
    store = await _restart(opened)
    replayed = await replay_unapplied_fills(await store.get_deployment(deployment_id), store=store)
    assert replayed.deployment.cash == opened.deployment.cash
    broker = _QuantityVenue()
    scoped = InstrumentScopedStore(store, "ETH-USD")
    await maintain_open_inventory(
        await scoped.get_deployment(deployment_id),
        strategy=_multi_strategy(),
        store=scoped,
        product=_product("ETH-USD"),
        candles=(_candle(close="201"),),
        broker=broker,
    )
    assert [item.product_id for item in broker.submissions] == ["ETH-USD"]
    assert broker.submissions[0].kind is OrderKind.STOP_LIMIT
    protection = next(
        order
        for order in (await store.get_deployment(deployment_id)).orders
        if order.id != entry.id
    )
    broker.statuses[protection.venue_order_id or ""] = [OrderStatus.FILLED]
    broker.reported[protection.venue_order_id or ""] = Decimal("0.01")
    broker.fills[protection.venue_order_id or ""] = [
        (_remote_fill(protection.venue_order_id or ""),)
    ]
    await reconcile_open_orders(
        await store.get_deployment(deployment_id), broker=broker, store=store, cooldown_bars=3
    )
    closed = await store.get_deployment(deployment_id)
    restarted = await _restart(closed)
    final = await InstrumentScopedStore(restarted, "ETH-USD").get_deployment(deployment_id)
    assert final.position is None
    assert final.deployment.phase is RuntimePhase.FLAT
    assert final.deployment.cooldown_bars_remaining == 3
    assert closed.deployment.phase is RuntimePhase.FLAT
    assert closed.instrument_runtimes[0] == before.instrument_runtimes[0]
    assert closed.deployment.last_evaluated_bar == before.deployment.last_evaluated_bar


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["signal", "time", "flatten"])
async def test_warming_continues_durable_exit_instead_of_resting_new_protection(
    monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    """A verified close can finish a committed exit without warming signal history."""
    store = InMemoryExecutionStore()
    deployment_id = await _book(store)
    await _entry(store, deployment_id, "ETH-USD")
    focused = await InstrumentScopedStore(store, "ETH-USD").get_deployment(deployment_id)
    assert focused.position is not None
    if decision == "signal":
        await store.save_position(
            replace(focused.position, signal_exit_bar=_candle().starts_at),
            deployment_id=deployment_id,
        )
    elif decision == "time":
        await InstrumentScopedStore(store, "ETH-USD").save_deployment(
            replace(focused.deployment, bars_held=_multi_strategy().exits.time_exit.max_bars_held)
        )
    else:
        await store.save_deployment(
            replace(focused.deployment, lifecycle_command=LifecycleCommand.FLATTEN)
        )
    protection = await _order(
        store,
        deployment_id,
        venue_order_id="protect",
        product_id="ETH-USD",
        status=OrderStatus.CANCELED,
    )
    before = await store.get_deployment(deployment_id)
    store = await _restart(before)
    broker = _QuantityVenue(statuses={"protect": [OrderStatus.CANCELED]})

    async def _warming(*_args: object, **_kwargs: object) -> None:
        """Evict history while keeping a verified executable preview available."""
        raise _warming_error()

    patch_worker_global(monkeypatch, "_closed_window_for", _warming)
    await service._supervise_warming_window(
        await store.get_deployment(deployment_id),
        strategy=_multi_strategy(),
        store=store,
        market_data=_as_market_data(_PricedPreview(_candle(close="201"))),
        paper_broker=PaperBroker(),
        live_broker=broker,
    )
    assert len(broker.submissions) == 1
    assert broker.submissions[0].product_id == "ETH-USD"
    assert broker.submissions[0].kind is OrderKind.MARKETABLE
    after = await store.get_deployment(deployment_id)
    purpose = next(
        intent.purpose
        for intent in after.intents
        if intent.id not in {order.intent_id for order in before.orders}
    )
    assert (
        purpose
        is {
            "signal": IntentPurpose.SIGNAL_EXIT,
            "time": IntentPurpose.TIME_EXIT,
            "flatten": IntentPurpose.STOP,
        }[decision]
    )
    assert after.positions == ()
    assert after.deployment.last_evaluated_bar == before.deployment.last_evaluated_bar
    assert after.deployment.status is DeploymentStatus.PAUSED
    assert (
        next(order for order in after.orders if order.id == protection.id).status
        is OrderStatus.CANCELED
    )


@pytest.mark.anyio
async def test_legacy_exit_cannot_offset_later_unprojected_entry_after_restart() -> None:
    """A closed pre-ledger holding is not evidence that a later owned entry is flat."""
    store = InMemoryExecutionStore()
    deployment_id = await _book(store)
    legacy_exit = await _order(
        store,
        deployment_id,
        venue_order_id="legacy-exit",
        product_id="ETH-USD",
        kind=OrderKind.MARKETABLE,
        purpose=IntentPurpose.STOP,
        status=OrderStatus.FILLED,
    )
    # Legacy inventory predates retained entry evidence; its economics already applied.
    await store.save_fill(
        replace(
            _remote_fill("legacy-exit"),
            deployment_id=deployment_id,
            order_id=legacy_exit.id,
            filled_at=_WHEN - timedelta(hours=1),
            economics_applied_at=_WHEN - timedelta(hours=1),
        )
    )
    await _entry(store, deployment_id, "ETH-USD", missing="stop")
    before = await store.get_deployment(deployment_id)
    store = await _restart(before)
    await store.save_deployment(
        replace(before.deployment, status=DeploymentStatus.RUNNING, mismatch_detail=None)
    )
    reconciled = await reconcile_open_orders(
        await store.get_deployment(deployment_id), broker=_QuantityVenue(), store=store
    )
    assert unprojected_inventory_products(reconciled) == ("ETH-USD",)
    assert reconciled.deployment.status is DeploymentStatus.PAUSED
    assert "unprojected inventory" in (reconciled.deployment.mismatch_detail or "")
    assert reconciled.deployment.cash == before.deployment.cash
    assert reconciled.fills == before.fills


@pytest.mark.anyio
async def test_warming_without_verified_price_keeps_durable_exit_and_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable exit is not permission to strip protection without executable context."""
    store = InMemoryExecutionStore()
    deployment_id = await _book(store)
    await _entry(store, deployment_id, "ETH-USD")
    before = await InstrumentScopedStore(store, "ETH-USD").get_deployment(deployment_id)
    assert before.position is not None
    marker = _candle().starts_at
    await store.save_position(
        replace(before.position, signal_exit_bar=marker), deployment_id=deployment_id
    )
    await _order(store, deployment_id, venue_order_id="protect", product_id="ETH-USD")
    store = await _restart(await store.get_deployment(deployment_id))
    broker = _QuantityVenue()

    async def _warming(*_args: object, **_kwargs: object) -> None:
        """Keep history unavailable for this worker cycle."""
        raise _warming_error()

    patch_worker_global(monkeypatch, "_closed_window_for", _warming)
    stale = replace(_candle(close="201"), starts_at=marker - timedelta(hours=1))
    await service._supervise_warming_window(
        await store.get_deployment(deployment_id),
        strategy=_multi_strategy(),
        store=store,
        market_data=_as_market_data(_PricedPreview(stale)),
        paper_broker=PaperBroker(),
        live_broker=broker,
    )
    after = await InstrumentScopedStore(store, "ETH-USD").get_deployment(deployment_id)
    assert after.position is not None
    assert after.position.signal_exit_bar == marker
    assert after.deployment.last_evaluated_bar == before.deployment.last_evaluated_bar
    assert after.deployment.status is DeploymentStatus.PAUSED
    assert broker.submissions == []
    assert not any(event == "cancel" for event, _ in broker.events)
