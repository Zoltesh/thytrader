"""PostgreSQL lease check inside an order intent's insert transaction (ADR 0153)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from thytrader.persistence.schema import deployments
from thytrader.trading.lease_fence import fenced_holder, require_lease_holder

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.trading.models import OrderIntent


async def require_intent_lease(connection: AsyncConnection, intent: OrderIntent) -> None:
    """Refuse a fenced intent whose book another worker now leases.

    ``FOR SHARE`` waits out a concurrent lease UPDATE, so the holder read here is the one the
    insert commits under; an acquisition after the commit sees this intent in its snapshot.
    """
    if fenced_holder(intent.deployment_id) is None:
        return
    holder = await connection.scalar(
        select(deployments.c.worker_lease_holder)
        .where(deployments.c.id == intent.deployment_id)
        .with_for_update(read=True)
    )
    require_lease_holder(intent.deployment_id, holder)


async def release_worker_leases(engine: AsyncEngine, *, holder: str, now: datetime) -> None:
    """Free every lease this worker holds once it has stopped, so a successor need not wait.

    Holders are unique per process, so without a release a replacement worker waits out each
    lease's TTL before it may maintain the book. Only a cleanly stopped worker calls this: no
    pass of this process can write an intent afterwards.
    """
    async with engine.begin() as connection:
        await connection.execute(
            deployments.update()
            .where(deployments.c.worker_lease_holder == holder)
            .where(deployments.c.worker_lease_expires_at > now)
            .values(worker_lease_holder=None, worker_lease_expires_at=now)
        )
