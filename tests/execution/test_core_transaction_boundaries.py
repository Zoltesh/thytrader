"""Counterexamples at actual execution and wrapper write boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Never

import pytest

from tests.execution.test_adr_0110_stopped_lifecycle import (
    _as_market_data,
    _candle,
    _order,
    _PricedPreview,
    _product,
    _remote_fill,
)
from tests.execution.test_lifecycle_safety import (
    _book,
    _entry,
    _multi_strategy,
    _QuantityVenue,
    _warming_error,
)
from tests.loop_patching import patch_loop_global
from tests.risk.test_loss_scope import _TODAY, _deployment, _policy, _round_trip
from tests.risk.test_safety_evidence import _MIDNIGHT, overnight_long, seed_accounting, sibling_loss
from tests.worker_patching import patch_worker_global
from thytrader.execution.discretionary import place_discretionary_order
from thytrader.execution.discretionary_request import parse_discretionary_request
from thytrader.execution.leases import RevisionFencedStore
from thytrader.execution.loop import (
    _apply_circuit_breakers,
    _persist_performance,
    maintain_open_inventory,
    process_closed_bar,
)
from thytrader.execution.paper import PaperBroker
from thytrader.execution.stopped import supervise_stopped_deployment
from thytrader.execution_worker import service
from thytrader.risk.opening_accounting import reconstruct_day_open
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.trading.day_open import MidnightMark
from thytrader.trading.fill_ledger import ingest_fill, unsettled_fill_evidence
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    InstrumentRuntime,
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.broker import SubmitResult
    from thytrader.trading.models import Deployment, DeploymentSnapshot, Order


class _CancelRaceVenue(_QuantityVenue):
    """Learn executed quantity only at cancellation, with configurable fill publication."""

    publication = Decimal(0)

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Expose the cancel GET's watermark independently of applied REST evidence."""
        result = await super().cancel_order(
            venue_order_id=venue_order_id, client_order_id=client_order_id
        )
        self.reported[venue_order_id] = Decimal("0.004")
        self.fills[venue_order_id] = [
            ()
            if not self.publication
            else (replace(_remote_fill(venue_order_id), quantity=self.publication),)
        ]
        return replace(result, filled_quantity=Decimal("0.004"))


@pytest.mark.anyio
@pytest.mark.parametrize("product_id", ["BTC-USD", "ETH-USD"])
@pytest.mark.parametrize("stop_only", [False, True])
@pytest.mark.parametrize("publication", ["0", "0.002", "0.004"])
async def test_replacement_revalidates_execution_learned_during_cancel(
    monkeypatch: pytest.MonkeyPatch, product_id: str, stop_only: bool, publication: str
) -> None:
    """Warming replacement never guesses quantity; complete applied fills allow 0.006."""
    store = InMemoryExecutionStore()
    identity = await _book(store)
    await _entry(store, identity, product_id, stop_only=stop_only)
    scoped = InstrumentScopedStore(store, product_id)
    before = await scoped.get_deployment(identity)
    assert before.position is not None
    await scoped.save_position(
        replace(before.position, stop_price=Decimal("195" if product_id == "ETH-USD" else "95")),
        deployment_id=identity,
    )
    await _order(store, identity, venue_order_id="old-bracket", product_id=product_id)
    broker = _CancelRaceVenue()
    broker.publication = Decimal(publication)

    async def warming(*args: object, **kwargs: object) -> Never:
        """Require the real product-preview fallback without manufacturing history."""
        del args, kwargs
        raise _warming_error()

    patch_worker_global(monkeypatch, "_closed_window_for", warming)
    await service._supervise_warming_window(
        await store.get_deployment(identity),
        strategy=_multi_strategy(),
        store=store,
        market_data=_as_market_data(
            _PricedPreview(_candle(close="201" if product_id == "ETH-USD" else "101"))
        ),
        paper_broker=PaperBroker(),
        live_broker=broker,
    )
    after = await scoped.get_deployment(identity)
    assert after.position is not None
    if publication != "0.004":
        assert unsettled_fill_evidence(after)
        assert not broker.submissions
    else:
        assert not unsettled_fill_evidence(after)
        assert after.position.quantity == Decimal("0.006")
        assert len(broker.submissions) == 1
        assert broker.submissions[0].quantity == Decimal("0.006")
        assert broker.submissions[0].product_id == product_id
        assert broker.submissions[0].kind is (
            OrderKind.STOP_LIMIT if stop_only else OrderKind.TRIGGER_BRACKET
        )
    # Reload, with complete economics or still-missing REST evidence, remains truthful.
    fresh = InstrumentScopedStore(
        RevisionFencedStore(store, identity, after.deployment.revision), product_id
    )
    await maintain_open_inventory(
        await fresh.get_deployment(identity),
        strategy=_multi_strategy(),
        product=_product(product_id),
        candles=(_candle(close="201" if product_id == "ETH-USD" else "101"),),
        broker=broker,
        store=fresh,
    )
    assert len(broker.submissions) == (1 if publication == "0.004" else 0)


