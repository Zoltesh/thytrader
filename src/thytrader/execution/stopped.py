"""Supervise stopped books until inventory and orders are settled.

Managed shutdown and explicit flatten stay distinct. A missing verified price never
cancels protection or counts as a successful flatten. Live orders and fills are
reconciled before a book is treated as flat, including after restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from thytrader.execution.exit_guards import active_orders, flat_and_idle
from thytrader.execution.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.execution.ids import utc_now
from thytrader.execution.live_protection import maintain_discretionary_protection
from thytrader.execution.loop import cancel_risk_increasing_orders, maintain_open_inventory
from thytrader.execution.models import (
    DeploymentMode,
    DeploymentStatus,
    LifecycleCommand,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
    with_runtime,
)
from thytrader.execution.overlay import InstrumentScopedStore
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.residual import (
    defer_flatten_without_executable_context,
    flatten_residual_book,
    settle_stopped_book,
)
from thytrader.execution.runtime_ops import cancel_resting_orders
from thytrader.market_data.models import MarketProduct, parse_candle_interval
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.strategies.models import lockstep_product_ids

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle
    from thytrader.market_data.service import MarketDataService
    from thytrader.strategies.models import StrategyDefinition

STOPPED_BROKER_UNAVAILABLE = "Live broker is unavailable; stopped residual remains supervised."
"""Detail recorded when a stopped live book cannot be reconciled."""

_DISCRETIONARY_WARMUP_BARS = 3


class ClosedWindowLoader(Protocol):
    """Load one product's deploy-anchored closed window without fabricating bars."""

    async def __call__(
        self,
        market_data: MarketDataService,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """Return the product, closed candles, and expected last bar start."""
        ...


class SignalWindowLoader(Protocol):
    """Load optional signal-exit clocks for one stopped product."""

    async def __call__(
        self,
        strategy: StrategyDefinition,
        *,
        product_id: str,
        deploy_anchor: datetime,
    ) -> tuple[tuple[Candle, ...], Mapping[str, Sequence[Candle]], Mapping[str, Sequence[Candle]]]:
        """Return HTF, extra-timeframe, and reference windows, or empty on a gap."""
        ...


class StrategyBarJournal(Protocol):
    """Journal one stopped strategy bar without authorizing a new entry."""

    async def __call__(
        self,
        snapshot: DeploymentSnapshot,
        *,
        strategy: StrategyDefinition,
        product: MarketProduct,
        candles: tuple[Candle, ...],
        advance: Callable[[], Awaitable[DeploymentSnapshot]],
        require_activity: bool,
    ) -> None:
        """Run ``advance`` and journal it when the worker has a decision store."""
        ...


@dataclass(frozen=True, slots=True)
class _ExitContext:
    """A provider-returned product and closed candles safe to price an exit."""

    product: MarketProduct
    candles: tuple[Candle, ...]
    full_window: bool = True


async def supervise_stopped_deployment(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    load_closed_window: ClosedWindowLoader,
    load_signal_windows: SignalWindowLoader | None = None,
    journal_strategy_bar: StrategyBarJournal | None = None,
) -> None:
    """Reconcile, then flatten or maintain every book on a stopped deployment."""
    broker = _stopped_broker(snapshot, paper_broker=paper_broker, live_broker=live_broker)
    if broker is None:
        await _note_broker_unavailable(snapshot, store=store)
        return
    snapshot = await _reconcile_stopped(snapshot, broker=broker, store=store)
    if snapshot.deployment.lifecycle_command is LifecycleCommand.FLATTEN:
        await _flatten_stopped_books(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            broker=broker,
            load_closed_window=load_closed_window,
            journal_strategy_bar=journal_strategy_bar,
        )
        return
    await _maintain_managed_shutdown(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        broker=broker,
        load_closed_window=load_closed_window,
        load_signal_windows=load_signal_windows,
        journal_strategy_bar=journal_strategy_bar,
    )


def stopped_product_ids(
    snapshot: DeploymentSnapshot, strategy: StrategyDefinition | None
) -> tuple[str, ...]:
    """Return every product a stopped book must supervise, including a sole secondary."""
    products = {snapshot.deployment.product_id}
    if strategy is not None:
        products.update(lockstep_product_ids(strategy))
    products.update(
        resolved_product_id(position.product_id, snapshot.deployment)
        for position in snapshot_positions(snapshot)
    )
    products.update(
        resolved_product_id(order.product_id, snapshot.deployment) for order in snapshot.orders
    )
    products.update(runtime.product_id for runtime in snapshot.instrument_runtimes)
    return tuple(sorted(product_id for product_id in products if product_id))


async def load_verified_exit_context(
    market_data: MarketDataService,
    *,
    product_id: str,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
    load_closed_window: ClosedWindowLoader,
) -> _ExitContext | None:
    """Return a provider candle context, never a synthesized price."""
    window = await _window_context(
        market_data,
        product_id=product_id,
        timeframe=timeframe,
        warmup_bars=warmup_bars,
        deploy_anchor=deploy_anchor,
        load_closed_window=load_closed_window,
    )
    if window is not None:
        return window
    return await _preview_context(market_data, product_id=product_id, timeframe=timeframe)


async def _flatten_stopped_books(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    broker: Broker,
    load_closed_window: ClosedWindowLoader,
    journal_strategy_bar: StrategyBarJournal | None,
) -> None:
    """Flatten every supervised product, then settle only when the deployment is flat."""
    for product_id in stopped_product_ids(snapshot, strategy):
        await _flatten_one_product(
            snapshot,
            product_id=product_id,
            strategy=strategy,
            store=store,
            market_data=market_data,
            broker=broker,
            load_closed_window=load_closed_window,
            journal_strategy_bar=journal_strategy_bar,
        )
    latest = await _reconcile_stopped(
        await store.get_deployment(snapshot.deployment.id), broker=broker, store=store
    )
    if flat_and_idle(latest):
        await settle_stopped_book(latest, store=store)


async def _flatten_one_product(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    broker: Broker,
    load_closed_window: ClosedWindowLoader,
    journal_strategy_bar: StrategyBarJournal | None,
) -> None:
    """Flatten one product book, or keep its protection when no price is verified."""
    scoped = InstrumentScopedStore(store, product_id)
    focused = await scoped.get_deployment(snapshot.deployment.id)
    if unprojected_inventory_products(focused) or unsettled_fill_evidence(focused):
        # Durable execution evidence, not a display fault, decides whether flat is known.
        await cancel_risk_increasing_orders(focused, broker=broker, store=scoped)
        return
    if focused.position is None and not active_orders(focused):
        if focused.deployment.phase is not RuntimePhase.FLAT:
            await scoped.save_deployment(
                with_runtime(
                    focused.deployment,
                    updated_at=utc_now(),
                    phase=RuntimePhase.FLAT,
                    pending_entry_bars=0,
                )
            )
        return
    context = await _context_for(
        snapshot,
        strategy=strategy,
        product_id=product_id,
        market_data=market_data,
        load_closed_window=load_closed_window,
    )
    if focused.position is not None and context is None:
        await defer_flatten_without_executable_context(focused, broker=broker, store=scoped)
        return
    if context is None:
        await cancel_resting_orders(focused, broker=broker, store=scoped)
        return
    await _exit_with_context(
        focused,
        context=context,
        strategy=strategy,
        broker=broker,
        store=scoped,
        journal_strategy_bar=journal_strategy_bar,
    )


async def _exit_with_context(
    snapshot: DeploymentSnapshot,
    *,
    context: _ExitContext,
    strategy: StrategyDefinition | None,
    broker: Broker,
    store: ExecutionStore,
    journal_strategy_bar: StrategyBarJournal | None,
) -> None:
    """Exit one focused book at its verified close, journaling strategy books."""
    cooldown = 0 if strategy is None else strategy.entry.cooldown_bars

    async def _advance() -> DeploymentSnapshot:
        """Submit the verified flatten without a new entry."""
        return await flatten_residual_book(
            snapshot,
            strategy=strategy,
            product=context.product,
            candles=context.candles,
            broker=broker,
            store=store,
            cooldown_bars=cooldown,
        )

    if strategy is None or journal_strategy_bar is None:
        await _advance()
        return
    await journal_strategy_bar(
        snapshot,
        strategy=strategy,
        product=context.product,
        candles=context.candles,
        advance=_advance,
        require_activity=True,
    )


async def _maintain_managed_shutdown(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    broker: Broker,
    load_closed_window: ClosedWindowLoader,
    load_signal_windows: SignalWindowLoader | None,
    journal_strategy_bar: StrategyBarJournal | None,
) -> None:
    """Cancel entries, keep protection, and continue risk-reducing exits per book."""
    await cancel_risk_increasing_orders(snapshot, broker=broker, store=store)
    current = await _reconcile_stopped(
        await store.get_deployment(snapshot.deployment.id), broker=broker, store=store
    )
    for product_id in stopped_product_ids(current, strategy):
        await _maintain_one_product(
            current,
            product_id=product_id,
            strategy=strategy,
            store=store,
            market_data=market_data,
            broker=broker,
            load_closed_window=load_closed_window,
            load_signal_windows=load_signal_windows,
            journal_strategy_bar=journal_strategy_bar,
        )


async def _maintain_one_product(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    broker: Broker,
    load_closed_window: ClosedWindowLoader,
    load_signal_windows: SignalWindowLoader | None,
    journal_strategy_bar: StrategyBarJournal | None,
) -> None:
    """Maintain one open book, doing nothing when its price context is missing."""
    scoped = InstrumentScopedStore(store, product_id)
    focused = await scoped.get_deployment(snapshot.deployment.id)
    if focused.position is None:
        return
    context = await _context_for(
        snapshot,
        strategy=strategy,
        product_id=product_id,
        market_data=market_data,
        load_closed_window=load_closed_window,
    )
    if context is None:
        return
    if strategy is None or not context.full_window:
        await maintain_discretionary_protection(
            focused,
            product=context.product,
            candles=context.candles,
            broker=broker,
            store=scoped,
            strategy=strategy,
        )
        return
    await _maintain_strategy_book(
        focused,
        strategy=strategy,
        context=context,
        store=scoped,
        broker=broker,
        load_signal_windows=load_signal_windows,
        journal_strategy_bar=journal_strategy_bar,
        deploy_anchor=snapshot.deployment.created_at,
    )


async def _maintain_strategy_book(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    context: _ExitContext,
    store: ExecutionStore,
    broker: Broker,
    load_signal_windows: SignalWindowLoader | None,
    journal_strategy_bar: StrategyBarJournal | None,
    deploy_anchor: datetime,
) -> None:
    """Continue stops, targets, and signal exits for one stopped strategy book."""
    htf: tuple[Candle, ...] = ()
    extra: Mapping[str, Sequence[Candle]] = {}
    references: Mapping[str, Sequence[Candle]] = {}
    if load_signal_windows is not None:
        try:
            htf, extra, references = await load_signal_windows(
                strategy, product_id=context.product.product_id, deploy_anchor=deploy_anchor
            )
        except WindowCacheWarmingError:
            await maintain_discretionary_protection(
                snapshot,
                product=context.product,
                candles=context.candles,
                broker=broker,
                store=store,
                strategy=strategy,
            )
            return

    async def _advance() -> DeploymentSnapshot:
        """Maintain protection and risk-reducing exits for this stopped book."""
        return await maintain_open_inventory(
            snapshot,
            strategy=strategy,
            product=context.product,
            candles=context.candles,
            broker=broker,
            store=store,
            htf_candles=htf,
            indicator_timeframe_candles=extra,
            reference_candles=references,
        )

    if journal_strategy_bar is None:
        await _advance()
        return
    await journal_strategy_bar(
        snapshot,
        strategy=strategy,
        product=context.product,
        candles=context.candles,
        advance=_advance,
        require_activity=False,
    )


async def _context_for(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    product_id: str,
    market_data: MarketDataService,
    load_closed_window: ClosedWindowLoader,
) -> _ExitContext | None:
    """Load a verified exit context for one supervised product."""
    return await load_verified_exit_context(
        market_data,
        product_id=product_id,
        timeframe=_timeframe(snapshot, strategy),
        warmup_bars=_warmup_bars(strategy),
        deploy_anchor=snapshot.deployment.created_at,
        load_closed_window=load_closed_window,
    )


async def _window_context(
    market_data: MarketDataService,
    *,
    product_id: str,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
    load_closed_window: ClosedWindowLoader,
) -> _ExitContext | None:
    """Use the deploy-anchored window when it contains provider candles."""
    try:
        product, candles, _expected = await load_closed_window(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
        )
    except OSError, RuntimeError, TypeError, ValueError:
        return None
    if not _executable_context(product, candles, product_id=product_id, timeframe=timeframe):
        return None
    return _ExitContext(product=product, candles=tuple(candles))


async def _preview_context(
    market_data: MarketDataService,
    *,
    product_id: str,
    timeframe: str,
) -> _ExitContext | None:
    """Use a provider preview's last closed candle when the anchored window is empty."""
    try:
        interval = parse_candle_interval(timeframe)
        preview = await market_data.get_preview(product_id, interval)
    except OSError, RuntimeError, TypeError, ValueError:
        return None
    if preview.interval is not interval or preview.quality.is_stale:
        return None
    closed = preview.quality.candles
    if not _executable_context(preview.product, closed, product_id=product_id, timeframe=timeframe):
        return None
    return _ExitContext(product=preview.product, candles=closed, full_window=False)


def _executable_context(
    product: MarketProduct,
    candles: Sequence[Candle],
    *,
    product_id: str,
    timeframe: str,
) -> bool:
    """Require venue constraints and a traded, aligned, most-recent closed price.

    Historical/synthesized no-trade closes and in-progress bars are not executable
    context. A stale context cannot authorize cancellation of protection.
    """
    if not candles or product.product_id != product_id or not product.trading_enabled:
        return False
    constraints = (
        product.price_increment,
        product.base_increment,
        product.quote_increment,
        product.base_min_size,
        product.quote_min_size,
    )
    if any(not value.is_finite() or value <= 0 for value in constraints):
        return False
    interval = parse_candle_interval(timeframe)
    latest = candles[-1]
    expected = interval.align_closed_end(utc_now()) - interval.duration
    return (
        latest.starts_at == expected
        and latest.close.is_finite()
        and latest.close > 0
        and latest.volume.is_finite()
        and latest.volume > 0
    )


async def _reconcile_stopped(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Learn live order and fill state before deciding a stopped book is flat."""
    if snapshot.deployment.mode is not DeploymentMode.LIVE:
        return snapshot
    return await reconcile_open_orders(
        snapshot,
        broker=broker,
        store=store,
        product_id=snapshot.deployment.product_id,
        cooldown_bars=0,
    )


async def _note_broker_unavailable(snapshot: DeploymentSnapshot, *, store: ExecutionStore) -> None:
    """Record that a stopped live book cannot be reconciled, without cancelling orders."""
    deployment = snapshot.deployment
    if deployment.mismatch_detail is not None:
        return
    noted = with_runtime(
        deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.STOPPED,
        mismatch_detail=STOPPED_BROKER_UNAVAILABLE,
    )
    await store.save_deployment(noted)


def _stopped_broker(
    snapshot: DeploymentSnapshot,
    *,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> Broker | None:
    """Select the live broker only when this stopped book is live."""
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        return live_broker
    return paper_broker


def _timeframe(snapshot: DeploymentSnapshot, strategy: StrategyDefinition | None) -> str:
    """Return the book clock used to look for a verified exit candle."""
    if strategy is not None:
        return strategy.timeframe
    return snapshot.deployment.timeframe or "1h"


def _warmup_bars(strategy: StrategyDefinition | None) -> int:
    """Return the strategy warmup, or a short discretionary lookback."""
    if strategy is None:
        return _DISCRETIONARY_WARMUP_BARS
    return strategy.data_requirements.warmup_bars
