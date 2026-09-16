"""In-memory execution store for tests and database-free API doubles."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID  # noqa: TC003

from thytrader.execution.fill_ledger import project_fill_economics
from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    Order,
    OrderIntent,
    OrderStatus,
    Position,
)


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
            return next(
                (
                    item
                    for item in self.fills.values()
                    if item.deployment_id == fill.deployment_id
                    and item.venue_fill_id == fill.venue_fill_id
                ),
                fill,
            )
        self._fill_keys.add(key)
        self.fills[fill.id] = fill
        if fill.economics_applied_at is not None:
            self._applied_fill_keys.add(key)
        return fill

    async def apply_fill_transaction(
        self,
        deployment_id: UUID,
        *,
        fill: Fill,
        order: Order,
        cooldown_bars: int = 0,
        timeframe: str | None = None,
    ) -> tuple[bool, DeploymentSnapshot]:
        """Insert fill evidence and apply economics in one in-memory step."""
        snapshot = await self.get_deployment(deployment_id)
        key = (fill.deployment_id, fill.venue_fill_id)
        existing = next(
            (
                item
                for item in snapshot.fills
                if item.deployment_id == fill.deployment_id
                and item.venue_fill_id == fill.venue_fill_id
            ),
            None,
        )
        if existing is not None and existing.economics_applied_at is not None:
            return False, snapshot
        if key not in self._fill_keys:
            self._fill_keys.add(key)
            self.fills[fill.id] = fill
        projected, stamped = project_fill_economics(
            snapshot,
            fill=existing or fill,
            order=order,
            cooldown_bars=cooldown_bars,
            timeframe=timeframe,
        )
        self.fills[stamped.id] = stamped
        self._applied_fill_keys.add(key)
        self.deployments[projected.deployment.id] = projected.deployment
        product_id = order.product_id or projected.deployment.product_id
        if projected.position is not None:
            self.positions[_position_key(deployment_id, product_id)] = projected.position
        elif projected.deployment.phase.value == "flat":
            self.positions.pop(_position_key(deployment_id, product_id), None)
        return True, await self.get_deployment(deployment_id)

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