@pytest.mark.anyio
async def test_known_unresolved_execution_keeps_useful_cover() -> None:
    """Preexisting unprojected evidence is not authority to cancel useful native cover."""
    store = InMemoryExecutionStore()
    identity = await _book(store)
    await _entry(store, identity, "BTC-USD")
    old = await _order(store, identity, venue_order_id="old-bracket", product_id="BTC-USD")
    await store.save_order(replace(old, filled_quantity=Decimal("0.004")))
    scoped = InstrumentScopedStore(store, "BTC-USD")
    broker = _CancelRaceVenue()
    await maintain_open_inventory(
        await scoped.get_deployment(identity),
        strategy=_multi_strategy(),
        product=_product("BTC-USD"),
        candles=(_candle(),),
        broker=broker,
        store=scoped,
    )
    assert not any(action == "cancel" for action, _identity in broker.events)
    assert not broker.submissions


class _FillBeforeSave(InMemoryExecutionStore):
    """Commit an actual entry immediately before the revision-gated parent write."""

    race_order: Order | None = None

    async def _race(self, deployment_id: UUID) -> None:
        """Fill at either the old partial runtime write or new atomic save boundary."""
        order = self.race_order
        if order is not None:
            self.race_order = None
            await ingest_fill(
                await self.get_deployment(deployment_id),
                fill=replace(
                    _remote_fill(order.venue_order_id or ""),
                    deployment_id=deployment_id,
                    order_id=order.id,
                ),
                order=order,
                store=self,
            )

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Expose the old independently committed stale runtime update."""
        await self._race(deployment_id)
        await super().save_instrument_runtime(runtime, deployment_id=deployment_id)

    async def save_deployment(
        self,
        deployment: Deployment,
        *,
        expected_revision: int | None = None,
        instrument_runtime: InstrumentRuntime | None = None,
    ) -> Deployment:
        """Race at the persistence boundary, supporting the proposed atomic optional runtime."""
        await self._race(deployment.id)
        if instrument_runtime is None:
            return await super().save_deployment(deployment, expected_revision=expected_revision)
        return await super().save_deployment(
            deployment, expected_revision=expected_revision, instrument_runtime=instrument_runtime
        )


@pytest.mark.anyio
@pytest.mark.parametrize("scope_outer", [True, False])
async def test_rejected_performance_cas_has_no_runtime_effect_and_next_bar_protects(
    scope_outer: bool,
) -> None:
    """Both wrapper orders reject the entire stale operation, then protect after reload."""
    store = _FillBeforeSave()
    identity = await _book(store)
    await store.save_instrument_runtime(
        InstrumentRuntime(
            "ETH-USD",
            RuntimePhase.PENDING_ENTRY,
            pending_stop_price=Decimal("90"),
            pending_target_price=Decimal("120"),
        ),
        deployment_id=identity,
    )
    order = await _order(
        store,
        identity,
        venue_order_id="entry",
        product_id="ETH-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
    )
    revision = (await store.get_deployment(identity)).deployment.revision
    scoped = (
        InstrumentScopedStore(RevisionFencedStore(store, identity, revision), "ETH-USD")
        if scope_outer
        else RevisionFencedStore(InstrumentScopedStore(store, "ETH-USD"), identity, revision)
    )
    before = await scoped.get_deployment(identity)
    store.race_order = order
    after = await _persist_performance(
        before, store=scoped, mark_price=Decimal("101"), product_id="ETH-USD"
    )
    full = await store.get_deployment(identity)
    assert full.deployment.cash == Decimal("9998.98")
    assert full.deployment.phase is RuntimePhase.OPEN
    assert after.deployment.phase is RuntimePhase.OPEN
    assert after.deployment.pending_stop_price is None
    fresh = InstrumentScopedStore(
        RevisionFencedStore(store, identity, full.deployment.revision), "ETH-USD"
    )
    broker = _QuantityVenue()
    await maintain_open_inventory(
        await fresh.get_deployment(identity),
        strategy=_multi_strategy(),
        product=_product("ETH-USD"),
        candles=(_candle(),),
        broker=broker,
        store=fresh,
    )
    assert len(broker.submissions) == 1
    assert broker.submissions[0].quantity == Decimal("0.01")
    assert broker.submissions[0].product_id == "ETH-USD"


@pytest.mark.anyio
@pytest.mark.parametrize("scope_outer", [True, False])
async def test_refreshed_wrapper_fence_cannot_bless_an_old_candidate(scope_outer: bool) -> None:
    """Even this wrapper's own applied fill cannot turn old runtime/cash into a current write."""
    store = InMemoryExecutionStore()
    identity = await _book(store)
    await store.save_instrument_runtime(
        InstrumentRuntime(
            "ETH-USD",
            RuntimePhase.PENDING_ENTRY,
            pending_stop_price=Decimal("90"),
            pending_target_price=Decimal("120"),
        ),
        deployment_id=identity,
    )
    order = await _order(
        store,
        identity,
        venue_order_id="entry",
        product_id="ETH-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
    )
    revision = (await store.get_deployment(identity)).deployment.revision
    scoped = (
        InstrumentScopedStore(RevisionFencedStore(store, identity, revision), "ETH-USD")
        if scope_outer
        else RevisionFencedStore(InstrumentScopedStore(store, "ETH-USD"), identity, revision)
    )
    before = await scoped.get_deployment(identity)
    await ingest_fill(
        before,
        fill=replace(_remote_fill("entry"), deployment_id=identity, order_id=order.id),
        order=order,
        store=scoped,
    )
    committed = await store.get_accounting_snapshot(identity)
    with pytest.raises(ExecutionConflictError, match="revision"):
        await scoped.save_deployment(before.deployment)
    assert await store.get_accounting_snapshot(identity) == committed


