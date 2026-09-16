"""Fenced worker leases and revision-checked deployment writes."""

from __future__ import annotations

from datetime import timedelta
import os
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.execution.ids import utc_now
from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    Order,
)

if TYPE_CHECKING:
    from thytrader.execution.store import ExecutionStore

WORKER_LEASE_TTL_SECONDS = 45


def worker_lease_holder() -> str:
    """Return a stable holder identity for this execution-worker process."""
    return f"execution-worker:{os.getpid()}"


class RevisionFencedStore:
    """Proxy that stamps ``expected_revision`` on every deployment write.

    Network I/O stays on the caller; this wrapper only sequences optimistic
    writes after a short lease UPDATE.
    """

    def __init__(self, inner: ExecutionStore, deployment_id: UUID, expected_revision: int) -> None:
        """Bind the inner store to one leased deployment and its loaded revision."""
        self._inner = inner
        self._deployment_id = deployment_id
        self._expected_revision = expected_revision

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
        apply = self._inner.apply_fill_transaction
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

    def __getattr__(self, name: str) -> object:
        """Delegate remaining store methods to the inner repository."""
        return getattr(self._inner, name)


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
