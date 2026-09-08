"""In-memory execution store for tests and database-free API doubles."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    ExecutionStoreError,
    Fill,
    Order,
    OrderIntent,
    OrderStatus,
    Position,
)

if TYPE_CHECKING:
    from uuid import UUID


class InMemoryExecutionStore:
    """Retain execution records in process memory."""

    def __init__(self) -> None:
        """Start with no deployments."""
        self.deployments: dict[UUID, Deployment] = {}
        self.intents: dict[UUID, OrderIntent] = {}
        self.orders: dict[UUID, Order] = {}
        self.fills: dict[UUID, Fill] = {}
        self.positions: dict[UUID, Position] = {}
        self._fill_keys: set[tuple[UUID, str]] = set()

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Insert one new deployment row."""
        self.deployments[deployment.id] = deployment
        return deployment

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Load one deployment with its related records or fail."""
        deployment = self.deployments.get(deployment_id)
        if deployment is None:
            raise ExecutionStoreError("Deployment was not found.")
        return DeploymentSnapshot(
            deployment=deployment,
            position=self.positions.get(deployment_id),
            orders=tuple(
                order for order in self.orders.values() if order.deployment_id == deployment_id
            ),
            fills=tuple(
                fill for fill in self.fills.values() if fill.deployment_id == deployment_id
            ),
            intents=tuple(
                intent for intent in self.intents.values() if intent.deployment_id == deployment_id
            ),
        )

    async def list_deployments(self) -> tuple[Deployment, ...]:
        """Return every deployment, newest-updated first."""
        return tuple(
            sorted(self.deployments.values(), key=lambda item: item.updated_at, reverse=True)
        )

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return deployments for one strategy identity, newest-updated first."""
        matching = [
            item for item in self.deployments.values() if str(item.strategy_id) == strategy_id
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

    async def save_position(self, position: Position | None, *, deployment_id: UUID) -> None:
        """Replace or clear the single position for one deployment."""
        if position is None:
            self.positions.pop(deployment_id, None)
            return
        self.positions[deployment_id] = position

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return open or unknown orders that the runtime must observe."""
        watch = {OrderStatus.OPEN, OrderStatus.UNKNOWN, OrderStatus.PENDING}
        return tuple(
            order
            for order in self.orders.values()
            if order.deployment_id == deployment_id and order.status in watch
        )
