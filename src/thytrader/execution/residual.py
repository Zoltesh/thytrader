"""Residual inventory: flatten, cancel-and-settle, and stopped-book settlement.

Flattening of stopped or requested residual books (deferred while no executable
price exists), cancel-before-exit sequencing, and settlement of flat books.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventOutcome
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.exit_guards import (
    FLAT_AFTER_FAULT_DETAIL,
    flat_and_idle,
    flatten_requested,
    stale_position_fault,
)
from thytrader.execution.exits import _marketable_exit
from thytrader.execution.paper import bind_paper_broker_fees
from thytrader.execution.runtime_ops import (
    _ACTIVE,
    _cancel_one_order,
    _reconcile_stopped_live,
    cancel_resting_orders,
)
from thytrader.trading.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    is_venue_protection,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.store import ExecutionStore


FLATTEN_AWAITING_EXECUTABLE_CONTEXT = (
    "Flatten is pending: no verified closed price is available, so protective orders "
    "were kept and no exit was submitted."
)
"""Operator detail when flatten cannot exit without inventing a price."""


async def flatten_stopped_residual(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Marketably exit open inventory on a flatten command, then cancel remainders.

    Protective children are cancelled before the exit (``_marketable_exit``); once the
    book is flat with nothing working it settles as STOPPED/FLAT with no stale detail.
    Without a verified candle the position is not exited and protection is not cancelled.
    """
    return await flatten_residual_book(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        broker=broker,
        store=store,
        cooldown_bars=strategy.entry.cooldown_bars,
    )


async def flatten_residual_book(
    snapshot: DeploymentSnapshot,
    *,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    strategy: StrategyDefinition | None = None,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """Exit one book only after cancels and live fills are known.

    An empty candle sequence never submits an exit and never cancels protection.
    A cancel that races a fill is reconciled before another exit is sent. An
    unconfirmed cancel stays supervised and is not treated as success.
    """
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    if snapshot.position is not None and not candles:
        return await defer_flatten_without_executable_context(snapshot, broker=broker, store=store)
    if snapshot.position is None:
        return await _cancel_and_settle(snapshot, broker=broker, store=store)
    return await _exit_after_confirmed_cancels(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        broker=broker,
        store=store,
        cooldown_bars=cooldown_bars,
    )


async def defer_flatten_without_executable_context(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Keep protection and record that flatten has no verified exit price.

    Only orders whose intent is an entry may be cancelled. Venue brackets,
    stop-limits, and take-profit orders stay so the book is not left naked.
    """
    snapshot = await _cancel_open_entries(snapshot, broker=broker, store=store)
    current = await _reconcile_stopped_live(
        await store.get_deployment(snapshot.deployment.id), broker=broker, store=store
    )
    if current.deployment.mismatch_detail is not None:
        # Do not hide a genuine reconciliation/cancellation fault behind a data wait.
        return current
    noted = with_runtime(
        current.deployment,
        updated_at=utc_now(),
        status=current.deployment.status,
        mismatch_detail=FLATTEN_AWAITING_EXECUTABLE_CONTEXT,
    )
    await store.save_deployment(noted)
    await record_execution_audit(
        action="flatten_awaiting_price",
        outcome=AuditEventOutcome.FAILURE,
        detail=(
            f"deployment_id={current.deployment.id}: flatten has no verified closed price; "
            "protective orders were kept and no exit was submitted."
        ),
        product_id=current.position.product_id if current.position is not None else None,
    )
    return await store.get_deployment(current.deployment.id)


async def _cancel_and_settle(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel remainders on a book that no longer has inventory, then settle."""
    cleared = await cancel_resting_orders(snapshot, broker=broker, store=store)
    cleared = await _reconcile_stopped_live(cleared, broker=broker, store=store)
    return await _settle_flat_book(cleared, store=store)


async def _exit_after_confirmed_cancels(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Cancel resting orders, learn any racing fill, then exit only if still open."""
    candle = candles[-1]
    exited = await _marketable_exit(
        snapshot,
        strategy=strategy,
        cooldown_bars=cooldown_bars,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        purpose=IntentPurpose.STOP,
        price=candle.close,
    )
    if exited.position is not None:
        return exited
    return await _cancel_and_settle(exited, broker=broker, store=store)


async def _cancel_open_entries(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel working entries and leave protective orders untouched."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    for order in tuple(snapshot.orders):
        if order.status not in _ACTIVE or order.intent_id not in entry_ids:
            continue
        if is_venue_protection(order.kind):
            continue
        snapshot = await _cancel_one_order(order, broker=broker, store=store)
    return await store.get_deployment(snapshot.deployment.id)


def _unsettled_fill_evidence(snapshot: DeploymentSnapshot) -> bool:
    """Detect unapplied economics, including canceled orders' executed remainders."""
    return unsettled_fill_evidence(snapshot)


async def settle_stopped_book(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Finish a requested flatten once every book is flat and idle."""
    return await _settle_flat_book(snapshot, store=store)


async def _settle_flat_book(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Finish a requested flatten and retire position-only pause details once flat.

    A flatten that reaches flat with nothing working ends STOPPED/FLAT with no detail,
    even when an earlier fault had paused it. On other books a detail that only described
    the open position's protection or exit is overwritten (paused) or cleared.
    """
    deployment = snapshot.deployment
    if not flat_and_idle(snapshot) or _unsettled_fill_evidence(snapshot):
        return snapshot
    # Recorded inventory evidence survives unrelated display faults and restart.
    if unprojected_inventory_products(snapshot):
        return snapshot
    if flatten_requested(snapshot) and deployment.status in {
        DeploymentStatus.PAUSED,
        DeploymentStatus.STOPPED,
    }:
        if deployment.status is DeploymentStatus.STOPPED and deployment.mismatch_detail is None:
            return snapshot
        settled = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.STOPPED,
            clear_mismatch=True,
            pending_entry_bars=0,
        )
        await store.save_deployment(settled)
        await record_execution_audit(
            action="flatten_settled",
            outcome=AuditEventOutcome.SUCCESS,
            detail=(
                f"deployment_id={deployment.id} previous_status={deployment.status.value}: "
                "requested flatten reached flat with no working orders; status stopped."
            ),
            product_id=deployment.product_id,
        )
        return await store.get_deployment(deployment.id)
    if not stale_position_fault(deployment.mismatch_detail):
        return snapshot
    if deployment.status is DeploymentStatus.PAUSED:
        refreshed = with_runtime(
            deployment, updated_at=utc_now(), mismatch_detail=FLAT_AFTER_FAULT_DETAIL
        )
    else:
        refreshed = with_runtime(deployment, updated_at=utc_now(), clear_mismatch=True)
    await store.save_deployment(refreshed)
    return await store.get_deployment(deployment.id)