class _PeerRaceStore(InMemoryExecutionStore):
    """Fill and change operator intent after the portfolio read, before the peer mutation."""

    race_peer: UUID | None = None
    stop_peer = False

    async def _race(self, deployment_id: UUID) -> None:
        """Apply economics and optionally stop/latch independently of the triggering worker."""
        if deployment_id != self.race_peer:
            return
        self.race_peer = None
        await _entry(self, deployment_id, "ETH-USD")
        full = await self.get_deployment(deployment_id)
        await super().save_deployment(
            replace(
                full.deployment,
                drawdown_latched=True,
                status=DeploymentStatus.STOPPED if self.stop_peer else DeploymentStatus.RUNNING,
                lifecycle_command=LifecycleCommand.MANAGED_SHUTDOWN
                if self.stop_peer
                else LifecycleCommand.STOP_NEW_ENTRIES,
            )
        )

    async def save_deployment(
        self,
        deployment: Deployment,
        *,
        expected_revision: int | None = None,
        instrument_runtime: InstrumentRuntime | None = None,
    ) -> Deployment:
        """Retain the old write hook so the negative control reproduces the cash overwrite."""
        if deployment.status is DeploymentStatus.PAUSED:
            await self._race(deployment.id)
        if instrument_runtime is None:
            return await super().save_deployment(deployment, expected_revision=expected_revision)
        return await super().save_deployment(
            deployment, expected_revision=expected_revision, instrument_runtime=instrument_runtime
        )

    async def save_breaker_pause(
        self,
        deployment_id: UUID,
        *,
        expected_revision: int,
        detail: str,
        daily_loss_latched: bool = False,
    ) -> Deployment:
        """Race the new metadata-only conditional operation instead of bypassing it."""
        await self._race(deployment_id)
        return await super().save_breaker_pause(
            deployment_id,
            expected_revision=expected_revision,
            detail=detail,
            daily_loss_latched=daily_loss_latched,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("stop_peer", [False, True])
async def test_quote_pause_preserves_peer_fill_lifecycle_latches_and_runtimes(
    monkeypatch: pytest.MonkeyPatch, stop_peer: bool
) -> None:
    """Scoped source fence cannot overwrite peer economics or a deliberate concurrent stop."""
    store = _PeerRaceStore()
    source = sibling_loss(live=True)
    await seed_accounting(store, source)
    peer = await _book(store, status=DeploymentStatus.RUNNING)
    before = await store.get_deployment(peer)
    await store.save_deployment(replace(before.deployment, phase=RuntimePhase.FLAT))
    store.race_peer, store.stop_peer = peer, stop_peer
    scoped = InstrumentScopedStore(
        RevisionFencedStore(store, source.deployment.id, source.deployment.revision), "BTC-USD"
    )
    patch_loop_global(monkeypatch, "utc_now", lambda: _TODAY)
    await _apply_circuit_breakers(
        await scoped.get_deployment(source.deployment.id),
        candle=_candle(),
        product_id="BTC-USD",
        store=scoped,
        risk_policy=_policy(),
        portfolio=(source,),
        marks={"BTC-USD": Decimal("100")},
    )
    after = await store.get_deployment(peer)
    assert after.deployment.cash == Decimal("9997.99")
    assert after.deployment.phase is RuntimePhase.OPEN
    assert after.deployment.drawdown_latched
    assert after.deployment.status is (
        DeploymentStatus.STOPPED if stop_peer else DeploymentStatus.PAUSED
    )
    assert after.deployment.lifecycle_command is (
        LifecycleCommand.MANAGED_SHUTDOWN if stop_peer else LifecycleCommand.STOP_NEW_ENTRIES
    )
    assert (
        next(r for r in after.instrument_runtimes if r.product_id == "ETH-USD").phase
        is RuntimePhase.OPEN
    )
    assert len(after.positions) == 1 and after.fills[0].economics_applied_at is not None
    assert after.deployment.revision > before.deployment.revision


class _StaleReuseStore(InMemoryExecutionStore):
    """The listing is genuinely FLAT; newer evidence may arrive before pending mutation."""

    race_id: UUID | None = None
    self_reads = 0

    async def get_accounting_snapshot(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Count authoritative candidate reads, not only peer reads."""
        self.self_reads += 1
        return await super().get_accounting_snapshot(deployment_id)

    async def save_deployment(
        self,
        deployment: Deployment,
        *,
        expected_revision: int | None = None,
        instrument_runtime: InstrumentRuntime | None = None,
    ) -> Deployment:
        """Commit a lifecycle update in the actual read-to-pending-write window."""
        if deployment.id == self.race_id and deployment.phase is RuntimePhase.PENDING_ENTRY:
            self.race_id = None
            current = await self.get_deployment(deployment.id)
            await super().save_deployment(
                replace(
                    current.deployment,
                    status=DeploymentStatus.STOPPED,
                    lifecycle_command=LifecycleCommand.MANAGED_SHUTDOWN,
                    drawdown_latched=True,
                )
            )
        if instrument_runtime is None:
            return await super().save_deployment(deployment, expected_revision=expected_revision)
        return await super().save_deployment(
            deployment, expected_revision=expected_revision, instrument_runtime=instrument_runtime
        )


@pytest.mark.anyio
async def test_actual_discretionary_reuse_fences_pending_after_authoritative_self_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feasible FLAT candidate race denies before persisting or submitting new intent."""
    store = _StaleReuseStore()
    root = replace(
        _deployment(
            strategy_id=None,
            mode=DeploymentMode.LIVE,
            cash=Decimal("0"),
            initial_equity=Decimal("0"),
            paper_starting_cash=None,
            performance_capital_quote=Decimal("10000"),
        ),
        timeframe="1h",
        venue_available_quote=Decimal("10000"),
        high_water_mark_equity=Decimal("0"),
    )
    await store.create_deployment(root)
    store.race_id = root.id
    request = parse_discretionary_request(
        mode="live",
        product_id="BTC-USD",
        entry_kind="post_only_limit",
        quantity="0.01",
        limit_price="101",
        stop_price="90",
        take_profit_price="120",
        origin="agent",
        idempotency_key="reuse-race",
        timeframe="1h",
    )
    broker = _QuantityVenue()
    monkeypatch.setattr("thytrader.execution.discretionary.utc_now", lambda: _TODAY)
    monkeypatch.setattr("thytrader.execution.discretionary_book.utc_now", lambda: _TODAY)
    with pytest.raises(ExecutionConflictError, match="revision"):
        await place_discretionary_order(
            store=store,
            broker=broker,
            market_data=_as_market_data(
                _PricedPreview(replace(_candle(), starts_at=_TODAY - timedelta(hours=1)))
            ),
            request=request,
            live_allowed=True,
            live_quote_cash=Decimal("10000"),
        )
    after = await store.get_deployment(root.id)
    assert store.self_reads > 0
    assert after.deployment.status is DeploymentStatus.STOPPED
    assert after.deployment.lifecycle_command is LifecycleCommand.MANAGED_SHUTDOWN
    assert after.deployment.drawdown_latched
    assert not broker.submissions and not after.intents


@pytest.mark.anyio
async def test_actual_reuse_denies_authoritative_recorded_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete loss evidence, not a fabricated closing-fill eligibility story, denies reuse."""
    store = _StaleReuseStore()
    root = replace(
        _deployment(
            strategy_id=None,
            mode=DeploymentMode.LIVE,
            cash=Decimal("-50"),
            initial_equity=Decimal("0"),
            paper_starting_cash=None,
            performance_capital_quote=Decimal("10000"),
        ),
        timeframe="1h",
        venue_available_quote=Decimal("10000"),
        high_water_mark_equity=Decimal("0"),
    )
    await seed_accounting(
        store,
        _round_trip(root, buy_at=_TODAY - timedelta(hours=2), sell_at=_TODAY - timedelta(hours=1)),
    )
    risk_store = InMemoryRiskPolicyStore()
    await risk_store.publish(_policy())
    broker = _QuantityVenue()
    monkeypatch.setattr("thytrader.execution.discretionary.utc_now", lambda: _TODAY)
    monkeypatch.setattr("thytrader.execution.discretionary_book.utc_now", lambda: _TODAY)
    request = parse_discretionary_request(
        mode="live",
        product_id="BTC-USD",
        entry_kind="post_only_limit",
        quantity="0.01",
        limit_price="101",
        stop_price="90",
        take_profit_price="120",
        origin="agent",
        idempotency_key="reuse-loss",
        timeframe="1h",
    )
    with pytest.raises(ExecutionConflictError, match="Daily"):
        await place_discretionary_order(
            store=store,
            broker=broker,
            market_data=_as_market_data(
                _PricedPreview(replace(_candle(), starts_at=_TODAY - timedelta(hours=1)))
            ),
            request=request,
            live_allowed=True,
            risk_store=risk_store,
            live_quote_cash=Decimal("10000"),
        )
    assert store.self_reads > 0
    assert (await store.get_deployment(root.id)).deployment.cash == Decimal("-50")
    assert not broker.submissions


@pytest.mark.anyio
@pytest.mark.parametrize(
    "primary_mark,secondary_mark,expected_latch", [("100", "1", False), ("1", "100", True)]
)
async def test_scoped_closed_bar_binds_actual_product_mark(
    monkeypatch: pytest.MonkeyPatch, primary_mark: str, secondary_mark: str, expected_latch: bool
) -> None:
    """Divergent sibling prices neither fabricate nor conceal loss with true opening proof."""
    full = overnight_long()
    proof = reconstruct_day_open(
        full,
        as_of=_TODAY,
        marks=(MidnightMark(product_id="BTC-USD", closes_at=_MIDNIGHT, price=Decimal("100")),),
    )
    assert proof is not None
    full = replace(
        full,
        deployment=replace(full.deployment, risk_day_open_evidence=proof, timeframe="1h"),
        instrument_runtimes=(
            InstrumentRuntime("BTC-USD", RuntimePhase.OPEN),
            InstrumentRuntime("ETH-USD", RuntimePhase.FLAT),
        ),
    )
    store = InMemoryExecutionStore()
    await seed_accounting(store, full)
    scoped = InstrumentScopedStore(
        RevisionFencedStore(store, full.deployment.id, full.deployment.revision), "ETH-USD"
    )
    patch_loop_global(monkeypatch, "utc_now", lambda: _TODAY)
    after = await process_closed_bar(
        await scoped.get_deployment(full.deployment.id),
        strategy=_multi_strategy(),
        product=_product("ETH-USD"),
        candles=(replace(_candle(close=secondary_mark), starts_at=_TODAY - timedelta(hours=1)),),
        broker=PaperBroker(),
        store=scoped,
        risk_policy=_policy(),
        portfolio=(full,),
        marks={"BTC-USD": Decimal(primary_mark), "ETH-USD": Decimal(secondary_mark)},
        allow_new_entries=False,
    )
    assert after.deployment.daily_loss_latched is expected_latch


@pytest.mark.anyio
async def test_stopped_preview_forwards_reached_strategy_time_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing full history retains strategy exit semantics without advancing any bar."""
    del monkeypatch
    store = InMemoryExecutionStore()
    identity = await _book(
        store, status=DeploymentStatus.STOPPED, command=LifecycleCommand.MANAGED_SHUTDOWN
    )
    await _entry(store, identity, "ETH-USD")
    scoped = InstrumentScopedStore(store, "ETH-USD")
    current = await scoped.get_deployment(identity)
    await scoped.save_deployment(
        replace(current.deployment, bars_held=_multi_strategy().exits.time_exit.max_bars_held)
    )
    await _order(
        store,
        identity,
        venue_order_id="old-bracket",
        product_id="ETH-USD",
        status=OrderStatus.CANCELED,
    )
    broker = _QuantityVenue(statuses={"old-bracket": [OrderStatus.CANCELED]})

    async def warming(*args: object, **kwargs: object) -> Never:
        """The verified preview is available but the full history genuinely is warming."""
        del args, kwargs
        raise _warming_error()

    await supervise_stopped_deployment(
        await store.get_deployment(identity),
        strategy=_multi_strategy(),
        store=store,
        market_data=_as_market_data(_PricedPreview(_candle(close="201"))),
        paper_broker=PaperBroker(),
        live_broker=broker,
        load_closed_window=warming,
    )
    after = await scoped.get_deployment(identity)
    assert len(broker.submissions) == 1
    assert broker.submissions[0].kind is OrderKind.MARKETABLE
    assert any(i.purpose is IntentPurpose.TIME_EXIT for i in after.intents)
    assert after.position is None
    assert after.deployment.status is DeploymentStatus.STOPPED
    assert after.deployment.last_evaluated_bar == current.deployment.last_evaluated_bar
