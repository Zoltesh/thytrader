"""In-memory execution store for tests and database-free API doubles."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.ids import utc_now
from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    FillApplication,
    FillApplicationResult,
    InstrumentRuntime,
    Order,
    OrderIntent,
    OrderStatus,
    Position,
    resolved_product_id,
)

if TYPE_CHECKING:
    from uuid import UUID


def _position_key(deployment_id: UUID, product_id: str) -> tuple[UUID, str]:
    """Return the in-memory key for one product book."""
    return (deployment_id, product_id)


class InMemoryExecutionStore:
    """Retain execution records in process memory."""

    def __init__(self) -> None:
        """Start with no deployments."""
        self.deployments: dict[UUID, Deployment] = {}
        self.intents: dict[UUID, OrderIntent] = {}
        self.orders: dict[UUID, Order] = {}
        self.fills: dict[UUID, Fill] = {}
        self.positions: dict[tuple[UUID, str], Position] = {}
        self.instrument_runtimes: dict[tuple[UUID, str], InstrumentRuntime] = {}
        self._fill_keys: set[tuple[UUID, str]] = set()
        # Separate from ``_fill_keys`` (F01): a fill recorded via ``save_fill`` (the
        # paper immediate-fill path) is evidence only, not yet an applied economic
        # effect. Mirrors Postgres's ``execution_fills.applied_at IS NULL``.
        self._applied_fill_keys: set[tuple[UUID, str]] = set()

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Insert one new deployment row."""
        self.deployments[deployment.id] = deployment
        return deployment

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Load one deployment with its related records or fail."""
        deployment = self.deployments.get(deployment_id)
        if deployment is None:
            raise ExecutionStoreError("Deployment was not found.")
        positions = tuple(
            position
            for (stored_id, _product), position in self.positions.items()
            if stored_id == deployment_id
        )
        runtimes = tuple(
            runtime
            for (stored_id, _product), runtime in self.instrument_runtimes.items()
            if stored_id == deployment_id
        )
        focused = _focused_position(positions, deployment.product_id)
        return DeploymentSnapshot(
            deployment=deployment,
            position=focused,
            orders=tuple(
                order for order in self.orders.values() if order.deployment_id == deployment_id
            ),
            fills=tuple(
                fill for fill in self.fills.values() if fill.deployment_id == deployment_id
            ),
            intents=tuple(
                intent for intent in self.intents.values() if intent.deployment_id == deployment_id
            ),
            positions=positions,
            instrument_runtimes=runtimes,
        )

    async def list_deployments(self) -> tuple[Deployment, ...]:
        """Return every deployment, newest-updated first."""
        return tuple(
            sorted(self.deployments.values(), key=lambda item: item.updated_at, reverse=True)
        )

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return deployments for one strategy identity, newest-updated first."""
        matching = [
            item
            for item in self.deployments.values()
            if item.strategy_id is not None and str(item.strategy_id) == strategy_id
        ]
        return tuple(sorted(matching, key=lambda item: item.updated_at, reverse=True))

    async def save_deployment(self, deployment: Deployment) -> Deployment:
        """Replace mutable runtime fields for one existing deployment."""
        if deployment.id not in self.deployments:
            raise ExecutionStoreError("Deployment was not found.")
        self.deployments[deployment.id] = deployment
        return deployment

    async def save_intent(self, intent: OrderIntent) -> OrderIntent:
        """Insert one order intent before venue submission."""
        if intent.idempotency_key is not None:
            existing = await self.get_intent_by_idempotency_key(intent.idempotency_key)
            if existing is not None:
                raise ExecutionConflictError("idempotency_key already used")
        self.intents[intent.id] = intent
        return intent

    async def save_order(self, order: Order) -> Order:
        """Insert or replace one venue-visible order snapshot."""
        existing = next(
            (
                item
                for item in self.orders.values()
                if item.client_order_id == order.client_order_id
            ),
            None,
        )
        if existing is not None:
            self.orders.pop(existing.id, None)
        self.orders[order.id] = order
        return order

    async def save_fill(self, fill: Fill) -> Fill:
        """Insert one fill, ignoring exact venue-fill duplicates."""
        key = (fill.deployment_id, fill.venue_fill_id)
        if key in self._fill_keys:
            return fill
        self._fill_keys.add(key)
        self.fills[fill.id] = fill
        return fill

    async def apply_fill_effect(self, application: FillApplication) -> FillApplicationResult:
        """Insert, apply, and mark one fill applied; idempotent on a repeated venue fill id.

        In-process memory has no partial-commit window, so recording and applying
        happen together here exactly as the durable stores must. A fill already
        recorded as evidence only (via ``save_fill``, e.g. the paper immediate-fill
        path) is not yet applied: this call still applies it the first time.
        """
        fill = application.fill
        key = (fill.deployment_id, fill.venue_fill_id)
        if key in self._applied_fill_keys:
            return FillApplicationResult(applied=False)
        self._applied_fill_keys.add(key)
        self._fill_keys.add(key)
        stamped = replace(fill, applied_at=utc_now())
        stale_ids = [
            existing_id
            for existing_id, existing in self.fills.items()
            if existing.deployment_id == fill.deployment_id
            and existing.venue_fill_id == fill.venue_fill_id
            and existing_id != stamped.id
        ]
        for stale_id in stale_ids:
            self.fills.pop(stale_id, None)
        self.fills[stamped.id] = stamped
        await self.save_order(application.order)
        key_product = resolved_product_id(application.order.product_id, application.deployment)
        await self.save_position(
            None if application.clear_position else application.position,
            deployment_id=application.deployment.id,
            product_id=key_product,
        )
        self.deployments[application.deployment.id] = application.deployment
        return FillApplicationResult(applied=True)

    async def save_position(
        self,
        position: Position | None,
        *,
        deployment_id: UUID,
        product_id: str | None = None,
    ) -> None:
        """Replace or clear one product book, or every book when the product is omitted."""
        if position is None and product_id is None:
            for key in [item for item in self.positions if item[0] == deployment_id]:
                self.positions.pop(key, None)
            return
        key_product = product_id or (position.product_id if position is not None else "")
        key = _position_key(deployment_id, key_product)
        if position is None:
            self.positions.pop(key, None)
            return
        stamped = position if position.product_id else replace(position, product_id=key_product)
        self.positions[_position_key(deployment_id, stamped.product_id)] = stamped

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Replace one product overlay row."""
        self.instrument_runtimes[_position_key(deployment_id, runtime.product_id)] = runtime

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return open or unknown orders that the runtime must observe."""
        watch = {OrderStatus.OPEN, OrderStatus.UNKNOWN, OrderStatus.PENDING}
        return tuple(
            order
            for order in self.orders.values()
            if order.deployment_id == deployment_id and order.status in watch
        )

    async def get_intent_by_idempotency_key(self, idempotency_key: str) -> OrderIntent | None:
        """Return the intent recorded under one client idempotency key, if any."""
        return next(
            (
                intent
                for intent in self.intents.values()
                if intent.idempotency_key == idempotency_key
            ),
            None,
        )


def _focused_position(positions: tuple[Position, ...], primary_product_id: str) -> Position | None:
    """Return the primary book, or the sole book, for legacy snapshot.position readers."""
    if not positions:
        return None
    if len(positions) == 1:
        return positions[0]
    for position in positions:
        if position.product_id in {"", primary_product_id}:
            return position
    return None
