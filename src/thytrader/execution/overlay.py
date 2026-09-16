"""Per-product overlay of the single-book closed-bar loop."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    Fill,
    InstrumentRuntime,
    Order,
    OrderIntent,
    OrderStatus,
    Position,
    RuntimePhase,
    aggregate_phase,
    resolved_product_id,
    runtime_from_deployment,
    snapshot_positions,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.store import ExecutionStore


def overlay_snapshot(snapshot: DeploymentSnapshot, product_id: str) -> DeploymentSnapshot:
    """Focus one product's runtime, position, and orders onto the shared deployment row."""
    runtime = _runtime_for(snapshot, product_id)
    deployment = replace(
        snapshot.deployment,
        phase=runtime.phase,
        last_evaluated_bar=runtime.last_evaluated_bar,
        last_signal=runtime.last_signal,
        pending_entry_bars=runtime.pending_entry_bars,
        bars_held=runtime.bars_held,
        cooldown_bars_remaining=runtime.cooldown_bars_remaining,
        pending_stop_price=runtime.pending_stop_price,
        pending_target_price=runtime.pending_target_price,
    )
    return DeploymentSnapshot(
        deployment=deployment,
        position=_position_for(snapshot, product_id),
        orders=_orders_for(snapshot, product_id),
        fills=_fills_for(snapshot, product_id),
        intents=_intents_for(snapshot, product_id),
        positions=snapshot_positions(snapshot),
        instrument_runtimes=_ensure_runtime(snapshot, runtime),
    )


def _runtime_for(snapshot: DeploymentSnapshot, product_id: str) -> InstrumentRuntime:
    """Return the stored overlay, or synthesize FLAT/primary fields."""
    for item in snapshot.instrument_runtimes:
        if item.product_id == product_id:
            return item
    if product_id == snapshot.deployment.product_id and not snapshot.instrument_runtimes:
        return runtime_from_deployment(snapshot.deployment, product_id)
    return InstrumentRuntime(product_id=product_id, phase=RuntimePhase.FLAT)


def _ensure_runtime(
    snapshot: DeploymentSnapshot, runtime: InstrumentRuntime
) -> tuple[InstrumentRuntime, ...]:
    """Keep the focused runtime in the snapshot's overlay list."""
    others = tuple(
        item for item in snapshot.instrument_runtimes if item.product_id != runtime.product_id
    )
    return (*others, runtime)


def _position_for(snapshot: DeploymentSnapshot, product_id: str) -> Position | None:
    """Return the open book for one product, if any."""
    for position in snapshot_positions(snapshot):
        if resolved_product_id(position.product_id, snapshot.deployment) == product_id:
            return position
    return None


def _orders_for(snapshot: DeploymentSnapshot, product_id: str) -> tuple[Order, ...]:
    """Filter venue orders to one product, treating blank ids as the primary product."""
    return tuple(
        order
        for order in snapshot.orders
        if resolved_product_id(order.product_id, snapshot.deployment) == product_id
    )


def _intents_for(snapshot: DeploymentSnapshot, product_id: str) -> tuple[OrderIntent, ...]:
    """Filter intents to one product, treating blank ids as the primary product."""
    return tuple(
        intent
        for intent in snapshot.intents
        if resolved_product_id(intent.product_id, snapshot.deployment) == product_id
    )


def _fills_for(snapshot: DeploymentSnapshot, product_id: str) -> tuple[Fill, ...]:
    """Keep fills whose order belongs to the focused product."""
    order_ids = {order.id for order in _orders_for(snapshot, product_id)}
    return tuple(fill for fill in snapshot.fills if fill.order_id in order_ids)


class InstrumentScopedStore:
    """Route loop persistence through one product while sharing quote cash."""

    def __init__(self, inner: ExecutionStore, product_id: str) -> None:
        """Bind the wrapper to one Coinbase USD spot product."""
        self._inner = inner
        self._product_id = product_id

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Insert one new deployment row."""
        return await self._inner.create_deployment(deployment)

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Load the deployment and overlay this product's runtime fields."""
        snapshot = await self._inner.get_deployment(deployment_id)
        return overlay_snapshot(snapshot, self._product_id)

    async def list_deployments(self) -> tuple[Deployment, ...]:
        """Return every deployment, newest-updated first."""
        return await self._inner.list_deployments()

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return deployments for one strategy identity, newest-updated first."""
        return await self._inner.list_by_strategy(strategy_id)

    async def save_deployment(self, deployment: Deployment) -> Deployment:
        """Persist this product's runtime overlay and shared cash/status."""
        current = await self._inner.get_deployment(deployment.id)
        runtime = runtime_from_deployment(deployment, self._product_id)
        await self._inner.save_instrument_runtime(runtime, deployment_id=deployment.id)
        runtimes = [
            runtime if item.product_id == self._product_id else item
            for item in current.instrument_runtimes
        ]
        if not any(item.product_id == self._product_id for item in current.instrument_runtimes):
            runtimes.append(runtime)
        parent = replace(
            current.deployment,
            cash=deployment.cash,
            status=deployment.status,
            mismatch_detail=deployment.mismatch_detail,
            last_signal=deployment.last_signal,
            phase=aggregate_phase(tuple(runtimes)),
            updated_at=deployment.updated_at,
        )
        await self._inner.save_deployment(parent)
        return deployment

    async def save_intent(self, intent: OrderIntent) -> OrderIntent:
        """Stamp this product id onto one intent before insert."""
        stamped = intent if intent.product_id else replace(intent, product_id=self._product_id)
        return await self._inner.save_intent(stamped)

    async def save_order(self, order: Order) -> Order:
        """Stamp this product id onto one order before upsert."""
        stamped = order if order.product_id else replace(order, product_id=self._product_id)
        return await self._inner.save_order(stamped)

    async def save_fill(self, fill: Fill) -> Fill:
        """Insert one fill, ignoring exact venue-fill duplicates."""
        return await self._inner.save_fill(fill)

    async def save_position(
        self,
        position: Position | None,
        *,
        deployment_id: UUID,
        product_id: str | None = None,
    ) -> None:
        """Replace or clear this product's book only.

        The overlay ignores ``product_id`` and always writes this wrapper's
        Coinbase USD spot product.
        """
        del product_id
        stamped = None
        if position is not None:
            stamped = (
                position if position.product_id else replace(position, product_id=self._product_id)
            )
        await self._inner.save_position(
            stamped, deployment_id=deployment_id, product_id=self._product_id
        )

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return open or unknown orders for this product."""
        snapshot = await self.get_deployment(deployment_id)
        watch = {OrderStatus.OPEN, OrderStatus.UNKNOWN, OrderStatus.PENDING}
        return tuple(order for order in snapshot.orders if order.status in watch)

    async def get_intent_by_idempotency_key(self, idempotency_key: str) -> OrderIntent | None:
        """Return the intent recorded under one client idempotency key, if any."""
        return await self._inner.get_intent_by_idempotency_key(idempotency_key)

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Replace one product overlay row."""
        await self._inner.save_instrument_runtime(runtime, deployment_id=deployment_id)
