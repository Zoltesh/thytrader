"""Durable execution-record contracts used by the API and runtime worker."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    DeploymentSummarySnapshot,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    Order,
    OrderIntent,
    PaginatedFills,
    PaginatedOrders,
    Position,
)

if TYPE_CHECKING:
    from datetime import datetime, timedelta
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

    async def get_deployment_summary(self, deployment_id: UUID) -> DeploymentSummarySnapshot:
        """Load positions and overlays without historical orders or fills."""
        ...

    async def list_deployments(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[Deployment, ...]:
        """Return deployments newest-updated first, optionally paginated."""
        ...

    async def list_fills(
        self, deployment_id: UUID, *, limit: int, cursor: str | None = None
    ) -> PaginatedFills:
        """Return one descending page of fills for one deployment."""
        ...

    async def list_orders(
        self, deployment_id: UUID, *, limit: int, cursor: str | None = None
    ) -> PaginatedOrders:
        """Return one descending page of orders for one deployment."""
        ...

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return deployments for one strategy identity, newest-updated first."""
        ...

    async def save_deployment(
        self, deployment: Deployment, *, expected_revision: int | None = None
    ) -> Deployment:
        """Replace mutable runtime fields for one existing deployment.

        When ``expected_revision`` is set, reject the write if the stored
        revision does not match so a stale RUNNING snapshot cannot overwrite
        a pause or stop.
        """
        ...

    async def acquire_worker_lease(
        self,
        deployment_id: UUID,
        *,
        holder: str,
        now: datetime,
        ttl: timedelta,
    ) -> Deployment | None:
        """Acquire or renew a fenced worker lease. None means another holder is current."""
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

    async def save_position(
        self,
        position: Position | None,
        *,
        deployment_id: UUID,
        product_id: str | None = None,
    ) -> None:
        """Replace or clear one product book for a deployment."""
        ...

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Replace one product overlay row for a deployment."""
        ...

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return open or unknown orders that the runtime must observe."""
        ...

    async def get_intent_by_idempotency_key(self, idempotency_key: str) -> OrderIntent | None:
        """Return the intent recorded under one client idempotency key, if any."""
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

    async def get_deployment_summary(self, deployment_id: UUID) -> DeploymentSummarySnapshot:
        """Refuse deployment summary reads without durable storage."""
        del deployment_id
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def list_deployments(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[Deployment, ...]:
        """Return no deployments when storage is unconfigured."""
        del limit, offset
        return ()

    async def list_fills(
        self, deployment_id: UUID, *, limit: int, cursor: str | None = None
    ) -> PaginatedFills:
        """Refuse fill pagination without durable storage."""
        del deployment_id, limit, cursor
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def list_orders(
        self, deployment_id: UUID, *, limit: int, cursor: str | None = None
    ) -> PaginatedOrders:
        """Refuse order pagination without durable storage."""
        del deployment_id, limit, cursor
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return no deployments when storage is unconfigured."""
        del strategy_id
        return ()

    async def save_deployment(
        self, deployment: Deployment, *, expected_revision: int | None = None
    ) -> Deployment:
        """Refuse runtime writes without durable storage."""
        del deployment, expected_revision
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def acquire_worker_lease(
        self,
        deployment_id: UUID,
        *,
        holder: str,
        now: datetime,
        ttl: timedelta,
    ) -> Deployment | None:
        """Refuse lease acquisition without durable storage."""
        del deployment_id, holder, now, ttl
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

    async def save_position(
        self,
        position: Position | None,
        *,
        deployment_id: UUID,
        product_id: str | None = None,
    ) -> None:
        """Refuse position writes without durable storage."""
        del position, deployment_id, product_id
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Refuse overlay writes without durable storage."""
        del runtime, deployment_id
        raise ExecutionStoreError("Execution storage is unavailable.")

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return no open orders when storage is unconfigured."""
        del deployment_id
        return ()

    async def get_intent_by_idempotency_key(self, idempotency_key: str) -> OrderIntent | None:
        """Return no intent when storage is unconfigured."""
        del idempotency_key
        return None
