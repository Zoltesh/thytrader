"""Fenced worker leases and revision-checked deployment writes."""

from __future__ import annotations

from datetime import timedelta
import os
from typing import TYPE_CHECKING

from thytrader.execution.ids import utc_now
from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    Order,
    OrderIntent,
    Position,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.execution.store import ExecutionStore

WORKER_LEASE_TTL_SECONDS = 45


def worker_lease_holder() -> str:
    """Return a stable holder identity for this execution-worker process."""
    return f"execution-worker:{os.getpid()}"


class RevisionFencedStore:
    """Proxy that stamps ``expected_revision`` on every deployment write.

    Network I/O stays on the caller; this wrapper only sequences optimistic
    writes after a short lease UPDATE. Explicit ``ExecutionStore`` methods keep
    the proxy structurally typed; ``apply_fill_transaction`` stays getattr
    because that method is not on the protocol.
    """

    def __init__(self, inner: ExecutionStore, deployment_id: UUID, expected_revision: int) -> None:
        """Bind the inner store to one leased deployment and its loaded revision."""
        self._inner = inner
        self._deployment_id = deployment_id
        self._expected_revision = expected_revision

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Insert one new deployment row on the inner store."""
        return await self._inner.create_deployment(deployment)

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Load one deployment with its related records or fail."""
        return await self._inner.get_deployment(deployment_id)

    async def list_deployments(self) -> tuple[Deployment, ...]:
        """Return every deployment, newest-updated first."""
        return await self._inner.list_deployments()

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return deployments for one strategy identity, newest-updated first."""
        return await self._inner.list_by_strategy(strategy_id)

    async def save_deployment(
        self, deployment: Deployment, *, expected_revision: int | None = None
    ) -> Deployment:
        """Replace runtime fields only when the fenced revision still matches.

        Sibling books (for example a daily-loss pause of the rest of the mode)
        pass through without this book's revision fence.
        """
        if deployment.id != self._deployment_id:
            return await self._inner.save_deployment(
                deployment, expected_revision=expected_revision
            )
        expected = self._expected_revision if expected_revision is None else expected_revision
        saved = await self._inner.save_deployment(deployment, expected_revision=expected)
        self._expected_revision = saved.revision
        return saved

    async def acquire_worker_lease(
        self,
        deployment_id: UUID,
        *,
        holder: str,
        now: datetime,
        ttl: timedelta,
    ) -> Deployment | None:
        """Acquire or renew a fenced worker lease on the inner store."""
        return await self._inner.acquire_worker_lease(
            deployment_id, holder=holder, now=now, ttl=ttl
        )

    async def save_intent(self, intent: OrderIntent) -> OrderIntent:
        """Insert one order intent before venue submission."""
        return await self._inner.save_intent(intent)

    async def save_order(self, order: Order) -> Order:
        """Insert or replace one venue-visible order snapshot."""
        return await self._inner.save_order(order)

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
        """Replace or clear one product book for a deployment."""
        await self._inner.save_position(
            position, deployment_id=deployment_id, product_id=product_id
        )

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Replace one product overlay row for a deployment."""
        await self._inner.save_instrument_runtime(runtime, deployment_id=deployment_id)

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return open or unknown orders that the runtime must observe."""
        return await self._inner.list_open_orders(deployment_id)

    async def get_intent_by_idempotency_key(self, idempotency_key: str) -> OrderIntent | None:
        """Return the intent recorded under one client idempotency key, if any."""
        return await self._inner.get_intent_by_idempotency_key(idempotency_key)

    async def apply_fill_transaction(
        self,
        deployment_id: UUID,
        *,
        fill: Fill,
        order: Order,
        cooldown_bars: int = 0,
        timeframe: str | None = None,
    ) -> tuple[bool, DeploymentSnapshot]:
        """Apply fill economics then refresh the fenced revision from the snapshot."""
        apply = getattr(self._inner, "apply_fill_transaction", None)
        if not callable(apply):
            raise ExecutionStoreError("Execution storage cannot apply fill transactions.")
        applied, snapshot = await apply(
            deployment_id,
            fill=fill,
            order=order,
            cooldown_bars=cooldown_bars,
            timeframe=timeframe,
        )
        if deployment_id == self._deployment_id:
            self._expected_revision = snapshot.deployment.revision
        return applied, snapshot


async def acquire_worker_lease(
    store: ExecutionStore,
    deployment_id: UUID,
    *,
    holder: str | None = None,
    ttl_seconds: int = WORKER_LEASE_TTL_SECONDS,
) -> Deployment | None:
    """Acquire or renew a fenced lease. None means another worker still holds it."""
    acquire = getattr(store, "acquire_worker_lease", None)
    if acquire is None:
        snapshot = await store.get_deployment(deployment_id)
        return snapshot.deployment
    now = utc_now()
    return await acquire(
        deployment_id,
        holder=holder or worker_lease_holder(),
        now=now,
        ttl=timedelta(seconds=ttl_seconds),
    )


def require_revision(deployment: Deployment, expected: int) -> None:
    """Raise when a stale writer would overwrite a newer snapshot."""
    if deployment.revision != expected:
        raise ExecutionConflictError("Deployment revision conflict.")


def lease_write_error(error: Exception) -> bool:
    """True when a write failed because of a lease or revision fence."""
    return isinstance(error, ExecutionConflictError | ExecutionStoreError) and (
        "revision" in str(error).lower() or "lease" in str(error).lower()
    )
