"""Durable execution-record contracts used by the API and runtime worker."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    ExecutionStoreError,
    Fill,
    Order,
    OrderIntent,
    Position,
)

if TYPE_CHECKING:
    from uuid import UUID


@runtime_checkable
class ExecutionStore(Protocol):
    """Persist deployments, intents, orders, fills, and the single position."""

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Insert one new deployment row."""
        ...

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Load one deployment with its related records or fail."""
        ...

    async def list_deployments(self) -> tuple[Deployment, ...]:
        """Return every deployment, newest-updated first."""
        ...

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return deployments for one strategy identity, newest-updated first."""
        ...

    async def save_deployment(self, deployment: Deployment) -> Deployment:
        """Replace mutable runtime fields for one existing deployment."""
        ...

    async def save_intent(self, intent: OrderIntent) -> OrderIntent:
        """Insert one order intent before venue submission."""
        ...

    async def save_order(self, order: Order) -> Order:
        """Insert or replace one venue-visible order snapshot."""
        ...

    async def save_fill(self, fill: Fill) -> Fill:
        """Insert one fill, ignoring exact venue-fill duplicates."""
        ...

    async def save_position(self, position: Position | None, *, deployment_id: UUID) -> None:
        """Replace or clear the single position for one deployment."""
        ...

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return open or unknown orders that the runtime must observe."""
        ...


class DisabledExecutionStore:
    """Fail closed when execution storage is not configured."""

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Refuse deployment creation without durable storage."""
        del deployment
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Refuse deployment reads without durable storage."""
        del deployment_id
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def list_deployments(self) -> tuple[Deployment, ...]:
        """Return no deployments when storage is unconfigured."""
        return ()

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return no deployments when storage is unconfigured."""
        del strategy_id
        return ()

    async def save_deployment(self, deployment: Deployment) -> Deployment:
        """Refuse runtime writes without durable storage."""
        del deployment
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def save_intent(self, intent: OrderIntent) -> OrderIntent:
        """Refuse intent writes without durable storage."""
        del intent
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def save_order(self, order: Order) -> Order:
        """Refuse order writes without durable storage."""
        del order
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def save_fill(self, fill: Fill) -> Fill:
        """Refuse fill writes without durable storage."""
        del fill
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def save_position(self, position: Position | None, *, deployment_id: UUID) -> None:
        """Refuse position writes without durable storage."""
        del position, deployment_id
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return no open orders when storage is unconfigured."""
        del deployment_id
        return ()
